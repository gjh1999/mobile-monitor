use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process;

// ===========================================================================
// JSON 解析 / 序列化（标准库手写，覆盖本题 payload 所需）
// ===========================================================================

#[derive(Clone, Debug, PartialEq)]
enum Json {
    Null,
    Bool(bool),
    Num(String),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }
    fn as_i64(&self) -> Option<i64> {
        match self {
            Json::Num(s) => s.parse::<i64>().ok(),
            _ => None,
        }
    }
    fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(kvs) => kvs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
    fn get_str(&self, key: &str) -> Option<String> {
        self.get(key).and_then(|v| v.as_str()).map(|s| s.to_string())
    }
}

struct Parser<'a> {
    s: &'a [u8],
    i: usize,
}

fn parse_json(text: &str) -> Result<Json, String> {
    let mut p = Parser { s: text.as_bytes(), i: 0 };
    let v = p.parse_value()?;
    p.skip_ws();
    if p.i != p.s.len() {
        return Err(format!("trailing bytes at {}", p.i));
    }
    Ok(v)
}

impl<'a> Parser<'a> {
    fn skip_ws(&mut self) {
        while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }
    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }
    fn parse_value(&mut self) -> Result<Json, String> {
        self.skip_ws();
        match self.peek() {
            Some(b'{') => self.parse_obj(),
            Some(b'[') => self.parse_arr(),
            Some(b'"') => Ok(Json::Str(self.parse_string()?)),
            Some(b't') => {
                self.expect_lit("true")?;
                Ok(Json::Bool(true))
            }
            Some(b'f') => {
                self.expect_lit("false")?;
                Ok(Json::Bool(false))
            }
            Some(b'n') => {
                self.expect_lit("null")?;
                Ok(Json::Null)
            }
            Some(c) if c == b'-' || c.is_ascii_digit() => Ok(Json::Num(self.parse_number()?)),
            Some(c) => Err(format!("unexpected byte {} at {}", c as char, self.i)),
            None => Err("unexpected eof".into()),
        }
    }
    fn expect_lit(&mut self, lit: &str) -> Result<(), String> {
        if self.s[self.i..].starts_with(lit.as_bytes()) {
            self.i += lit.len();
            Ok(())
        } else {
            Err(format!("expected {} at {}", lit, self.i))
        }
    }
    fn parse_number(&mut self) -> Result<String, String> {
        let start = self.i;
        if self.peek() == Some(b'-') {
            self.i += 1;
        }
        while self.i < self.s.len() {
            let c = self.s[self.i];
            if c.is_ascii_digit() || c == b'.' || c == b'e' || c == b'E' || c == b'+' || c == b'-' {
                self.i += 1;
            } else {
                break;
            }
        }
        Ok(String::from_utf8_lossy(&self.s[start..self.i]).into_owned())
    }
    fn parse_string(&mut self) -> Result<String, String> {
        if self.peek() != Some(b'"') {
            return Err(format!("expected string at {}", self.i));
        }
        self.i += 1;
        let mut out = String::new();
        loop {
            if self.i >= self.s.len() {
                return Err("unterminated string".into());
            }
            let c = self.s[self.i];
            if c == b'"' {
                self.i += 1;
                break;
            }
            if c == b'\\' {
                self.i += 1;
                if self.i >= self.s.len() {
                    return Err("bad escape".into());
                }
                let e = self.s[self.i];
                self.i += 1;
                match e {
                    b'"' => out.push('"'),
                    b'\\' => out.push('\\'),
                    b'/' => out.push('/'),
                    b'b' => out.push('\u{8}'),
                    b'f' => out.push('\u{c}'),
                    b'n' => out.push('\n'),
                    b'r' => out.push('\r'),
                    b't' => out.push('\t'),
                    b'u' => {
                        let hex = std::str::from_utf8(&self.s[self.i..self.i + 4])
                            .map_err(|_| "bad \\u".to_string())?;
                        self.i += 4;
                        let cp = u32::from_str_radix(hex, 16).map_err(|_| "bad \\u".to_string())?;
                        out.push(char::from_u32(cp).unwrap_or('\u{fffd}'));
                    }
                    _ => return Err(format!("bad escape \\{}", e as char)),
                }
            } else {
                // UTF-8 多字节直接按字节累积，最终按 lossy 还原
                let start = self.i;
                while self.i < self.s.len() && self.s[self.i] != b'"' && self.s[self.i] != b'\\' {
                    self.i += 1;
                }
                out.push_str(&String::from_utf8_lossy(&self.s[start..self.i]));
            }
        }
        Ok(out)
    }
    fn parse_arr(&mut self) -> Result<Json, String> {
        self.i += 1; // [
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b']') {
            self.i += 1;
            return Ok(Json::Arr(items));
        }
        loop {
            items.push(self.parse_value()?);
            self.skip_ws();
            match self.peek() {
                Some(b',') => {
                    self.i += 1;
                }
                Some(b']') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(format!("expected , or ] at {}", self.i)),
            }
        }
        Ok(Json::Arr(items))
    }
    fn parse_obj(&mut self) -> Result<Json, String> {
        self.i += 1; // {
        let mut kvs = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b'}') {
            self.i += 1;
            return Ok(Json::Obj(kvs));
        }
        loop {
            self.skip_ws();
            let k = self.parse_string()?;
            self.skip_ws();
            if self.peek() != Some(b':') {
                return Err(format!("expected : at {}", self.i));
            }
            self.i += 1;
            let v = self.parse_value()?;
            kvs.push((k, v));
            self.skip_ws();
            match self.peek() {
                Some(b',') => {
                    self.i += 1;
                }
                Some(b'}') => {
                    self.i += 1;
                    break;
                }
                _ => return Err(format!("expected , or }} at {}", self.i)),
            }
        }
        Ok(Json::Obj(kvs))
    }
}

