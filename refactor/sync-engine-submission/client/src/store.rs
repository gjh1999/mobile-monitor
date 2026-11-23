// Client-side durable state: the sync snapshot (baselines, pending
// batches, applied cursors) and the event library. Everything is JSON
// on disk, written atomically (tmp + rename), so a hard kill never
// leaves the state directory inconsistent.
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct FileEntry {
    pub hash: String,
    pub size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingBatch {
    pub changes: Vec<Value>,
    pub status: String, // "unsent" | "sent" (sent = awaiting confirmation)
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConflictRecord {
    pub path: String,
    pub kind: String, // "edit-edit" | "delete-edit" | "delete-here-edit-remote"
    pub source_client: String,
    pub source_batch: String,
    pub remote_hash: String,
    pub local_hash: String,
    pub saved_as: String,
    pub marker: String,
    pub detected_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub client_id: String,
    pub server_url: String,
    pub project_dir: String,
    /// Server version of the last confirmed commit of this client.
    pub version: i64,
    /// Highest applied /v1/feed server_seq (other clients' changes).
    pub applied_seq: i64,
    /// Highest applied /v1/events/feed event_seq.
    pub applied_event_seq: i64,
    /// Highest event library line confirmed present on the server.
    pub event_push_cursor: i64,
    pub offline: bool,
    /// Baseline of known content: path -> hash/size. Updated immediately
    /// when local changes are provisioned for push and when remote
    /// changes are applied, so rescans never duplicate confirmed work.
    pub files: BTreeMap<String, FileEntry>,
    pub pending_batches: BTreeMap<String, PendingBatch>,
    pub conflicts: Vec<ConflictRecord>,
}

impl Snapshot {
    pub fn new(client_id: &str, server_url: &str, project_dir: &str) -> Self {
        Snapshot {
            client_id: client_id.to_string(),
            server_url: server_url.to_string(),
            project_dir: project_dir.to_string(),
            version: 0,
            applied_seq: 0,
            applied_event_seq: 0,
            event_push_cursor: 0,
            offline: false,
            files: BTreeMap::new(),
            pending_batches: BTreeMap::new(),
            conflicts: Vec::new(),
        }
    }
}

pub struct StateStore {
    pub dir: PathBuf,
}

impl StateStore {
    pub fn new(dir: &Path) -> std::io::Result<Self> {
        fs::create_dir_all(dir)?;
        fs::create_dir_all(dir.join("events"))?;
        Ok(StateStore { dir: dir.to_path_buf() })
    }

    /// Exclusive access to one state directory: only one process (the
    /// daemon, or one one-shot CLI) may own it at a time. A stale lock
    /// from a killed process is reclaimed after checking that the PID
    /// is no longer alive.
    pub fn acquire_lock(&self, client_id: &str) -> Result<(), String> {
        let path = self.dir.join(".lock");
        match fs::read(&path) {
            Ok(bytes) => {
                let pid: u32 = String::from_utf8_lossy(&bytes)
                    .trim()
                    .split('|')
                    .next()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0);
                if pid > 0 && pid_alive(pid) {
                    return Err(format!(
                        "state dir {} is locked by PID {} (another daemon or CLI is using it)",
                        self.dir.display(),
                        pid
                    ));
                }
                // Stale lock from a dead process: take it over.
            }
            Err(_) => {}
        }
        let content = format!("{}|{}", std::process::id(), client_id);
        let _ = fs::remove_file(&path);
        match fs::write(&path, content.as_bytes()) {
            Ok(_) => Ok(()),
            Err(e) => Err(format!("cannot write lock: {}", e)),
        }
    }

    pub fn release_lock(&self) {
        let _ = fs::remove_file(self.dir.join(".lock"));
    }

    fn snapshot_path(&self) -> PathBuf {
        self.dir.join("snapshot.json")
    }

    pub fn load(&self, client_id: &str, server_url: &str, project_dir: &str) -> Snapshot {
        match fs::read(self.snapshot_path()) {
            Ok(bytes) => match serde_json::from_slice::<Snapshot>(&bytes) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("warning: snapshot unreadable ({}), starting fresh", e);
                    Snapshot::new(client_id, server_url, project_dir)
                }
            },
            Err(_) => Snapshot::new(client_id, server_url, project_dir),
        }
    }

    pub fn save(&self, snap: &Snapshot) -> std::io::Result<()> {
        let path = self.snapshot_path();
        let tmp = path.with_extension("json.tmp");
        let data = serde_json::to_vec_pretty(snap).expect("serialize snapshot");
        atomic_write(&tmp, &path, &data)
    }

    // ----- event library -----

    fn events_path(&self) -> PathBuf {
        self.dir.join("events").join("events.jsonl")
    }

    /// Append events (already deduplicated against the library) and
    /// return the resulting line count.
    pub fn append_events(&self, events: &[Value]) -> std::io::Result<i64> {
        use std::io::Write;
        let path = self.events_path();
        let mut f = fs::OpenOptions::new().create(true).append(true).open(&path)?;
        let mut bytes = Vec::new();
        for ev in events {
            let mut line = serde_json::to_vec(ev).expect("serialize event");
            line.push(b'\n');
            bytes.extend_from_slice(&line);
        }
        f.write_all(&bytes)?;
        f.sync_all()?;
        drop(f);
        Ok(self.count_events()?)
    }

    /// Load all events from the library, in arrival order.
    pub fn read_events(&self) -> std::io::Result<Vec<Value>> {
        let path = self.events_path();
        if !path.exists() {
            return Ok(Vec::new());
        }
        let data = fs::read(&path)?;
        let mut out = Vec::new();
        for line in data.split(|&b| b == b'\n') {
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_slice::<Value>(line) {
                out.push(v);
            }
        }
        Ok(out)
    }

    pub fn count_events(&self) -> std::io::Result<i64> {
        Ok(self.read_events()?.len() as i64)
    }

    /// event_ids currently in the library (for dedup on import).
    pub fn event_ids(&self) -> std::io::Result<std::collections::HashSet<String>> {
        Ok(self
            .read_events()?
            .iter()
            .filter_map(|v| v.get("event_id").and_then(|x| x.as_str()).map(|s| s.to_string()))
            .collect())
    }

    /// Append raw text lines to the replay log (observability of read-only replays).
    pub fn append_replay_log(&self, record: &Value) -> std::io::Result<()> {
        use std::io::Write;
        let path = self.dir.join("events").join("replay.log");
        let mut f = fs::OpenOptions::new().create(true).append(true).open(&path)?;
        let mut line = serde_json::to_vec(record).expect("serialize replay record");
        line.push(b'\n');
        f.write_all(&line)?;
        f.sync_all()?;
        Ok(())
    }
}

