// The sync engine: scan the project against the baseline, push idempotent
// batches, pull and apply remote changes with conflict preservation,
// and move the event library to/from the coordinator.
//
// Invariants this module maintains (each maps to a task-book requirement):
// - Provision-on-scan: local changes update the baseline immediately and
//   land in a pending batch BEFORE any network traffic, so a kill at any
//   moment loses nothing and a retry replays the same batch_id (idempotent).
// - A batch that was durably committed but whose ack was dropped is only
//   ever retried under the SAME batch_id, so the server records the change
//   exactly once ("the same confirmed change produces one logical result").
// - Applying a remote change checks whether the local copy has moved away
//   from the shared baseline (pending for that path, or disk hash differs
//   from baseline). If so, both sides are preserved on disk and a conflict
//   record is written; otherwise the change applies directly.
use crate::httpc::{self, HttpError};
use crate::store::{self, FileEntry, PendingBatch, Snapshot, StateStore};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

pub const TIMEOUT: Duration = Duration::from_secs(10);

pub struct Engine {
    pub snap: Snapshot,
    pub store: StateStore,
    pub project: PathBuf,
    pub client_id: String,
    pub server_url: String,
    pub last_error: Option<String>,
}

pub struct SyncReport {
    pub pushed: i64,
    pub pulled: i64,
    pub conflicts: i64,
    pub pending_push: i64,
    pub events_pushed: i64,
    pub events_pulled: i64,
    pub receipt_lost: bool,
    pub offline: bool,
}

impl SyncReport {
    pub fn to_json(&self) -> Value {
        json!({
            "pushed": self.pushed,
            "pulled": self.pulled,
            "conflicts": self.conflicts,
            "pending_push": self.pending_push,
            "events_pushed": self.events_pushed,
            "events_pulled": self.events_pulled,
            "receipt_lost": self.receipt_lost,
            "offline": self.offline,
        })
    }
}

impl Engine {
    pub fn open(state_dir: &Path, client_id: &str, server_url: &str, project_dir: &Path) -> std::io::Result<Self> {
        let store = StateStore::new(state_dir)?;
        let snap = store.load(client_id, server_url, &project_dir.to_string_lossy());
        fs::create_dir_all(project_dir)?;
        Ok(Engine {
            snap,
            store,
            project: project_dir.to_path_buf(),
            client_id: client_id.to_string(),
            server_url: server_url.to_string(),
            last_error: None,
        })
    }

    pub fn save(&self) {
        if let Err(e) = self.store.save(&self.snap) {
            eprintln!("FATAL: cannot save snapshot: {}", e);
            std::process::exit(3);
        }
    }

    fn norm(rel: &Path) -> String {
        rel.to_string_lossy().replace('\\', "/")
    }

    // ---------------- scanning ----------------

    /// Read every file in the project (recursive), returning path (with /)
    /// -> bytes. Empty directories are irrelevant; conflict markers and
    /// tombstone JSON files are ordinary files and propagate like any other.
    fn scan_disk(&self) -> std::io::Result<BTreeMap<String, Vec<u8>>> {
        let mut out = BTreeMap::new();
        walk_files(&self.project, &self.project, &mut out)?;
        Ok(out)
    }