fn escape_string(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn json_to_string(v: &Json) -> String {
    match v {
        Json::Null => "null".to_string(),
        Json::Bool(b) => b.to_string(),
        Json::Num(n) => n.clone(),
        Json::Str(s) => escape_string(s),
        Json::Arr(items) => {
            let inner: Vec<String> = items.iter().map(json_to_string).collect();
            format!("[{}]", inner.join(","))
        }
        Json::Obj(kvs) => {
            let inner: Vec<String> = kvs
                .iter()
                .map(|(k, val)| format!("{}:{}", escape_string(k), json_to_string(val)))
                .collect();
            format!("{{{}}}", inner.join(","))
        }
    }
}

// ===========================================================================
// base64 解码（标准字母表 + padding）
// ===========================================================================

fn b64_value(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

fn base64_decode(s: &str) -> Result<Vec<u8>, String> {
    let mut acc: u32 = 0;
    let mut bits = 0;
    let mut out = Vec::new();
    for &c in s.as_bytes() {
        if c == b'=' {
            break;
        }
        if c == b'\n' || c == b'\r' || c == b' ' || c == b'\t' {
            continue;
        }
        let v = b64_value(c).ok_or_else(|| format!("bad base64 char {}", c as char))?;
        acc = (acc << 6) | v as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Ok(out)
}

// ===========================================================================
// FNV-1a 64
// ===========================================================================

fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &b in data {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn blob_id(data: &[u8]) -> String {
    format!("{:016x}", fnv1a64(data))
}

fn op_id(kind: &str, path: &str, tag: &str) -> String {
    format!("{:016x}", fnv1a64(format!("{}\x00{}\x00{}", kind, path, tag).as_bytes()))
}

// ===========================================================================
// HTTP 客户端（本机回环）
// ===========================================================================

fn http_request(host: &str, port: u16, method: &str, path: &str, body: Option<&[u8]>) -> Result<String, String> {
    let mut stream = TcpStream::connect((host, port))
        .map_err(|e| format!("connect {}:{} failed: {}", host, port, e))?;
    let mut req = format!("{} {} HTTP/1.1\r\nHost: {}:{}\r\n", method, path, host, port);
    match body {
        Some(b) => {
            req.push_str("Content-Type: application/json\r\n");
            req.push_str(&format!("Content-Length: {}\r\n", b.len()));
            req.push_str("Connection: close\r\n\r\n");
            stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
            stream.write_all(b).map_err(|e| e.to_string())?;
        }
        None => {
            req.push_str("Connection: close\r\n\r\n");
            stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
        }
    }
    let mut resp = Vec::new();
    stream.read_to_end(&mut resp).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&resp).into_owned();
    // 分离头与体
    let idx = text.find("\r\n\r\n").ok_or_else(|| "no header terminator".to_string())?;
    let head = &text[..idx];
    let body_bytes = &resp[idx + 4..];
    let status = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .unwrap_or("0");
    let status: u16 = status.parse().unwrap_or(0);
    let mut content_length: Option<usize> = None;
    for line in head.lines() {
        if let Some(v) = line.strip_prefix("Content-Length:") {
            content_length = v.trim().parse::<usize>().ok();
        }
    }
    let body = match content_length {
        Some(n) => &body_bytes[..body_bytes.len().min(n)],
        None => body_bytes,
    };
    if !(200..300).contains(&status) {
        return Err(format!("HTTP {}: {}", status, String::from_utf8_lossy(body)));
    }
    Ok(String::from_utf8_lossy(body).into_owned())
}

// ===========================================================================
// 配置与状态
// ===========================================================================

struct Config {
    id: String,
    project_dir: PathBuf,
    state_dir: PathBuf,
    host: String,
    port: u16,
}

fn rel_of(root: &Path, p: &Path) -> String {
    let r = p.strip_prefix(root).unwrap_or(p);
    r.to_string_lossy().replace('\\', "/")
}

fn scan_project(root: &Path) -> BTreeMap<String, String> {
    let mut map = BTreeMap::new();
    if !root.exists() {
        return map;
    }
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        if let Ok(rd) = fs::read_dir(&dir) {
            for ent in rd.flatten() {
                let p = ent.path();
                if p.is_dir() {
                    stack.push(p);
                } else if p.is_file() {
                    if let Ok(data) = fs::read(&p) {
                        map.insert(rel_of(root, &p), blob_id(&data));
                    }
                }
            }
        }
    }
    map
}

fn baseline_path(cfg: &Config) -> PathBuf {
    cfg.state_dir.join("baseline.tsv")
}
fn seq_path(cfg: &Config) -> PathBuf {
    cfg.state_dir.join("seq.txt")
}
fn online_path(cfg: &Config) -> PathBuf {
    cfg.state_dir.join("online.txt")
}
fn events_path(cfg: &Config) -> PathBuf {
    cfg.state_dir.join("events.jsonl")
}
fn pending_path(cfg: &Config) -> PathBuf {
    cfg.state_dir.join("pending-events.jsonl")
}

fn load_baseline(cfg: &Config) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    if let Ok(txt) = fs::read_to_string(baseline_path(cfg)) {
        for line in txt.lines() {
            if let Some((p, b)) = line.split_once('\t') {
                m.insert(p.to_string(), b.to_string());
            }
        }
    }
    m
}
fn save_baseline(cfg: &Config, m: &BTreeMap<String, String>) {
    let mut out = String::new();
    for (p, b) in m {
        out.push_str(p);
        out.push('\t');
        out.push_str(b);
        out.push('\n');
    }
    let _ = fs::write(baseline_path(cfg), out);
}
fn load_seq(cfg: &Config) -> i64 {
    fs::read_to_string(seq_path(cfg))
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok())
        .unwrap_or(0)
}
fn save_seq(cfg: &Config, v: i64) {
    let _ = fs::write(seq_path(cfg), v.to_string());
}
fn load_online(cfg: &Config) -> bool {
    fs::read_to_string(online_path(cfg))
        .map(|s| s.trim() == "1")
        .unwrap_or(true)
}
fn save_online(cfg: &Config, v: bool) {
    let _ = fs::write(online_path(cfg), if v { "1" } else { "0" });
}