pub fn atomic_write(tmp: &Path, dst: &Path, data: &[u8]) -> std::io::Result<()> {
    fs::write(tmp, data)?;
    fs::rename(tmp, dst)?;
    Ok(())
}

pub fn now_rfc3339() -> String {
    // std has no date formatting; produce a sortable UTC timestamp by hand.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = secs / 86400;
    let rem = secs % 86400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (y, mo, d) = civil_from_days(days as i64);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", y, mo, d, h, m, s)
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    // Howard Hinnant's algorithm.
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Generate a reasonably unique batch id without external crates.
pub fn new_batch_id(client: &str) -> String {
    use sha2::{Digest, Sha256};
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let material = format!("{}|{}|{}|{}|{}", client, nanos, pid, n, nanos.rotate_left(17));
    let mut hasher = Sha256::new();
    hasher.update(material.as_bytes());
    let hex: String = hasher
        .finalize()
        .iter()
        .take(4)
        .map(|b| format!("{:02x}", b))
        .collect();
    let ms = (nanos / 1_000_000) as u64;
    format!("batch-{}-{}", ms, hex)
}

pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect()
}

/// Is a Windows process with this PID still running? Uses tasklist,
/// which ships with every Windows installation.
fn pid_alive(pid: u32) -> bool {
    let out = std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {}", pid), "/NH", "/FO", "CSV"])
        .output();
    match out {
        Ok(o) => {
            let s = String::from_utf8_lossy(&o.stdout);
            // Filtered tasklist prints "INFO: ..." when there is no match.
            s.contains(&pid.to_string()) && !s.contains("INFO:")
        }
        Err(_) => false,
    }
}
