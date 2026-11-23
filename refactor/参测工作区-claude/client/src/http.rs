use serde_json::Value;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

#[derive(Debug)]
pub enum HttpError {
    Connect(String),
    AfterSend(String),
    Status(u16, String),
    Parse(String),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::Connect(m) => write!(f, "connect: {}", m),
            HttpError::AfterSend(m) => write!(f, "after-send: {}", m),
            HttpError::Status(c, b) => write!(f, "http {}: {}", c, b),
            HttpError::Parse(m) => write!(f, "parse: {}", m),
        }
    }
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn decode_chunked(data: &[u8]) -> Result<Vec<u8>, HttpError> {
    let mut out = Vec::new();
    let mut i = 0;
    loop {
        let line_end = find_subsequence(&data[i..], b"\r\n")
            .ok_or_else(|| HttpError::Parse("chunk size line".into()))?
            + i;
        let line = String::from_utf8_lossy(&data[i..line_end]).to_string();
        let size_str = line.split(';').next().unwrap_or("").trim();
        let size = usize::from_str_radix(size_str, 16)
            .map_err(|_| HttpError::Parse(format!("bad chunk size {:?}", size_str)))?;
        i = line_end + 2;
        if size == 0 {
            break;
        }
        if i + size > data.len() {
            return Err(HttpError::Parse("truncated chunk".into()));
        }
        out.extend_from_slice(&data[i..i + size]);
        i += size + 2;
    }
    Ok(out)
}

fn truncate(s: &str, n: usize) -> String {
    let mut t: String = s.chars().take(n).collect();
    if s.chars().count() > n {
        t.push_str("...");
    }
    t
}

/// Minimal HTTP/1.1 JSON client. `path_query` starts with '/'.
pub fn request(
    base: &str,
    method: &str,
    path_query: &str,
    body: Option<&Value>,
) -> Result<Value, HttpError> {
    let trimmed = base.trim_end_matches('/');
    let rest = trimmed
        .strip_prefix("http://")
        .ok_or_else(|| HttpError::Parse(format!("unsupported server URL {:?}", base)))?;
    let (authority, base_path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    let addrs: Vec<_> = authority
        .to_socket_addrs()
        .map_err(|e| HttpError::Connect(format!("resolve {}: {}", authority, e)))?
        .collect();
    let mut stream: Option<TcpStream> = None;
    let mut last_err = String::new();
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, Duration::from_secs(5)) {
            Ok(s) => {
                stream = Some(s);
                break;
            }
            Err(e) => last_err = e.to_string(),
        }
    }
    let mut stream = stream.ok_or_else(|| {
        HttpError::Connect(format!("connect {} failed: {}", authority, last_err))
    })?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(15)));

    let body_bytes = match body {
        Some(v) => Some(
            serde_json::to_vec(v)
                .map_err(|e| HttpError::Parse(format!("encode request: {}", e)))?,
        ),
        None => None,
    };
    let mut req = Vec::new();
    write!(
        &mut req,
        "{} {}{} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\nAccept: application/json\r\n",
        method, base_path, path_query, authority
    )
    .map_err(|e| HttpError::Parse(e.to_string()))?;
    if let Some(b) = &body_bytes {
        write!(
            &mut req,
            "Content-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\n",
            b.len()
        )
        .map_err(|e| HttpError::Parse(e.to_string()))?;
    }
    req.extend_from_slice(b"\r\n");
    if let Some(b) = body_bytes {
        req.extend_from_slice(&b);
    }
    if let Err(e) = stream.write_all(&req).and_then(|_| stream.flush()) {
        return Err(HttpError::AfterSend(format!("write request: {}", e)));
    }

    let mut buf = Vec::new();
    let mut read_err: Option<String> = None;
    if let Err(e) = stream.read_to_end(&mut buf) {
        read_err = Some(e.to_string());
    }
    if buf.is_empty() {
        return Err(HttpError::AfterSend(match read_err {
            Some(e) => format!("no response bytes ({})", e),
            None => "empty response (success receipt dropped?)".to_string(),
        }));
    }
    let header_end = match find_subsequence(&buf, b"\r\n\r\n") {
        Some(i) => i + 4,
        None => {
            return Err(HttpError::AfterSend(format!(
                "incomplete response headers{}",
                read_err
                    .map(|e| format!(" ({})", e))
                    .unwrap_or_default()
            )))
        }
    };
    let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or("");
    let code: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| HttpError::Parse(format!("bad status line {:?}", status_line)))?;
    let mut chunked = false;
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            if k.eq_ignore_ascii_case("transfer-encoding")
                && v.to_ascii_lowercase().contains("chunked")
            {
                chunked = true;
            }
        }
    }
    let payload = if chunked {
        decode_chunked(&buf[header_end..])?
    } else {
        buf[header_end..].to_vec()
    };
    let text = String::from_utf8_lossy(&payload).to_string();
    if code >= 400 {
        return Err(HttpError::Status(code, truncate(&text, 500)));
    }
    if text.trim().is_empty() {
        return Err(HttpError::Parse("empty response body".into()));
    }
    serde_json::from_str(&text).map_err(|e| {
        HttpError::Parse(format!("decode response: {} body={}", e, truncate(&text, 300)))
    })
}
