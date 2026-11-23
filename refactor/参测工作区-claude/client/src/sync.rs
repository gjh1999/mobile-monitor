use crate::http::{self, HttpError};
use crate::projection;
use crate::state::{BaseEntry, Core, Inflight};
use crate::util::*;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;

#[derive(Clone, Debug)]
pub struct PendingOp {
    pub path: String,
    pub base_version: u64,
    pub deleted: bool,
    pub hash: String,
    pub content: Option<Vec<u8>>,
    pub op_id: String,
}

fn make_op_id(device: &str, path: &str, base_version: u64, deleted: bool, hash: &str) -> String {
    let sig = format!(
        "{}|{}|{}|{}|{}",
        device,
        path,
        base_version,
        if deleted { "delete" } else { "put" },
        hash
    );
    let full = sha256_hex(sig.as_bytes());
    let safe: String = device
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect();
    format!("{}-{}", safe, &full[..32])
}

impl PendingOp {
    fn to_json(&self) -> Value {
        json!({
            "path": self.path,
            "kind": if self.deleted { "delete" } else { "put" },
            "base_version": self.base_version,
            "hash": self.hash,
            "op_id": self.op_id,
        })
    }
}

pub fn compute_pending(core: &Core, files: &BTreeMap<String, Vec<u8>>) -> Vec<PendingOp> {
    let mut hashes: BTreeMap<String, String> = BTreeMap::new();
    for (p, b) in files {
        hashes.insert(p.clone(), sha256_hex(b));
    }
    let mut paths: BTreeSet<String> = core.state.base.keys().cloned().collect();
    paths.extend(hashes.keys().cloned());
    let mut out = Vec::new();
    for path in paths {
        let base = core.state.base.get(&path);
        let cur_hash = hashes.get(&path);
        let content = files.get(&path);
        match (base, cur_hash, content) {
            (None, Some(h), Some(c)) => {
                let op_id = make_op_id(&core.state.device_id, &path, 0, false, h);
                out.push(PendingOp {
                    path,
                    base_version: 0,
                    deleted: false,
                    hash: h.clone(),
                    content: Some(c.clone()),
                    op_id,
                });
            }
            (Some(b), Some(h), Some(c)) => {
                let needs = if b.deleted {
                    true
                } else {
                    h != &b.hash
                };
                if needs {
                    let op_id = make_op_id(&core.state.device_id, &path, b.version, false, h);
                    out.push(PendingOp {
                        path,
                        base_version: b.version,
                        deleted: false,
                        hash: h.clone(),
                        content: Some(c.clone()),
                        op_id,
                    });
                }
            }
            (Some(b), None, None) => {
                if !b.deleted {
                    let op_id =
                        make_op_id(&core.state.device_id, &path, b.version, true, &b.hash);
                    out.push(PendingOp {
                        path,
                        base_version: b.version,
                        deleted: true,
                        hash: b.hash.clone(),
                        content: None,
                        op_id,
                    });
                }
            }
            _ => {}
        }
    }
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

fn local_matches_base(base: Option<&BaseEntry>, bytes: Option<&Vec<u8>>) -> bool {
    match (base, bytes) {
        (None, None) => true,
        (None, Some(_)) => false,
        (Some(b), None) => b.deleted,
        (Some(b), Some(content)) => !b.deleted && sha256_hex(content) == b.hash,
    }
}

pub fn sync_once(core: &mut Core) {
    if !core.state.paused {
        process_pending_imports(core);
    }
    let files = match scan_project(&core.project) {
        Ok(f) => f,
        Err(e) => {
            core.record_error(format!("scan project failed: {}", e));
            return;
        }
    };
    let ops = compute_pending(core, &files);
    core.last_pending = ops.iter().map(|o| o.to_json()).collect();

    if core.state.paused {
        return;
    }
    if core.state.awaiting.is_some() {
        core.last_error = Some("success receipt missing; retry is blocked until /retry or restart".into());
        return;
    }
    if core.state.inflight.is_some() {
        core.last_error =
            Some("in-flight operation without receipt; retry blocked until /retry".into());
        return;
    }

    let server = core.server.clone();
    let device = core.state.device_id.clone();
    let mut attempted = 0usize;
    let mut pushed_names: Vec<String> = Vec::new();

    for op in ops {
        if core.state.paused {
            break;
        }
        let inflight = Inflight {
            op_id: op.op_id.clone(),
            path: op.path.clone(),
            base_version: op.base_version,
            deleted: op.deleted,
            hash: op.hash.clone(),
            started_at: now_ms(),
        };
        core.state.inflight = Some(inflight.clone());
        core.save();

        let body = json!({
            "device_id": device,
            "op": {
                "op_id": op.op_id,
                "path": op.path,
                "base_version": op.base_version,
                "deleted": op.deleted,
                "hash": if op.deleted { String::new() } else { op.hash.clone() },
                "content_b64": if op.deleted {
                    String::new()
                } else {
                    b64_encode(op.content.as_deref().unwrap_or_default())
                },
            }
        });
        match http::request(&server, "POST", "/api/push", Some(&body)) {
            Ok(resp) => {
                let result = resp
                    .get("result")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                match result.as_str() {
                    "applied" | "already_applied" => {
                        let version = resp.get("version").and_then(Value::as_u64).unwrap_or(0);
                        let entry = BaseEntry {
                            version,
                            deleted: op.deleted,
                            hash: if op.deleted {
                                String::new()
                            } else {
                                op.hash.clone()
                            },
                        };
                        core.state.base.insert(op.path.clone(), entry);
                        core.state.inflight = None;
                        core.state.last_sync_at = Some(now_ms());
                        core.receipt_resolved(
                            &inflight.op_id,
                            &format!("{} version={}", result, version),
                        );
                        core.last_error = None;
                        core.server_seq = resp
                            .get("server_seq")
                            .and_then(Value::as_u64)
                            .unwrap_or(core.server_seq);
                        core.save();
                        logline(&format!(
                            "PUSH_ACK device={} path={} result={} version={} op={}",
                            device, op.path, result, version, op.op_id
                        ));
                        attempted += 1;
                        pushed_names.push(op.path.clone());
                    }
                    "conflict" => {
                        let server_view = resp.get("server").cloned().unwrap_or(Value::Null);
                        handle_conflict(core, &op, &server_view);
                        core.state.inflight = None;
                        core.save();
                        logline(&format!(
                            "CONFLICT device={} path={} base={} server_version={} server_deleted={}",
                            device,
                            op.path,
                            op.base_version,
                            server_view.get("version").and_then(Value::as_u64).unwrap_or(0),
                            server_view.get("deleted").and_then(Value::as_bool).unwrap_or(true)
                        ));
                        break;
                    }
                    other => {
                        core.state.inflight = None;
                        core.record_error(format!(
                            "unexpected push result {:?} for path {}: {}",
                            other, op.path, resp
                        ));
                        break;
                    }
                }
            }
            Err(HttpError::AfterSend(msg)) => {
                let awaiting = core.state.inflight.take();
                core.state.awaiting = awaiting;
                core.last_error = Some(format!(
                    "success receipt lost after server commit; retry blocked: {}",
                    msg
                ));
                core.save();
                logline(&format!(
                    "RECEIPT_LOST device={} path={} op={} detail={} -> awaiting_confirmation (retry blocked)",
                    device, op.path, op.op_id, msg
                ));
                return;
            }
            Err(e) => {
                core.state.inflight = None;
                core.record_error(format!("push transport error for {}: {:?}", op.path, e));
                return;
            }
        }
    }

    if core.state.awaiting.is_some() {
        return;
    }

    let url = format!(
        "/api/pull?device_id={}&since={}",
        url_encode(&device),
        core.state.last_seen_version
    );
    match http::request(&server, "GET", &url, None) {
        Ok(resp) => {
            let mut applied = 0usize;
            if let Some(entries) = resp.get("entries").and_then(Value::as_array) {
                for entry in entries {
                    apply_remote(core, entry);
                    applied += 1;
                }
            }
            let seq = resp
                .get("server_seq")
                .and_then(Value::as_u64)
                .unwrap_or(core.state.last_seen_version);
            let seq_changed = seq > core.state.last_seen_version;
            if seq_changed {
                core.state.last_seen_version = seq;
            }
            core.server_seq = seq;
            core.last_error = None;
            if applied > 0 || seq_changed {
                core.state.last_sync_at = Some(now_ms());
                core.save();
            }
            if applied > 0 {
                logline(&format!(
                    "PULL device={} applied_entries={} server_seq={}",
                    device, applied, seq
                ));
            }
            if attempted > 0 && applied == 0 {
                logline(&format!(
                    "SYNC_OK device={} pushed={:?} server_seq={}",
                    device, pushed_names, seq
                ));
            }
        }
        Err(e) => {
            core.record_error(format!("pull failed: {:?}", e));
        }
    }
}

fn handle_conflict(core: &mut Core, op: &PendingOp, server: &Value) {
    let path = op.path.clone();
    let server_version = server
        .get("version")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let server_deleted = server
        .get("deleted")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let server_hash = server
        .get("hash")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let abs = core.project_path(&path);
    let local_bytes = fs::read(&abs).ok();
    let local_hash = local_bytes
        .as_ref()
        .map(|b| sha256_hex(b))
        .unwrap_or_default();
    let device = core.state.device_id.clone();
    let mut conflict_copy = Value::Null;
    let kind: &str;

    if op.deleted {
        if server_deleted {
            kind = "both-deleted";
        } else {
            kind = "local-deleted-remote-modified";
            if let Some(b64) = server.get("content_b64").and_then(Value::as_str) {
                if let Ok(bytes) = b64_decode(b64) {
                    if let Err(e) = atomic_write(&abs, &bytes) {
                        core.record_error(format!("restore remote content {}: {}", path, e));
                    } else {
                        logline(&format!(
                            "CONFLICT_RESTORED_REMOTE path={} server_version={}",
                            path, server_version
                        ));
                    }
                }
            }
        }
    } else if server_deleted {
        kind = "remote-deleted-vs-local-modified";
        if let Some(bytes) = &local_bytes {
            let rel = choose_conflict_path(
                &core.project,
                &path,
                &device,
                server_version,
                &local_hash,
            );
            match atomic_write(&core.project_path(&rel), bytes) {
                Ok(()) => {
                    conflict_copy = Value::String(rel.clone());
                    let _ = fs::remove_file(&abs);
                    logline(&format!(
                        "CONFLICT_PRESERVED path={} copy={} hash={} server_deleted=true version={}",
                        path, rel, local_hash, server_version
                    ));
                }
                Err(e) => core.record_error(format!("write conflict copy {}: {}", rel, e)),
            }
        }
    } else {
        kind = "both-modified";
        if let Some(bytes) = &local_bytes {
            let rel = choose_conflict_path(
                &core.project,
                &path,
                &device,
                server_version,
                &local_hash,
            );
            match atomic_write(&core.project_path(&rel), bytes) {
                Ok(()) => {
                    conflict_copy = Value::String(rel.clone());
                    logline(&format!(
                        "CONFLICT_PRESERVED path={} copy={} hash={} version={}",
                        path, rel, local_hash, server_version
                    ));
                }
                Err(e) => core.record_error(format!("write conflict copy {}: {}", rel, e)),
            }
        }
        if let Some(b64) = server.get("content_b64").and_then(Value::as_str) {
            if let Ok(bytes) = b64_decode(b64) {
                if let Err(e) = atomic_write(&abs, &bytes) {
                    core.record_error(format!("apply remote content {}: {}", path, e));
                }
            }
        }
    }

    core.state.base.insert(
        path.clone(),
        BaseEntry {
            version: server_version,
            deleted: server_deleted,
            hash: if server_deleted {
                String::new()
            } else {
                server_hash.clone()
            },
        },
    );
    core.state.conflicts.push(json!({
        "path": path,
        "kind": kind,
        "detected_at": now_ms(),
        "device": device,
        "server_version": server_version,
        "server_deleted": server_deleted,
        "server_hash": server_hash,
        "prior_hash": server.get("prior_hash").cloned().unwrap_or(Value::Null),
        "local_hash": local_hash,
        "local_deleted": op.deleted,
        "local_conflict_path": conflict_copy,
        "resolution": "remote state kept at original path; local content preserved in conflict copy when present",
    }));
}

fn apply_remote(core: &mut Core, entry: &Value) {
    let path = match entry.get("path").and_then(Value::as_str) {
        Some(p) => p.to_string(),
        None => return,
    };
    let version = entry
        .get("version")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let deleted = entry
        .get("deleted")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let hash = entry
        .get("hash")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let abs = core.project_path(&path);
    let local_bytes = fs::read(&abs).ok();
    let base = core.state.base.get(&path).cloned();
    if !local_matches_base(base.as_ref(), local_bytes.as_ref()) {
        let device = core.state.device_id.clone();
        let local_hash = local_bytes
            .as_ref()
            .map(|b| sha256_hex(b))
            .unwrap_or_default();
        let mut copy = Value::Null;
        if let Some(bytes) = &local_bytes {
            let rel = choose_conflict_path(&core.project, &path, &device, version, &local_hash);
            match atomic_write(&core.project_path(&rel), bytes) {
                Ok(()) => {
                    copy = Value::String(rel.clone());
                    logline(&format!(
                        "CONFLICT_PRESERVED path={} copy={} hash={} (pull)",
                        path, rel, local_hash
                    ));
                }
                Err(e) => core.record_error(format!("write pull conflict copy {}: {}", rel, e)),
            }
        }
        core.state.conflicts.push(json!({
            "path": path,
            "kind": if local_bytes.is_some() { "pull-remote-vs-local-modified" } else { "pull-remote-vs-local-deleted" },
            "detected_at": now_ms(),
            "device": device,
            "server_version": version,
            "server_deleted": deleted,
            "server_hash": hash,
            "local_hash": local_hash,
            "local_conflict_path": copy,
            "resolution": "remote state kept at original path; local content preserved in conflict copy when present",
        }));
    }

    if deleted {
        match fs::remove_file(&abs) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => core.record_error(format!("apply remote delete {}: {}", path, e)),
        }
    } else if let Some(b64) = entry.get("content_b64").and_then(Value::as_str) {
        match b64_decode(b64) {
            Ok(bytes) => {
                if sha256_hex(&bytes) != hash {
                    core.record_error(format!("remote content hash mismatch for {}", path));
                    return;
                }
                if let Err(e) = atomic_write(&abs, &bytes) {
                    core.record_error(format!("apply remote content {}: {}", path, e));
                    return;
                }
            }
            Err(e) => {
                core.record_error(format!("decode remote content {}: {}", path, e));
                return;
            }
        }
    } else {
        core.record_error(format!("pull entry without content for live path {}", path));
        return;
    }
    core.state.base.insert(
        path,
        BaseEntry {
            version,
            deleted,
            hash: if deleted { String::new() } else { hash },
        },
    );
}