fn load_event_ids(cfg: &Config) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    if let Ok(txt) = fs::read_to_string(events_path(cfg)) {
        for line in txt.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = parse_json(line) {
                if let Some(id) = v.get_str("event_id") {
                    set.insert(id);
                }
            }
        }
    }
    set
}

fn append_events(cfg: &Config, events: &[Json]) {
    if events.is_empty() {
        return;
    }
    let mut f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(events_path(cfg))
        .unwrap();
    for e in events {
        writeln!(f, "{}", json_to_string(e)).unwrap();
    }
}

// ===========================================================================
// 协议对象
// ===========================================================================

#[derive(Clone)]
struct FileOp {
    op_id: String,
    path: String,
    kind: String, // put | delete
    blob: String,
    base: String,
    content: Vec<u8>,
}

fn b64enc(data: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    let mut i = 0;
    while i < data.len() {
        let b0 = data[i];
        let b1 = if i + 1 < data.len() { data[i + 1] } else { 0 };
        let b2 = if i + 2 < data.len() { data[i + 2] } else { 0 };
        out.push(T[(b0 >> 2) as usize] as char);
        out.push(T[(((b0 & 0x3) << 4) | (b1 >> 4)) as usize] as char);
        let c2 = if i + 1 < data.len() {
            T[(((b1 & 0xf) << 2) | (b2 >> 6)) as usize] as char
        } else {
            '='
        };
        out.push(c2);
        let c3 = if i + 2 < data.len() {
            T[(b2 & 0x3f) as usize] as char
        } else {
            '='
        };
        out.push(c3);
        i += 3;
    }
    out
}

