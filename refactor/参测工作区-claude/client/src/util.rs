use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine as _;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Write};
use std::path::{Component, Path};
use std::time::{SystemTime, UNIX_EPOCH};

pub fn now_ms() -> String {
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    ms.to_string()
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    let out = h.finalize();
    let mut s = String::with_capacity(out.len() * 2);
    for b in out {
        s.push_str(&format!("{:02x}", b));
    }
    s
}

pub fn b64_encode(bytes: &[u8]) -> String {
    B64.encode(bytes)
}

pub fn b64_decode(s: &str) -> Result<Vec<u8>, String> {
    B64.decode(s.as_bytes()).map_err(|e| e.to_string())
}

pub fn logline(msg: &str) {
    let mut out = io::stdout().lock();
    let _ = writeln!(out, "[{}] {}", now_ms(), msg);
    let _ = out.flush();
}

/// Write bytes to `path` via a temp file in the same directory plus rename.
pub fn atomic_write(path: &Path, bytes: &[u8]) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("file");
    let tmp = path.with_file_name(format!(
        ".sync-tmp-{}-{}-{}",
        std::process::id(),
        now_ms(),
        name
    ));
    {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    match fs::rename(&tmp, path) {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = fs::remove_file(&tmp);
            Err(e)
        }
    }
}

fn rel_from(root: &Path, full: &Path) -> String {
    let rel = full.strip_prefix(root).unwrap_or(full);
    let mut parts = Vec::new();
    for c in rel.components() {
        if let Component::Normal(os) = c {
            parts.push(os.to_string_lossy().to_string());
        }
    }
    parts.join("/")
}

/// Scan all regular files under `root`. Keys are project-relative with `/`.
pub fn scan_project(root: &Path) -> io::Result<BTreeMap<String, Vec<u8>>> {
    let mut out = BTreeMap::new();
    if !root.exists() {
        return Ok(out);
    }
    walk(root, root, &mut out)?;
    Ok(out)
}

fn walk(root: &Path, dir: &Path, out: &mut BTreeMap<String, Vec<u8>>) -> io::Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() {
            walk(root, &entry.path(), out)?;
        } else if file_type.is_file() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with(".sync-tmp-") {
                continue;
            }
            let full = entry.path();
            let bytes = fs::read(&full)?;
            out.insert(rel_from(root, &full), bytes);
        }
    }
    Ok(())
}

pub fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

pub fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hi = (bytes[i + 1] as char).to_digit(16);
                let lo = (bytes[i + 2] as char).to_digit(16);
                if let (Some(hi), Some(lo)) = (hi, lo) {
                    out.push((hi * 16 + lo) as u8);
                    i += 3;
                    continue;
                }
                out.push(b'%');
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).to_string()
}

pub fn parse_query(query: &str) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for pair in query.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        out.insert(percent_decode(k), percent_decode(v));
    }
    out
}

/// Create a fresh conflict-copy path that does not overwrite existing content.
pub fn choose_conflict_path(
    root: &Path,
    rel: &str,
    device: &str,
    server_version: u64,
    local_hash: &str,
) -> String {
    let safe_device: String = device
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect();
    let short = if local_hash.len() >= 8 {
        &local_hash[..8]
    } else {
        local_hash
    };
    let base = format!("{}.conflict-{}-v{}", rel, safe_device, server_version);
    let mut candidate = base.clone();
    let mut n = 0;
    loop {
        let full = root.join(&candidate);
        if !full.exists() {
            return candidate;
        }
        if let Ok(existing) = fs::read(&full) {
            if sha256_hex(&existing) == local_hash {
                return candidate;
            }
        }
        n += 1;
        candidate = format!("{}-{}", base, if n == 1 { short.to_string() } else { format!("{}-{}", short, n) });
    }
}