fn process_pending_imports(core: &mut Core) {
    if core.state.pending_imports.is_empty() {
        return;
    }
    let pending = std::mem::take(&mut core.state.pending_imports);
    let mut still: Vec<Value> = Vec::new();
    for req in pending {
        let file = req
            .get("file")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let start = req.get("start_line").and_then(Value::as_u64);
        let end = req.get("end_line").and_then(Value::as_u64);
        match import_online(core, &file, start, end) {
            Ok(_) => logline(&format!("IMPORT_QUEUED_DONE file={}", file)),
            Err(e) => {
                core.record_error(format!("queued import failed: {}", e));
                still.push(req);
            }
        }
    }
    core.state.pending_imports = still;
    core.save();
}

pub fn import_file(
    core: &mut Core,
    file: &str,
    start: Option<u64>,
    end: Option<u64>,
) -> Result<Value, String> {
    if core.state.paused {
        core.state.pending_imports.push(json!({
            "file": file,
            "start_line": start,
            "end_line": end,
            "queued_at": now_ms(),
        }));
        core.save();
        logline(&format!(
            "IMPORT_QUEUED file={} start={:?} end={:?} (device paused)",
            file, start, end
        ));
        return Ok(json!({
            "ok": true,
            "queued": true,
            "file": file,
            "start_line": start,
            "end_line": end,
            "reason": "device is paused; import will run after resume",
        }));
    }
    import_online(core, file, start, end)
}

