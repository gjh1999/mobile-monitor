use crate::util::atomic_write;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct BaseEntry {
    pub version: u64,
    pub deleted: bool,
    #[serde(default)]
    pub hash: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Inflight {
    pub op_id: String,
    pub path: String,
    pub base_version: u64,
    pub deleted: bool,
    pub hash: String,
    pub started_at: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ReceiptLogEntry {
    pub op_id: String,
    pub path: String,
    pub base_version: u64,
    pub deleted: bool,
    pub hash: String,
    pub dropped_at: String,
    #[serde(default)]
    pub resumed_at: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub resolution: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ClientState {
    pub device_id: String,
    #[serde(default)]
    pub base: BTreeMap<String, BaseEntry>,
    #[serde(default)]
    pub last_seen_version: u64,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub inflight: Option<Inflight>,
    #[serde(default)]
    pub awaiting: Option<Inflight>,
    #[serde(default)]
    pub receipt_log: Vec<ReceiptLogEntry>,
    #[serde(default)]
    pub conflicts: Vec<Value>,
    #[serde(default)]
    pub imports: Vec<Value>,
    #[serde(default)]
    pub pending_imports: Vec<Value>,
    #[serde(default)]
    pub event_cache: BTreeMap<String, Vec<Value>>,
    #[serde(default)]
    pub tool_dispatch_attempts: u64,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub last_sync_at: Option<String>,
    #[serde(default)]
    pub sync_errors: Vec<String>,
}

impl ClientState {
    pub fn load_or_new(state_path: &Path, device: &str) -> io::Result<Self> {
        if state_path.exists() {
            let data = fs::read(state_path)?;
            let mut st: ClientState =
                serde_json::from_slice(&data).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
            if !st.device_id.is_empty() && st.device_id != device {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "state directory belongs to device {:?}, not {:?}",
                        st.device_id, device
                    ),
                ));
            }
            st.device_id = device.to_string();
            if st.created_at.is_empty() {
                st.created_at = crate::util::now_ms();
            }
            Ok(st)
        } else {
            Ok(ClientState {
                device_id: device.to_string(),
                created_at: crate::util::now_ms(),
                ..Default::default()
            })
        }
    }

    pub fn save(&self, state_path: &Path) -> io::Result<()> {
        let data = serde_json::to_vec_pretty(self)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        atomic_write(state_path, &data)
    }
}

pub struct Core {
    pub state: ClientState,
    pub state_path: PathBuf,
    pub project: PathBuf,
    pub server: String,
    pub last_error: Option<String>,
    pub last_pending: Vec<Value>,
    pub server_seq: u64,
    pub last_history_source: Option<String>,
}

impl Core {
    pub fn save(&mut self) {
        if let Err(e) = self.state.save(&self.state_path) {
            crate::util::logline(&format!("ERROR save state: {}", e));
            self.last_error = Some(format!("state save failed: {}", e));
        }
    }

    pub fn record_error(&mut self, msg: String) {
        crate::util::logline(&format!("ERROR {}", msg));
        self.last_error = Some(msg);
        self.state.sync_errors.push(crate::util::now_ms());
        if self.state.sync_errors.len() > 50 {
            let excess = self.state.sync_errors.len() - 50;
            self.state.sync_errors.drain(0..excess);
        }
        self.save();
    }

    pub fn project_path(&self, rel: &str) -> PathBuf {
        let mut p = self.project.clone();
        for part in rel.split('/') {
            if !part.is_empty() {
                p.push(part);
            }
        }
        p
    }

    pub fn receipt_resolved(&mut self, op_id: &str, resolution: &str) {
        for entry in self.state.receipt_log.iter_mut().rev() {
            if entry.op_id == op_id && entry.resolution.is_none() {
                entry.resolution = Some(resolution.to_string());
                break;
            }
        }
    }

    /// Persist the blocked-receipt state and permit a retry (used by /retry and restart).
    pub fn release_awaiting(&mut self, reason: &str) -> bool {
        let mut moved = false;
        if let Some(a) = self.state.awaiting.take() {
            self.state.receipt_log.push(ReceiptLogEntry {
                op_id: a.op_id,
                path: a.path,
                base_version: a.base_version,
                deleted: a.deleted,
                hash: a.hash,
                dropped_at: a.started_at,
                resumed_at: crate::util::now_ms(),
                reason: reason.to_string(),
                resolution: None,
            });
            moved = true;
        }
        if let Some(a) = self.state.inflight.take() {
            self.state.receipt_log.push(ReceiptLogEntry {
                op_id: a.op_id,
                path: a.path,
                base_version: a.base_version,
                deleted: a.deleted,
                hash: a.hash,
                dropped_at: a.started_at,
                resumed_at: crate::util::now_ms(),
                reason: format!("{}-resume-inflight", reason),
                resolution: None,
            });
            moved = true;
        }
        if self.state.receipt_log.len() > 100 {
            let excess = self.state.receipt_log.len() - 100;
            self.state.receipt_log.drain(0..excess);
        }
        if moved {
            self.save();
        }
        moved
    }
}
