use crate::state::Core;
use crate::sync;
use crate::util::{logline, now_ms, parse_query};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub struct Shared {
    pub core: Mutex<Core>,
    pub trigger: AtomicBool,
    pub shutdown: AtomicBool,
}

pub fn spawn_control(
    addr: &str,
    shared: Arc<Shared>,
) -> std::io::Result<std::thread::JoinHandle<()>> {
    let listener = TcpListener::bind(addr)?;
    {
        let core = shared.core.lock().unwrap();
        logline(&format!(
            "CONTROL listening={} device={}",
            addr, core.state.device_id
        ));
    }
    let handle = std::thread::spawn(move || {
        for conn in listener.incoming() {
            if shared.shutdown.load(Ordering::SeqCst) {
                break;
            }
            match conn {
                Ok(stream) => handle_conn(stream, &shared),
                Err(_) => continue,
            }
        }
    });
    Ok(handle)
}

fn find_headers_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n").map(|i| i + 4)
}

fn handle_conn(mut stream: TcpStream, shared: &Arc<Shared>) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(15)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));
    let mut buf: Vec<u8> = Vec::new();
    let mut tmp = [0u8; 8192];
    let header_end = loop {
        match stream.read(&mut tmp) {
            Ok(0) => break None,
            Ok(n) => {
                buf.extend_from_slice(&tmp[..n]);
                if let Some(end) = find_headers_end(&buf) {
                    break Some(end);
                }
                if buf.len() > 64 << 20 {
                    break None;
                }
            }
            Err(_) => break None,
        }
    };
    let header_end = match header_end {
        Some(e) => e,
        None => {
            respond(&mut stream, 400, &json!({"error": "bad request headers"}));
            return;
        }
    };
    let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let target = parts.next().unwrap_or("/").to_string();
    let mut content_length = 0usize;
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            if k.trim().eq_ignore_ascii_case("content-length") {
                content_length = v.trim().parse().unwrap_or(0);
            }
        }
    }
    let mut body = buf[header_end..].to_vec();
    while body.len() < content_length {
        match stream.read(&mut tmp) {
            Ok(0) => break,
            Ok(n) => body.extend_from_slice(&tmp[..n]),
            Err(_) => break,
        }
    }
    if body.len() > content_length {
        body.truncate(content_length);
    }
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target, String::new()),
    };
    let params = parse_query(&query);
    let body_json: Value = if body.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&body).unwrap_or(Value::Null)
    };
    let (status, resp) = route(&method, &path, &params, &body_json, shared);
    respond(&mut stream, status, &resp);
}

fn route(
    method: &str,
    path: &str,
    params: &BTreeMap<String, String>,
    body: &Value,
    shared: &Arc<Shared>,
) -> (u16, Value) {
    match (method, path) {
        ("GET", "/healthz") => {
            let core = shared.core.lock().unwrap();
            (
                200,
                json!({
                    "ok": true,
                    "service": "sync-client",
                    "device_id": core.state.device_id,
                    "online": !core.state.paused,
                    "queue_empty": core.state.awaiting.is_none(),
                }),
            )
        }
        ("GET", "/status") => {
            let mut core = shared.core.lock().unwrap();
            (200, sync::status(&mut core))
        }
        ("GET", "/pending") => {
            let core = shared.core.lock().unwrap();
            let pending = sync::pending_snapshot(&core);
            (
                200,
                json!({"ok": true, "pending_count": pending.len(), "pending": pending}),
            )
        }
        ("GET", "/conflicts") => {
            let core = shared.core.lock().unwrap();
            (
                200,
                json!({"ok": true, "conflicts": core.state.conflicts}),
            )
        }
        ("GET", "/history") => {
            let thread = params.get("thread_id").cloned().unwrap_or_default();
            if thread.is_empty() {
                return (400, json!({"error": "thread_id is required"}));
            }
            let turn = params
                .get("turn_id")
                .filter(|s| !s.is_empty())
                .map(|s| s.as_str());
            let mut core = shared.core.lock().unwrap();
            match sync::history(&mut core, &thread, turn) {
                Ok(v) => (200, v),
                Err(e) => (500, json!({"error": e})),
            }
        }
        ("GET", "/replay") => {
            let thread = params.get("thread_id").cloned().unwrap_or_default();
            if thread.is_empty() {
                return (400, json!({"error": "thread_id is required"}));
            }
            let turn = params
                .get("turn_id")
                .filter(|s| !s.is_empty())
                .map(|s| s.as_str());
            let mut core = shared.core.lock().unwrap();
            match sync::replay(&mut core, &thread, turn) {
                Ok(v) => (200, v),
                Err(e) => (500, json!({"error": e})),
            }
        }
        ("GET", "/threads") => {
            let mut core = shared.core.lock().unwrap();
            (200, sync::threads(&mut core))
        }
        ("GET", "/events") => {
            let thread = params
                .get("thread_id")
                .filter(|s| !s.is_empty())
                .map(|s| s.as_str());
            let mut core = shared.core.lock().unwrap();
            match sync::raw_events(&mut core, thread) {
                Ok(v) => (200, v),
                Err(e) => (500, json!({"error": e})),
            }
        }
        ("POST", "/sync") => {
            shared.trigger.store(true, Ordering::SeqCst);
            logline("CTL sync requested");
            (200, json!({"ok": true, "triggered": true}))
        }
        ("POST", "/pause") => {
            let mut core = shared.core.lock().unwrap();
            core.state.paused = true;
            core.save();
            logline(&format!(
                "CTL pause device={} (transport disconnected)",
                core.state.device_id
            ));
            (
                200,
                json!({"ok": true, "paused": true, "online": false, "at": now_ms()}),
            )
        }
        ("POST", "/resume") => {
            let mut core = shared.core.lock().unwrap();
            core.state.paused = false;
            core.save();
            shared.trigger.store(true, Ordering::SeqCst);
            logline(&format!(
                "CTL resume device={} (transport restored)",
                core.state.device_id
            ));
            (
                200,
                json!({"ok": true, "paused": false, "online": true, "at": now_ms()}),
            )
        }
        ("POST", "/retry") => {
            let mut core = shared.core.lock().unwrap();
            let moved = core.release_awaiting("manual-retry");
            shared.trigger.store(true, Ordering::SeqCst);
            logline(&format!(
                "CTL retry device={} released_awaiting={}",
                core.state.device_id, moved
            ));
            (200, json!({"ok": true, "retry_released": moved}))
        }
        ("POST", "/import") => {
            let file = body
                .get("file")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if file.is_empty() {
                return (400, json!({"error": "file is required"}));
            }
            let start = body
                .get("start_line")
                .and_then(Value::as_u64)
                .filter(|n| *n > 0);
            let end = body
                .get("end_line")
                .and_then(Value::as_u64)
                .filter(|n| *n > 0);
            let mut core = shared.core.lock().unwrap();
            match sync::import_file(&mut core, &file, start, end) {
                Ok(v) => (200, v),
                Err(e) => (500, json!({"error": e})),
            }
        }
        ("POST", "/shutdown") => {
            shared.shutdown.store(true, Ordering::SeqCst);
            logline("CTL shutdown requested");
            (200, json!({"ok": true, "shutting_down": true}))
        }
        _ => (
            404,
            json!({"error": "not found", "method": method, "path": path}),
        ),
    }
}

fn respond(stream: &mut TcpStream, status: u16, value: &Value) {
    let body = serde_json::to_vec_pretty(value).unwrap_or_else(|_| b"{}".to_vec());
    let reason = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        405 => "Method Not Allowed",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        status,
        reason,
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(&body);
    let _ = stream.flush();
}