fn build_file_ops(cfg: &Config, local: &BTreeMap<String, String>, baseline: &BTreeMap<String, String>) -> Vec<FileOp> {
    let mut ops = Vec::new();
    // 删除：baseline 有、local 无
    for (p, b) in baseline {
        if !local.contains_key(p) {
            ops.push(FileOp {
                op_id: op_id("delete", p, b),
                path: p.clone(),
                kind: "delete".into(),
                blob: String::new(),
                base: b.clone(),
                content: Vec::new(),
            });
        }
    }
    // 新增 / 修改
    for (p, b) in local {
        match baseline.get(p) {
            None => {
                let data = fs::read(cfg.project_dir.join(p.replace('/', "\\"))).unwrap_or_default();
                ops.push(FileOp {
                    op_id: op_id("put", p, b),
                    path: p.clone(),
                    kind: "put".into(),
                    blob: b.clone(),
                    base: String::new(),
                    content: data,
                });
            }
            Some(old) if old != b => {
                let data = fs::read(cfg.project_dir.join(p.replace('/', "\\"))).unwrap_or_default();
                ops.push(FileOp {
                    op_id: op_id("put", p, b),
                    path: p.clone(),
                    kind: "put".into(),
                    blob: b.clone(),
                    base: old.clone(),
                    content: data,
                });
            }
            _ => {}
        }
    }
    ops
}

fn push_ops(cfg: &Config, ops: &[FileOp], events: &[Json]) -> Result<Json, String> {
    let mut files = Vec::new();
    for op in ops {
        files.push(format!(
            "{{\"op_id\":\"{}\",\"path\":\"{}\",\"kind\":\"{}\",\"blob\":\"{}\",\"base\":\"{}\",\"content\":\"{}\"}}",
            op.op_id,
            op.path,
            op.kind,
            op.blob,
            op.base,
            b64enc(&op.content)
        ));
    }
    let ev: Vec<String> = events.iter().map(|e| json_to_string(e)).collect();
    let body = format!("{{\"client\":\"{}\",\"files\":[{}],\"events\":[{}]}}", cfg.id, files.join(","), ev.join(","));
    let path = "/push";
    let text = http_request(&cfg.host, cfg.port, "POST", path, Some(body.as_bytes()))?;
    parse_json(&text)
}