fn import_online(
    core: &mut Core,
    file: &str,
    start: Option<u64>,
    end: Option<u64>,
) -> Result<Value, String> {
    let text = fs::read_to_string(file).map_err(|e| format!("read {}: {}", file, e))?;
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let lines: Vec<&str> = text.lines().collect();
    if lines.is_empty() {
        return Err(format!("{} has no lines", file));
    }
    let start_line = start.unwrap_or(1);
    let end_line = end.unwrap_or(lines.len() as u64);
    if start_line == 0 || end_line == 0 {
        return Err("line numbers are 1-based".into());
    }
    if start_line > end_line {
        return Err(format!("start_line {} > end_line {}", start_line, end_line));
    }
    if end_line > lines.len() as u64 {
        return Err(format!(
            "end_line {} exceeds file line count {}",
            end_line,
            lines.len()
        ));
    }
    let mut events: Vec<Value> = Vec::new();
    for idx in (start_line as usize)..=(end_line as usize) {
        let line = lines[idx - 1].trim();
        if line.is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(line)
            .map_err(|e| format!("line {}: invalid JSON: {}", idx, e))?;
        for key in ["event_id", "thread_id", "turn_id", "seq", "type", "item_id"] {
            if v.get(key).is_none() {
                return Err(format!("line {}: missing field {}", idx, key));
            }
        }
        events.push(v);
    }
    if events.is_empty() {
        return Err(format!(
            "no events found in {} lines {}-{}",
            file, start_line, end_line
        ));
    }
    let body = json!({
        "device_id": core.state.device_id,
        "events": events,
    });
    let server = core.server.clone();
    let resp = http::request(&server, "POST", "/api/events", Some(&body))
        .map_err(|e| format!("event upload failed: {:?}", e))?;
    let accepted = resp
        .get("accepted")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let duplicates = resp
        .get("duplicates")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let total = resp.get("total").and_then(Value::as_u64).unwrap_or(0);

    let mut threads: BTreeSet<String> = BTreeSet::new();
    for e in &events {
        if let Some(t) = e.get("thread_id").and_then(Value::as_str) {
            threads.insert(t.to_string());
        }
    }
    for t in &threads {
        let url = format!("/api/events?thread_id={}", url_encode(t));
        if let Ok(v) = http::request(&server, "GET", &url, None) {
            if let Some(arr) = v.get("events").and_then(Value::as_array) {
                core.state.event_cache.insert(t.clone(), arr.clone());
            }
        }
    }
    let threads_vec: Vec<String> = threads.iter().cloned().collect();
    core.state.imports.push(json!({
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "event_lines": events.len(),
        "accepted": accepted,
        "duplicates": duplicates,
        "server_total": total,
        "threads": threads_vec,
        "at": now_ms(),
        "status": "ok",
    }));
    if core.state.imports.len() > 100 {
        let excess = core.state.imports.len() - 100;
        core.state.imports.drain(0..excess);
    }
    core.state.last_sync_at = Some(now_ms());
    core.save();
    logline(&format!(
        "IMPORT device={} file={} lines={}-{} events={} accepted={} duplicates={} total={}",
        core.state.device_id,
        file,
        start_line,
        end_line,
        events.len(),
        accepted,
        duplicates,
        total
    ));
    Ok(json!({
        "ok": true,
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "event_lines": events.len(),
        "accepted": accepted,
        "duplicates": duplicates,
        "server_total": total,
        "threads": threads_vec,
    }))
}