    /// Diff disk against the baseline; pair disappear/appear with equal
    /// hashes into renames. Returns a list of change objects (serde values).
    pub fn scan_changes(&self) -> std::io::Result<Vec<Value>> {
        let disk = self.scan_disk()?;
        let mut hashes: BTreeMap<String, String> = BTreeMap::new();
        let mut contents: BTreeMap<String, Vec<u8>> = BTreeMap::new();
        for (p, data) in &disk {
            hashes.insert(p.clone(), store::sha256_hex(data));
            contents.insert(p.clone(), data.clone());
        }
        let mut deletes: Vec<String> = Vec::new();
        let mut upserts: Vec<String> = Vec::new();
        for (p, entry) in &self.snap.files {
            match hashes.get(p) {
                Some(h) if h == &entry.hash => {} // unchanged
                Some(_) => upserts.push(p.clone()), // modified (or replaced)
                None => deletes.push(p.clone()),    // gone from disk
            }
        }
        for p in hashes.keys() {
            if !self.snap.files.contains_key(p) {
                upserts.push(p.clone()); // new file
            }
        }
        // Pair renames: a delete whose content reappears unchanged at a new path.
        let mut used_upserts: BTreeSet<usize> = BTreeSet::new();
        let mut changes: Vec<Value> = Vec::new();
        'outer: for d in &deletes {
            let base = self.snap.files.get(d).map(|e| e.hash.clone()).unwrap_or_default();
            for (i, u) in upserts.iter().enumerate() {
                if used_upserts.contains(&i) {
                    continue;
                }
                if hashes.get(u) == Some(&base) && !self.snap.files.contains_key(u) {
                    used_upserts.insert(i);
                    changes.push(json!({
                        "op": "rename",
                        "from": d,
                        "path": u,
                        "hash": base,
                        "size": contents.get(u).map(|c| c.len()).unwrap_or(0),
                        "base_hash": base,
                    }));
                    continue 'outer;
                }
            }
            changes.push(json!({
                "op": "delete",
                "path": d,
                "base_hash": base,
            }));
        }
        for (i, u) in upserts.iter().enumerate() {
            if used_upserts.contains(&i) {
                continue;
            }
            let h = hashes.get(u).cloned().unwrap_or_default();
            let base = self.snap.files.get(u).map(|e| e.hash.clone()).unwrap_or_default();
            changes.push(json!({
                "op": "upsert",
                "path": u,
                "hash": h,
                "size": contents.get(u).map(|c| c.len()).unwrap_or(0),
                "base_hash": base,
            }));
        }
        Ok(changes)
    }

    fn disk_bytes(&self, path: &str) -> Option<Vec<u8>> {
        let p = self.project.join(path.replace('/', "\\"));
        fs::read(p).ok()
    }

    fn write_project_file(&self, path: &str, data: &[u8]) -> std::io::Result<()> {
        let p = self.project.join(path.replace('/', "\\"));
        if let Some(parent) = p.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(p, data)
    }

    fn remove_project_file(&self, path: &str) {
        let p = self.project.join(path.replace('/', "\\"));
        let _ = fs::remove_file(p);
        // Prune now-empty parent dirs so renames do not leave shells behind.
        let mut parent = Path::new(path).parent().map(|x| x.to_path_buf());
        while let Some(rel) = parent {
            if rel.as_os_str().is_empty() {
                break;
            }
            let abs = self.project.join(&rel);
            if fs::remove_dir(&abs).is_err() {
                break;
            }
            parent = rel.parent().map(|x| x.to_path_buf());
        }
    }

    // ---------------- push ----------------

    /// Push all pending batches (older first), then provision any newly
    /// scanned changes into a fresh batch and push it.
    /// Returns (pushed_change_count, receipt_lost_flag).
    fn push_all(&mut self) -> Result<(i64, bool), String> {
        let mut pushed = 0i64;
        let mut receipt_lost = false;
        let batch_ids: Vec<String> = self.snap.pending_batches.keys().cloned().collect();
        for id in batch_ids {
            match self.commit_batch(&id) {
                Ok(n) => pushed += n,
                Err(PushError::ReceiptLost(msg)) => {
                    self.last_error = Some(msg);
                    receipt_lost = true;
                    break; // stop pushing; pending stays for the retry
                }
                Err(PushError::Unreachable(msg)) => {
                    self.last_error = Some(msg);
                    break; // offline; pending stays
                }
                Err(PushError::Server(msg)) => {
                    self.last_error = Some(format!("server rejected batch {}: {}", id, msg));
                    break;
                }
            }
        }
        // Provision newly scanned local changes into a new batch and push.
        if !receipt_lost && self.last_error.is_none() {
            match self.scan_changes() {
                Ok(changes) if !changes.is_empty() => {
                    let id = store::new_batch_id(&self.client_id);
                    self.provision(&changes, &id);
                    self.save();
                    match self.commit_batch(&id) {
                        Ok(n) => pushed += n,
                        Err(PushError::ReceiptLost(msg)) => {
                            self.last_error = Some(msg);
                            receipt_lost = true;
                        }
                        Err(PushError::Unreachable(msg)) => self.last_error = Some(msg),
                        Err(PushError::Server(msg)) => {
                            self.last_error = Some(format!("server rejected batch {}: {}", id, msg))
                        }
                    }
                }
                Ok(_) => {}
                Err(e) => self.last_error = Some(format!("scan failed: {}", e)),
            }
        }
        Ok((pushed, receipt_lost))
    }

    /// Record local changes in the baseline and pending queue (durable,
    /// before any network I/O). base_hash carries the pre-change baseline
    /// so the receiving side can detect concurrency.
    fn provision(&mut self, changes: &[Value], batch_id: &str) {
        for c in changes {
            let op = c["op"].as_str().unwrap_or("");
            match op {
                "upsert" => {
                    let path = c["path"].as_str().unwrap_or("").to_string();
                    let hash = c["hash"].as_str().unwrap_or("").to_string();
                    let size = c["size"].as_u64().unwrap_or(0);
                    self.snap.files.insert(path, FileEntry { hash, size });
                }
                "delete" => {
                    let path = c["path"].as_str().unwrap_or("").to_string();
                    self.snap.files.remove(&path);
                }
                "rename" => {
                    let from = c["from"].as_str().unwrap_or("").to_string();
                    let to = c["path"].as_str().unwrap_or("").to_string();
                    let hash = c["hash"].as_str().unwrap_or("").to_string();
                    let size = c["size"].as_u64().unwrap_or(0);
                    self.snap.files.remove(&from);
                    self.snap.files.insert(to, FileEntry { hash, size });
                }
                _ => {}
            }
        }
        self.snap.pending_batches.insert(
            batch_id.to_string(),
            PendingBatch {
                changes: changes.to_vec(),
                status: "unsent".to_string(),
                created_at: store::now_rfc3339(),
            },
        );
    }

    /// Upload blobs then POST /v1/commit. On success the batch is removed
    /// from pending. A Lost error means the server may have durably
    /// committed: the batch stays pending with status "sent" and will be
    /// retried with the SAME batch_id.
    fn commit_batch(&mut self, batch_id: &str) -> Result<i64, PushError> {
        let changes = match self.snap.pending_batches.get(batch_id) {
            Some(b) => b.changes.clone(),
            None => return Ok(0),
        };
        // Upload content first.
        for c in &changes {
            let op = c["op"].as_str().unwrap_or("");
            if op == "upsert" || op == "rename" {
                let path = c["path"].as_str().unwrap_or("");
                let hash = c["hash"].as_str().unwrap_or("");
                if hash.is_empty() {
                    continue;
                }
                let data = match self.disk_bytes(path) {
                    Some(d) => d,
                    None => return Err(PushError::Server(format!("file vanished: {}", path))),
                };
                let actual = store::sha256_hex(&data);
                if actual != hash {
                    return Err(PushError::Server(format!(
                        "content changed under us for {} (scan again)",
                        path
                    )));
                }
                let url = format!("{}/v1/blob/{}", self.server_url, hash);
                match httpc::request(&url, "PUT", Some(&data), TIMEOUT) {
                    Ok(_) => {}
                    Err(HttpError::Lost(m)) => {
                        // Blob may or may not be stored; harmless either way,
                        // the commit below carries the same hash and the
                        // server validates content presence.
                        mark_sent(&mut self.snap, batch_id);
                        return Err(PushError::ReceiptLost(format!("blob upload receipt lost: {}", m)));
                    }
                    Err(HttpError::Connect(m)) | Err(HttpError::Timeout(m)) => {
                        return Err(PushError::Unreachable(format!("blob upload: {}", m)));
                    }
                    Err(HttpError::Protocol(m)) => return Err(PushError::Server(m)),
                }
            }
        }
        let body = json!({"client": self.client_id, "batch_id": batch_id, "changes": changes});
        let url = format!("{}/v1/commit", self.server_url);
        match httpc::request(&url, "POST", Some(body.to_string().as_bytes()), TIMEOUT) {
            Ok(resp) => {
                if resp.status != 200 {
                    return Err(PushError::Server(format!(
                        "http {}: {}",
                        resp.status,
                        String::from_utf8_lossy(&resp.body)
                    )));
                }
                let v = resp.json().map_err(|e| PushError::Server(e.to_string()))?;
                let ver = v["version"].as_i64().unwrap_or(self.snap.version);
                self.snap.version = ver;
                self.snap.pending_batches.remove(batch_id);
                Ok(changes.len() as i64)
            }
            Err(HttpError::Lost(m)) => {
                mark_sent(&mut self.snap, batch_id);
                Err(PushError::ReceiptLost(format!(
                    "commit ack lost (server may have committed; will retry same batch_id): {}",
                    m
                )))
            }
            Err(HttpError::Connect(m)) | Err(HttpError::Timeout(m)) => {
                Err(PushError::Unreachable(format!("commit: {}", m)))
            }
            Err(HttpError::Protocol(m)) => Err(PushError::Server(m)),
        }
    }

    // ---------------- pull ----------------

    /// Pull and apply other clients' changes after our applied_seq.
    fn pull_changes(&mut self) -> Result<i64, String> {
        let url = format!(
            "{}/v1/feed?since={}&client={}",
            self.server_url, self.snap.applied_seq, self.client_id
        );
        let resp = httpc::request(&url, "GET", None, TIMEOUT).map_err(|e| e.to_string())?;
        if resp.status != 200 {
            return Err(format!("feed http {}", resp.status));
        }
        let v = resp.json().map_err(|e| e.to_string())?;
        let entries = v["entries"].as_array().cloned().unwrap_or_default();
        let mut applied = 0i64;
        for e in &entries {
            let seq = e["server_seq"].as_i64().unwrap_or(0);
            if seq <= self.snap.applied_seq {
                continue;
            }
            self.apply_remote(e);
            self.snap.applied_seq = seq;
            applied += 1;
            // Persist after each entry so a kill mid-pull cannot replay
            // already-applied entries in a way that duplicates effects
            // (re-applying is content-idempotent, but cursors are cheap).
            self.save();
        }
        Ok(applied)
    }

    /// Apply one remote entry with conflict preservation.
    fn apply_remote(&mut self, e: &Value) {
        let change = &e["change"];
        let op = change["op"].as_str().unwrap_or("").to_string();
        let src_client = e["client"].as_str().unwrap_or("?").to_string();
        let src_batch = e["batch_id"].as_str().unwrap_or("?").to_string();

        // A rename arrives as one atomic entry; treat as delete-from + upsert-to.
        if op == "rename" {
            self.apply_remote(&json!({
                "server_seq": e["server_seq"], "commit_seq": e["commit_seq"],
                "version": e["version"], "client": e["client"], "batch_id": e["batch_id"],
                "change": {"op": "delete", "path": change["from"], "base_hash": change["base_hash"]}
            }));
            self.apply_remote(&json!({
                "server_seq": e["server_seq"], "commit_seq": e["commit_seq"],
                "version": e["version"], "client": e["client"], "batch_id": e["batch_id"],
                "change": {"op": "upsert", "path": change["path"], "hash": change["hash"],
                            "size": change["size"], "base_hash": ""}
            }));
            return;
        }

        let path = change["path"].as_str().unwrap_or("").to_string();
        let remote_hash = change["hash"].as_str().unwrap_or("").to_string();
        let base_hash = change["base_hash"].as_str().unwrap_or("").to_string();

        let baseline = self.snap.files.get(&path).map(|f| f.hash.clone());
        let disk_hash = self.disk_bytes(&path).map(|d| store::sha256_hex(&d));
        let pending_dirty = self.pending_has_path(&path);
        // "dirty" = our copy has moved away from what the other side based
        // its change on: either we have unconfirmed local work on the path,
        // or the disk/baseline no longer matches the remote base.
        let dirty = pending_dirty
            || (baseline.as_deref() != Some(base_hash.as_str())
                && !(baseline.is_none() && disk_hash.is_none()));

        if op == "delete" {
            if disk_hash.is_none() {
                self.snap.files.remove(&path);
                return;
            }
            if dirty {
                // Remote delete vs local work: keep the readable local
                // content and present the delete intent as a tombstone.
                let marker = format!("{}.deleted-by-{}-{}.json", path, src_client, short(&src_batch));
                let tomb = json!({
                    "note": "DELETE-INTENT TOMBSTONE: the remote client deleted this file while this machine had local changes. The original path keeps the readable local content.",
                    "deleted_by": src_client,
                    "deleted_batch": src_batch,
                    "path": path,
                    "detected_at": store::now_rfc3339(),
                    "client": self.client_id,
                });
                let _ = self.write_project_file(&marker, serde_json::to_vec_pretty(&tomb).unwrap().as_slice());
                let tomb_hash = self
                    .disk_bytes(&marker)
                    .map(|d| store::sha256_hex(&d))
                    .unwrap_or_default();
                let tomb_size = self.disk_bytes(&marker).map(|d| d.len()).unwrap_or(0) as u64;
                self.snap.files.insert(marker.clone(), FileEntry { hash: tomb_hash, size: tomb_size });
                self.record_conflict(&path, "delete-edit", &src_client, &src_batch, "", &baseline.unwrap_or_default(), "", &marker);
                self.withdraw_pending(&path);
            } else {
                self.remove_project_file(&path);
                self.snap.files.remove(&path);
            }
            return;
        }

        // upsert
        if disk_hash.as_deref() == Some(remote_hash.as_str()) {
            // Content already identical: apply as a baseline update only.
            let size = self.disk_bytes(&path).map(|d| d.len()).unwrap_or(0) as u64;
            self.snap.files.insert(path, FileEntry { hash: remote_hash, size });
            return;
        }
        let data = match self.fetch_blob(&remote_hash) {
            Some(d) => d,
            None => {
                self.last_error = Some(format!("blob missing on server for {}", path));
                return;
            }
        };
        if dirty {
            // Concurrent edit: keep local content at the original path,
            // save the remote content under a conflict file. Both readable.
            let cpath = format!("{}.conflict-from-{}-{}", path, src_client, short(&src_batch));
            let _ = self.write_project_file(&cpath, &data);
            let ch = store::sha256_hex(&data);
            self.snap.files.insert(
                cpath.clone(),
                FileEntry { hash: ch, size: data.len() as u64 },
            );
            self.record_conflict(&path, "edit-edit", &src_client, &src_batch, &remote_hash, &baseline.unwrap_or_default(), &remote_hash, &cpath);
            self.withdraw_pending(&path);
        } else {
            let _ = self.write_project_file(&path, &data);
            self.snap.files.insert(
                path,
                FileEntry { hash: remote_hash, size: data.len() as u64 },
            );
        }
    }

    fn fetch_blob(&self, hash: &str) -> Option<Vec<u8>> {
        let url = format!("{}/v1/blob/{}", self.server_url, hash);
        match httpc::request(&url, "GET", None, TIMEOUT) {
            Ok(r) if r.status == 200 => {
                if store::sha256_hex(&r.body) == hash {
                    Some(r.body)
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    fn pending_has_path(&self, path: &str) -> bool {
        self.snap
            .pending_batches
            .values()
            .any(|b| b.changes.iter().any(|c| {
                c["path"].as_str() == Some(path) || c["from"].as_str() == Some(path)
            }))
    }

    /// Remove all pending changes for a path after a conflict so that the
    /// preserved pair (local + conflict/tombstone files) becomes the new
    /// state to propagate, instead of blindly replaying the old intent.
    fn withdraw_pending(&mut self, path: &str) {
        let ids: Vec<String> = self.snap.pending_batches.keys().cloned().collect();
        for id in ids {
            if let Some(b) = self.snap.pending_batches.get_mut(&id) {
                b.changes.retain(|c| {
                    c["path"].as_str() != Some(path) && c["from"].as_str() != Some(path)
                });
            }
            if self
                .snap
                .pending_batches
                .get(&id)
                .map(|b| b.changes.is_empty())
                .unwrap_or(false)
            {
                self.snap.pending_batches.remove(&id);
            }
        }
    }

    fn record_conflict(
        &mut self,
        path: &str,
        kind: &str,
        src_client: &str,
        src_batch: &str,
        remote_hash: &str,
        local_hash: &str,
        _rh: &str,
        saved_as: &str,
    ) {
        let marker = if saved_as.ends_with(".json") {
            saved_as.to_string()
        } else {
            String::new()
        };
        self.snap.conflicts.push(crate::store::ConflictRecord {
            path: path.to_string(),
            kind: kind.to_string(),
            source_client: src_client.to_string(),
            source_batch: src_batch.to_string(),
            remote_hash: remote_hash.to_string(),
            local_hash: local_hash.to_string(),
            saved_as: saved_as.to_string(),
            marker: marker.to_string(),
            detected_at: store::now_rfc3339(),
        });
    }

    // ---------------- events ----------------

    /// Push local events that have not been confirmed on the server yet
    /// (event_push_cursor). Idempotent by event_id on the server side.
    fn push_events(&mut self) -> Result<i64, String> {
        let all = self.store.read_events().map_err(|e| e.to_string())?;
        let total = all.len() as i64;
        let from = self.snap.event_push_cursor;
        if from >= total {
            return Ok(0);
        }
        let slice: Vec<Value> = all[from as usize..].to_vec();
        let batch_id = store::new_batch_id(&self.client_id);
        let body = json!({
            "client": self.client_id,
            "batch_id": batch_id,
            "events": slice,
        });
        let url = format!("{}/v1/events", self.server_url);
        let resp = httpc::request(&url, "POST", Some(body.to_string().as_bytes()), TIMEOUT)
            .map_err(|e| e.to_string())?;
        if resp.status != 200 {
            return Err(format!("events http {}: {}", resp.status, String::from_utf8_lossy(&resp.body)));
        }
        let _ = resp.json().map_err(|e| e.to_string())?;
        // Durable on the server (accepted or duplicates): advance cursor.
        self.snap.event_push_cursor = total;
        Ok(slice.len() as i64)
    }

    fn pull_events(&mut self) -> Result<i64, String> {
        let url = format!(
            "{}/v1/events/feed?since={}&client={}",
            self.server_url, self.snap.applied_event_seq, self.client_id
        );
        let resp = httpc::request(&url, "GET", None, TIMEOUT).map_err(|e| e.to_string())?;
        if resp.status != 200 {
            return Err(format!("events feed http {}", resp.status));
        }
        let v = resp.json().map_err(|e| e.to_string())?;
        let entries = v["entries"].as_array().cloned().unwrap_or_default();
        let have = self.store.event_ids().map_err(|e| e.to_string())?;
        let mut fresh: Vec<Value> = Vec::new();
        let mut max_seq = self.snap.applied_event_seq;
        for ent in &entries {
            let seq = ent["event_seq"].as_i64().unwrap_or(0);
            if seq > max_seq {
                max_seq = seq;
            }
            let ev = ent.get("event").cloned().unwrap_or(Value::Null);
            let id = ev.get("event_id").and_then(|x| x.as_str()).unwrap_or("").to_string();
            if !id.is_empty() && !have.contains(&id) {
                fresh.push(ev);
            }
        }
        if !fresh.is_empty() {
            self.store.append_events(&fresh).map_err(|e| e.to_string())?;
        }
        self.snap.applied_event_seq = max_seq;
        Ok(fresh.len() as i64)
    }

    /// Import a JSONL file (full or a 1-based inclusive line range) into
    /// the local library, then push. Re-importing the same content is a
    /// no-op thanks to event_id dedup both locally and remotely.
    pub fn import_events_file(&mut self, file: &Path, from: Option<i64>, to: Option<i64>) -> Result<(i64, i64, i64), String> {
        let data = fs::read(file).map_err(|e| format!("read {}: {}", file.display(), e))?;
        let text = String::from_utf8(data).map_err(|_| "file is not UTF-8".to_string())?;
        let mut events: Vec<Value> = Vec::new();
        for (idx, line) in text.lines().enumerate() {
            let lineno = (idx + 1) as i64;
            if let Some(f) = from {
                if lineno < f {
                    continue;
                }
            }
            if let Some(t) = to {
                if lineno > t {
                    continue;
                }
            }
            if line.trim().is_empty() {
                continue;
            }
            let v: Value = serde_json::from_str(line)
                .map_err(|e| format!("line {}: {}", lineno, e))?;
            validate_event(&v).map_err(|e| format!("line {}: {}", lineno, e))?;
            events.push(v);
        }
        let submitted = events.len() as i64;
        let have = self.store.event_ids().map_err(|e| e.to_string())?;
        let fresh: Vec<Value> = events
            .into_iter()
            .filter(|ev| {
                let id = ev.get("event_id").and_then(|x| x.as_str()).unwrap_or("");
                !id.is_empty() && !have.contains(id)
            })
            .collect();
        let accepted_local = fresh.len() as i64;
        if !fresh.is_empty() {
            self.store.append_events(&fresh).map_err(|e| e.to_string())?;
        }
        let pushed = self.push_events()?;
        Ok((submitted, accepted_local, pushed))
    }

    // ---------------- one full sync round ----------------

    pub fn sync(&mut self) -> SyncReport {
        self.last_error = None;
        let mut report = SyncReport {
            pushed: 0,
            pulled: 0,
            conflicts: 0,
            pending_push: 0,
            events_pushed: 0,
            events_pulled: 0,
            receipt_lost: false,
            offline: self.snap.offline,
        };
        if self.snap.offline {
            report.pending_push = self.pending_count();
            return report;
        }
        // 1) push (pending retries first, then new scan)
        match self.push_all() {
            Ok((n, lost)) => {
                report.pushed = n;
                report.receipt_lost = lost;
            }
            Err(msg) => {
                self.last_error = Some(msg);
            }
        }
        // 2) push local events (if any unconfirmed)
        if self.last_error.is_none() {
            match self.push_events() {
                Ok(n) => report.events_pushed = n,
                Err(e) => self.last_error = Some(e),
            }
        }
        // 3) pull remote changes
        if self.last_error.is_none() {
            match self.pull_changes() {
                Ok(n) => report.pulled = n,
                Err(e) => self.last_error = Some(e),
            }
        }
        // 4) pull remote events
        if self.last_error.is_none() {
            match self.pull_events() {
                Ok(n) => report.events_pulled = n,
                Err(e) => self.last_error = Some(e),
            }
        }
        report.pending_push = self.pending_count();
        report.conflicts = self.snap.conflicts.len() as i64;
        self.save();
        report
    }

    pub fn pending_count(&self) -> i64 {
        self.snap
            .pending_batches
            .values()
            .map(|b| b.changes.len() as i64)
            .sum()
    }

    pub fn status_json(&self) -> Value {
        json!({
            "client_id": self.client_id,
            "online": !self.snap.offline,
            "version": self.snap.version,
            "applied_seq": self.snap.applied_seq,
            "applied_event_seq": self.snap.applied_event_seq,
            "event_push_cursor": self.snap.event_push_cursor,
            "pending_changes": self.pending_count(),
            "pending_batches": self.snap.pending_batches.len(),
            "conflicts": self.snap.conflicts.len(),
            "events": self.store.read_events().map(|v| v.len()).unwrap_or(0),
            "files": self.snap.files.len(),
            "server_url": self.server_url,
            "project_dir": self.snap.project_dir,
        })
    }

    pub fn files_json(&self) -> Value {
        let disk = self.scan_disk().unwrap_or_default();
        let arr: Vec<Value> = disk
            .iter()
            .map(|(p, d)| {
                json!({
                    "path": p,
                    "size": d.len(),
                    "sha256": store::sha256_hex(d),
                })
            })
            .collect();
        json!({"client_id": self.client_id, "files": arr})
    }

    pub fn conflicts_json(&self) -> Value {
        json!({
            "client_id": self.client_id,
            "conflicts": self.snap.conflicts,
        })
    }

    pub fn set_offline(&mut self, offline: bool) {
        self.snap.offline = offline;
        self.save();
    }
}

fn walk_files(root: &Path, dir: &Path, out: &mut BTreeMap<String, Vec<u8>>) -> std::io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let p = entry.path();
        if p.is_dir() {
            walk_files(root, &p, out)?;
        } else if p.is_file() {
            let rel = p.strip_prefix(root).unwrap_or(&p).to_path_buf();
            let key = rel.to_string_lossy().replace('\\', "/");
            if let Ok(data) = fs::read(&p) {
                out.insert(key, data);
            }
        }
    }
    Ok(())
}

enum PushError {
    ReceiptLost(String),
    Unreachable(String),
    Server(String),
}

fn mark_sent(snap: &mut Snapshot, batch_id: &str) {
    if let Some(b) = snap.pending_batches.get_mut(batch_id) {
        b.status = "sent".to_string();
    }
}

fn short(s: &str) -> String {
    s.chars().rev().take(8).collect::<Vec<_>>().into_iter().rev().collect()
}

pub fn validate_event(v: &Value) -> Result<(), String> {
    let obj = v.as_object().ok_or("not a JSON object")?;
    for k in ["event_id", "thread_id", "turn_id", "item_id", "type"] {
        let s = obj.get(k).and_then(|x| x.as_str()).filter(|s| !s.is_empty());
        if s.is_none() {
            return Err(format!("missing {}", k));
        }
    }
    let seq = obj
        .get("seq")
        .and_then(|x| x.as_i64())
        .ok_or("missing or invalid seq")?;
    if seq < 1 {
        return Err("seq must be >= 1".to_string());
    }
    let ty = obj.get("type").and_then(|x| x.as_str()).unwrap_or("");
    match ty {
        "user.message" | "assistant.delta" => {
            if obj.get("text").and_then(|x| x.as_str()).is_none() {
                return Err(format!("{} requires text", ty));
            }
        }
        "tool.call" => {
            if obj.get("call_id").and_then(|x| x.as_str()).is_none() {
                return Err("tool.call requires call_id".to_string());
            }
            if obj.get("name").and_then(|x| x.as_str()).is_none() {
                return Err("tool.call requires name".to_string());
            }
            if obj.get("arguments").is_none() {
                return Err("tool.call requires arguments".to_string());
            }
        }
        "tool.result" => {
            if obj.get("call_id").and_then(|x| x.as_str()).is_none() {
                return Err("tool.result requires call_id".to_string());
            }
            if obj.get("result").is_none() {
                return Err("tool.result requires result".to_string());
            }
        }
        "assistant.done" => {}
        other => return Err(format!("unknown type {:?}", other)),
    }
    Ok(())
}

// Re-export for main.rs convenience.
pub use store::sha256_hex as sha_hex;

pub type ChangeMap = Map<String, Value>;