fn pull(cfg: &Config, after: i64) -> Result<Json, String> {
    let path = format!("/pull?after={}", after);
    let text = http_request(&cfg.host, cfg.port, "GET", &path, None)?;
    parse_json(&text)
}

fn write_file_ensuring_dir(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, data)
}

// 应用 pull 结果：reconcile 文件 + 追加事件，返回 (冲突描述, 新增事件数)
fn apply_pull(cfg: &Config, resp: &Json, baseline: &mut BTreeMap<String, String>) -> (Vec<String>, usize) {
    let mut notes: Vec<String> = Vec::new();
    let mut new_events = 0;

    // 事件
    let known = load_event_ids(cfg);
    if let Some(Json::Arr(evs)) = resp.get("events") {
        let mut to_add = Vec::new();
        for e in evs {
            if let Some(id) = e.get_str("event_id") {
                if !known.contains(&id) {
                    to_add.push(e.clone());
                }
            }
        }
        new_events = to_add.len();
        append_events(cfg, &to_add);
    }

    // 文件 manifest / blobs / deleted
    let mut manifest: BTreeMap<String, String> = BTreeMap::new();
    if let Some(Json::Obj(kvs)) = resp.get("manifest") {
        for (k, v) in kvs {
            if let Some(s) = v.as_str() {
                manifest.insert(k.clone(), s.to_string());
            }
        }
    }
    let mut blobs: BTreeMap<String, Vec<u8>> = BTreeMap::new();
    if let Some(Json::Obj(kvs)) = resp.get("blobs") {
        for (k, v) in kvs {
            if let Some(s) = v.as_str() {
                if let Ok(b) = base64_decode(s) {
                    blobs.insert(k.clone(), b);
                }
            }
        }
    }

    let local = scan_project(&cfg.project_dir);

    // 应用 manifest（远端当前状态）
    for (p, target) in &manifest {
        let local_blob = local.get(p);
        let base_blob = baseline.get(p);
        let dirty = match (local_blob, base_blob) {
            (Some(l), Some(b)) => l != b,
            (Some(_), None) => true, // 本地新增文件
            _ => false,
        };
        let target_data = blobs.get(target).cloned().unwrap_or_default();
        if dirty && local_blob.is_some() && local_blob != Some(target) {
            // 冲突：保留本地内容为冲突副本
            if let Some(lb) = local_blob {
                if let Ok(data) = fs::read(cfg.project_dir.join(p.replace('/', "\\"))) {
                    let cpath = format!("{}.conflict-{}", p, &lb[..8.min(lb.len())]);
                    let _ = write_file_ensuring_dir(&cfg.project_dir.join(cpath.replace('/', "\\")), &data);
                    notes.push(format!("conflict(fork) {}: kept local as {}", p, cpath));
                }
            }
        }
        if local_blob != Some(target) {
            let _ = write_file_ensuring_dir(&cfg.project_dir.join(p.replace('/', "\\")), &target_data);
        }
        baseline.insert(p.clone(), target.clone());
    }

    // 删除：baseline 有、manifest 无
    for (p, base_blob) in baseline.clone() {
        if !manifest.contains_key(&p) {
            if let Some(lb) = local.get(&p).cloned() {
                let dirty = &lb != &base_blob;
                if dirty {
                    // 删除与修改相遇：保留修改内容，删除规范路径
                    if let Ok(data) = fs::read(cfg.project_dir.join(p.replace('/', "\\"))) {
                        let short = &lb[..8.min(lb.len())];
                        let cpath = format!("{}.conflict-{}", p, short);
                        let _ = write_file_ensuring_dir(&cfg.project_dir.join(cpath.replace('/', "\\")), &data);
                        notes.push(format!("conflict(delete) {}: kept local edit as {}", p, cpath));
                    }
                }
                let _ = fs::remove_file(cfg.project_dir.join(p.replace('/', "\\")));
            }
            baseline.remove(&p);
        }
    }

    (notes, new_events)
}