pub fn history(core: &mut Core, thread: &str, turn: Option<&str>) -> Result<Value, String> {
    let url = format!("/api/events?thread_id={}", url_encode(thread));
    let (events, source) = match http::request(&core.server, "GET", &url, None) {
        Ok(v) => {
            let arr = v
                .get("events")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            core.state.event_cache.insert(thread.to_string(), arr.clone());
            core.save();
            (arr, "server")
        }
        Err(e) => {
            let cached = core
                .state
                .event_cache
                .get(thread)
                .cloned()
                .unwrap_or_default();
            if cached.is_empty() {
                return Err(format!(
                    "history unavailable: server error and no cached events: {:?}",
                    e
                ));
            }
            (cached, "cache")
        }
    };
    core.last_history_source = Some(source.to_string());
    let mut out = projection::project(&events, thread, turn);
    if let Some(obj) = out.as_object_mut() {
        obj.insert("source".into(), json!(source));
    }
    Ok(out)
}

pub fn replay(core: &mut Core, thread: &str, turn: Option<&str>) -> Result<Value, String> {
    let history = history(core, thread, turn)?;
    let observed = projection::count_tool_calls(&history);
    Ok(json!({
        "history": history,
        "replay_mode": "read-only-projection",
        "tool_dispatch_attempts": 0,
        "tool_call_items_observed": observed,
        "note": "tool.call and tool.result events are rendered as history only; this client has no tool executor",
    }))
}