// 推送本地全部事件（服务端按 event_id 去重）
fn push_all_events(cfg: &Config) -> Result<i64, String> {
    let known = load_event_ids(cfg);
    if known.is_empty() {
        return Ok(0);
    }
    let mut events = Vec::new();
    if let Ok(txt) = fs::read_to_string(events_path(cfg)) {
        for line in txt.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = parse_json(line) {
                events.push(v);
            }
        }
    }
    let resp = push_ops(cfg, &[], &events)?;
    Ok(resp.get("seq").and_then(|v| v.as_i64()).unwrap_or(0))
}

// ===========================================================================
// 投影 / 历史回放（只读，不派发工具）
// ===========================================================================

struct Item {
    seq: i64,
    kind: String, // user | call | result | assistant
    item_id: String,
    call_id: String,
    body: String, // 展示文本
}

fn build_history(cfg: &Config) -> BTreeMap<(String, String), Vec<Item>> {
    // key: (thread_id, turn_id) -> items（按 seq 排序）
    let mut turns: BTreeMap<(String, String), Vec<Item>> = BTreeMap::new();
    let mut deltas: BTreeMap<(String, String, String), (i64, String)> = BTreeMap::new(); // (thread,turn,item) -> (minseq, text)
    let mut done_items: BTreeSet<(String, String, String)> = BTreeSet::new();

    if let Ok(txt) = fs::read_to_string(events_path(cfg)) {
        for line in txt.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let v = match parse_json(line) {
                Ok(v) => v,
                Err(_) => continue,
            };
            let thread = v.get_str("thread_id").unwrap_or_default();
            let turn = v.get_str("turn_id").unwrap_or_default();
            let seq = v.get("seq").and_then(|x| x.as_i64()).unwrap_or(0);
            let item = v.get_str("item_id").unwrap_or_default();
            let ty = v.get_str("type").unwrap_or_default();
            let key = (thread.clone(), turn.clone());
            match ty.as_str() {
                "user.message" => {
                    let text = v.get_str("text").unwrap_or_default();
                    turns.entry(key).or_default().push(Item {
                        seq,
                        kind: "user".into(),
                        item_id: item,
                        call_id: String::new(),
                        body: text,
                    });
                }
                "tool.call" => {
                    let call = v.get_str("call_id").unwrap_or_default();
                    let name = v.get_str("name").unwrap_or_default();
                    let args = v.get("arguments").map(json_to_string).unwrap_or_default();
                    turns.entry(key).or_default().push(Item {
                        seq,
                        kind: "call".into(),
                        item_id: item,
                        call_id: call,
                        body: format!("{} {}", name, args),
                    });
                }
                "tool.result" => {
                    let call = v.get_str("call_id").unwrap_or_default();
                    let result = v.get("result").map(json_to_string).unwrap_or_default();
                    turns.entry(key).or_default().push(Item {
                        seq,
                        kind: "result".into(),
                        item_id: item,
                        call_id: call,
                        body: result,
                    });
                }
                "assistant.delta" => {
                    let text = v.get_str("text").unwrap_or_default();
                    let dkey = (thread.clone(), turn.clone(), item.clone());
                    let entry = deltas.entry(dkey).or_insert((seq, String::new()));
                    if entry.0 == 0 || seq < entry.0 {
                        entry.0 = seq;
                    }
                    entry.1.push_str(&text);
                }
                "assistant.done" => {
                    done_items.insert((thread.clone(), turn.clone(), item.clone()));
                }
                _ => {}
            }
        }
    }

    // 组装 assistant 条目
    for ((thread, turn, item), (seq, text)) in deltas {
        let key = (thread, turn);
        let done = done_items.contains(&(key.0.clone(), key.1.clone(), item.clone()));
        turns.entry(key).or_default().push(Item {
            seq,
            kind: "assistant".into(),
            item_id: item,
            call_id: String::new(),
            body: if done { format!("{} [done]", text) } else { text },
        });
    }

    // 每轮按 seq 排序
    for items in turns.values_mut() {
        items.sort_by_key(|it| it.seq);
    }
    turns
}

fn print_history(cfg: &Config) {
    let turns = build_history(cfg);
    for ((thread, turn), items) in &turns {
        println!("== thread:{} turn:{} ==", thread, turn);
        for it in items {
            match it.kind.as_str() {
                "user" => println!("  [user.message {}] {}", it.item_id, it.body),
                "call" => println!("  [tool.call {}] {}", it.call_id, it.body),
                "result" => println!("  [tool.result {}] {}", it.call_id, it.body),
                "assistant" => println!("  [assistant {}] {}", it.item_id, it.body),
                _ => {}
            }
        }
    }
}

// ===========================================================================
// 命令
// ===========================================================================

fn cmd_sync(cfg: &Config) {
    fs::create_dir_all(&cfg.state_dir).unwrap();
    if !load_online(cfg) {
        let local = scan_project(&cfg.project_dir);
        let baseline = load_baseline(cfg);
        let ops = build_file_ops(cfg, &local, &baseline);
        println!("[{}] offline: {} pending file ops not pushed", cfg.id, ops.len());
        for o in &ops {
            println!("  pending {} {}", o.kind, o.path);
        }
        return;
    }

    let local = scan_project(&cfg.project_dir);
    let mut baseline = load_baseline(cfg);

    // 1) 推送事件（服务端去重）
    let _ = push_all_events(cfg);

    // 2) 推送文件操作
    let ops = build_file_ops(cfg, &local, &baseline);
    if !ops.is_empty() {
        match push_ops(cfg, &ops, &[]) {
            Ok(resp) => {
                if let Some(Json::Arr(results)) = resp.get("results") {
                    let mut by_id: BTreeMap<String, String> = BTreeMap::new();
                    for r in results {
                        let id = r.get_str("op_id").unwrap_or_default();
                        let st = r.get_str("status").unwrap_or_default();
                        by_id.insert(id, st);
                    }
                    for op in &ops {
                        match by_id.get(&op.op_id).map(|s| s.as_str()) {
                            Some("applied") => {
                                if op.kind == "put" {
                                    baseline.insert(op.path.clone(), op.blob.clone());
                                } else {
                                    baseline.remove(&op.path);
                                }
                            }
                            Some("conflict") => {
                                println!("[{}] conflict on {} (kept server version)", cfg.id, op.path);
                            }
                            _ => {}
                        }
                    }
                }
            }
            Err(e) => {
                println!("[{}] push failed: {} (ops kept pending)", cfg.id, e);
            }
        }
    }
    save_baseline(cfg, &baseline);

    // 3) pull
    let after = load_seq(cfg);
    match pull(cfg, after) {
        Ok(resp) => {
            let head = resp.get("head").and_then(|v| v.as_i64()).unwrap_or(after);
            let (notes, n) = apply_pull(cfg, &resp, &mut baseline);
            save_baseline(cfg, &baseline);
            save_seq(cfg, head);
            for nte in &notes {
                println!("[{}] {}", cfg.id, nte);
            }
            let conflicts = resp.get("conflicts").map(|c| match c {
                Json::Arr(a) => a.len(),
                _ => 0,
            });
            println!(
                "[{}] sync done: seq {}->{}, +{} events, {} file op(s), server conflicts={}",
                cfg.id,
                after,
                head,
                n,
                ops.len(),
                conflicts.unwrap_or(0)
            );
        }
        Err(e) => println!("[{}] pull failed: {}", cfg.id, e),
    }
}