pub fn raw_events(core: &mut Core, thread: Option<&str>) -> Result<Value, String> {
    let url = match thread {
        Some(t) => format!("/api/events?thread_id={}", url_encode(t)),
        None => "/api/events".to_string(),
    };
    match http::request(&core.server, "GET", &url, None) {
        Ok(v) => {
            if let Some(t) = thread {
                if let Some(arr) = v.get("events").and_then(Value::as_array) {
                    core.state.event_cache.insert(t.to_string(), arr.clone());
                    core.save();
                }
            }
            Ok(v)
        }
        Err(e) => {
            let t = thread.unwrap_or("");
            let cached = core
                .state
                .event_cache
                .get(t)
                .cloned()
                .unwrap_or_default();
            Ok(json!({"ok": true, "thread_id": t, "count": cached.len(), "events": cached, "source": "cache", "error": format!("{:?}", e)}))
        }
    }
}

pub fn threads(core: &mut Core) -> Value {
    match http::request(&core.server, "GET", "/api/threads", None) {
        Ok(v) => v,
        Err(_) => {
            let mut map: BTreeMap<String, BTreeMap<String, BTreeSet<String>>> = BTreeMap::new();
            for (thread, events) in &core.state.event_cache {
                for e in events {
                    let t = e
                        .get("turn_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    let i = e
                        .get("item_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    map.entry(thread.clone())
                        .or_default()
                        .entry(t)
                        .or_default()
                        .insert(i);
                }
            }
            let threads: Vec<Value> = map
                .iter()
                .map(|(tid, turns)| {
                    let turns_v: Vec<Value> = turns
                        .iter()
                        .map(|(turn, items)| {
                            json!({"turn_id": turn, "item_count": items.len()})
                        })
                        .collect();
                    json!({"thread_id": tid, "turns": turns_v})
                })
                .collect();
            json!({"ok": true, "threads": threads, "source": "cache"})
        }
    }
}

pub fn pending_snapshot(core: &Core) -> Vec<Value> {
    match scan_project(&core.project) {
        Ok(files) => compute_pending(core, &files)
            .iter()
            .map(|o| o.to_json())
            .collect(),
        Err(e) => vec![json!({"error": format!("scan failed: {}", e)})],
    }
}

pub fn status(core: &mut Core) -> Value {
    let pending = pending_snapshot(core);
    let awaiting = core.state.awaiting.clone();
    let inflight = core.state.inflight.clone();
    let conflicts: Vec<Value> = core.state.conflicts.iter().rev().take(20).cloned().collect();
    let imports: Vec<Value> = core.state.imports.iter().rev().take(20).cloned().collect();
    let receipts: Vec<Value> = core
        .state
        .receipt_log
        .iter()
        .rev()
        .take(20)
        .map(|r| serde_json::to_value(r).unwrap_or(Value::Null))
        .collect();
    let queue_empty = pending.is_empty()
        && awaiting.is_none()
        && inflight.is_none()
        && core.state.pending_imports.is_empty();
    json!({
        "ok": true,
        "device_id": core.state.device_id,
        "online": !core.state.paused,
        "paused": core.state.paused,
        "project_dir": core.project.to_string_lossy(),
        "state_dir": core.state_path.parent().map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
        "server": core.server,
        "server_seq": core.server_seq,
        "last_seen_version": core.state.last_seen_version,
        "pending_count": pending.len(),
        "pending": pending,
        "inflight": inflight,
        "awaiting_confirmation": awaiting,
        "retry_blocked": core.state.awaiting.is_some(),
        "queue_empty": queue_empty,
        "conflict_count": core.state.conflicts.len(),
        "conflicts": conflicts,
        "pending_imports": core.state.pending_imports,
        "imports": imports,
        "receipt_log": receipts,
        "tool_dispatch_attempts": 0,
        "last_sync_at": core.state.last_sync_at,
        "last_error": core.last_error,
        "created_at": core.state.created_at,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn test_core() -> Core {
        Core {
            state: crate::state::ClientState {
                device_id: "B".into(),
                ..Default::default()
            },
            state_path: PathBuf::from("state.json"),
            project: PathBuf::from("project"),
            server: "http://127.0.0.1:1".into(),
            last_error: None,
            last_pending: Vec::new(),
            server_seq: 0,
            last_history_source: None,
        }
    }

    #[test]
    fn pending_detects_add_modify_delete_and_uses_base_version() {
        let mut core = test_core();
        core.state.base.insert(
            "keep.txt".into(),
            BaseEntry { version: 3, deleted: false, hash: sha256_hex(b"a\n") },
        );
        core.state.base.insert(
            "gone.txt".into(),
            BaseEntry { version: 4, deleted: false, hash: sha256_hex(b"x\n") },
        );
        core.state.base.insert(
            "tomb.txt".into(),
            BaseEntry { version: 5, deleted: true, hash: String::new() },
        );
        let mut files = BTreeMap::new();
        files.insert("keep.txt".into(), b"a\n".to_vec());
        files.insert("new.txt".into(), b"n\n".to_vec());
        files.insert("tomb.txt".into(), b"reborn\n".to_vec());
        let ops = compute_pending(&core, &files);
        let kinds: Vec<(String, bool, u64)> = ops
            .iter()
            .map(|o| (o.path.clone(), o.deleted, o.base_version))
            .collect();
        assert_eq!(
            kinds,
            vec![
                ("gone.txt".to_string(), true, 4),
                ("new.txt".to_string(), false, 0),
                ("tomb.txt".to_string(), false, 5),
            ]
        );
        assert_eq!(ops[0].op_id, make_op_id("B", "gone.txt", 4, true, &sha256_hex(b"x\n")));
        assert_eq!(ops[1].op_id, make_op_id("B", "new.txt", 0, false, &sha256_hex(b"n\n")));
    }

    #[test]
    fn unchanged_files_are_not_pending() {
        let mut core = test_core();
        core.state.base.insert(
            "a.txt".into(),
            BaseEntry { version: 1, deleted: false, hash: sha256_hex(b"same\n") },
        );
        let mut files = BTreeMap::new();
        files.insert("a.txt".into(), b"same\n".to_vec());
        assert!(compute_pending(&core, &files).is_empty());
    }

    #[test]
    fn local_match_rules() {
        let base = BaseEntry { version: 1, deleted: false, hash: sha256_hex(b"x") };
        assert!(local_matches_base(Some(&base), Some(&b"x".to_vec())));
        assert!(!local_matches_base(Some(&base), Some(&b"y".to_vec())));
        assert!(!local_matches_base(Some(&base), None));
        let tomb = BaseEntry { version: 2, deleted: true, hash: String::new() };
        assert!(local_matches_base(Some(&tomb), None));
        assert!(!local_matches_base(Some(&tomb), Some(&b"x".to_vec())));
        assert!(local_matches_base(None, None));
    }
}