fn cmd_status(cfg: &Config) {
    let local = scan_project(&cfg.project_dir);
    let baseline = load_baseline(cfg);
    let ops = build_file_ops(cfg, &local, &baseline);
    let seq = load_seq(cfg);
    let online = load_online(cfg);
    let events = load_event_ids(cfg).len();
    println!("client={} online={} last_seq={} events={}", cfg.id, online, seq, events);
    println!("pending file ops: {}", ops.len());
    for o in &ops {
        println!("  {} {} (base={})", o.kind, o.path, if o.base.is_empty() { "-" } else { &o.base });
    }
    if ops.is_empty() {
        println!("queue settled (no pending file ops)");
    } else {
        println!("queue NOT settled: pending ops listed above");
    }
}

fn cmd_import(cfg: &Config, file: &str, range: Option<(usize, usize)>) {
    let txt = fs::read_to_string(file).unwrap_or_else(|e| {
        eprintln!("read {} failed: {}", file, e);
        process::exit(2);
    });
    let lines: Vec<&str> = txt.lines().collect();
    let (start, end) = range.unwrap_or((1, lines.len().max(1)));
    let known = load_event_ids(cfg);
    let mut to_add: Vec<Json> = Vec::new();
    for (idx, line) in lines.iter().enumerate() {
        let lineno = idx + 1;
        if lineno < start || lineno > end {
            continue;
        }
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match parse_json(line) {
            Ok(v) => {
                if let Some(id) = v.get_str("event_id") {
                    if known.contains(&id) {
                        println!("[{}] skip duplicate event {}", cfg.id, id);
                        continue;
                    }
                }
                to_add.push(v);
            }
            Err(e) => eprintln!("line {}: {}", lineno, e),
        }
    }
    append_events(cfg, &to_add);
    println!("[{}] imported {} event(s) to local store", cfg.id, to_add.len());
    if load_online(cfg) {
        match push_all_events(cfg) {
            Ok(seq) => println!("[{}] events pushed, server head seq={}", cfg.id, seq),
            Err(e) => println!("[{}] push failed (stored locally, retry on sync): {}", cfg.id, e),
        }
    } else {
        println!("[{}] offline: events stored locally, will push on next sync", cfg.id);
    }
}

fn cmd_online(cfg: &Config, online: bool) {
    save_online(cfg, online);
    println!("[{}] online={}", cfg.id, online);
}

fn usage() -> ! {
    eprintln!("usage: sync-client <id> <project-dir> <state-dir> <server-url> <command> [args]");
    eprintln!("  command: sync | status | import-events <file> [start end] | history | offline | online");
    process::exit(2);
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 6 {
        usage();
    }
    let id = args[1].clone();
    let project_dir = PathBuf::from(&args[2]);
    let state_dir = PathBuf::from(&args[3]);
    let server_url = &args[4];
    let cmd = &args[5];

    let (host, port) = parse_url(server_url);
    let cfg = Config {
        id,
        project_dir,
        state_dir,
        host,
        port,
    };
    fs::create_dir_all(&cfg.state_dir).unwrap();

    match cmd.as_str() {
        "sync" => cmd_sync(&cfg),
        "status" => cmd_status(&cfg),
        "history" => print_history(&cfg),
        "import-events" => {
            if args.len() < 7 {
                usage();
            }
            let file = args[6].clone();
            let range = if args.len() >= 9 {
                let s: usize = args[7].parse().unwrap_or(1);
                let e: usize = args[8].parse().unwrap_or(usize::MAX);
                Some((s, e))
            } else {
                None
            };
            cmd_import(&cfg, &file, range);
        }
        "online" => cmd_online(&cfg, true),
        "offline" => cmd_online(&cfg, false),
        _ => usage(),
    }
}

fn parse_url(url: &str) -> (String, u16) {
    // http://host:port
    let trimmed = url.trim_end_matches('/');
    let rest = trimmed.strip_prefix("http://").unwrap_or(trimmed);
    let mut parts = rest.split(':');
    let host = parts.next().unwrap_or("127.0.0.1").to_string();
    let port = parts.next().and_then(|p| p.parse::<u16>().ok()).unwrap_or(8787);
    (host, port)
}
