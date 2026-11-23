// Minimal HTTP/1.1 client and server, hand-rolled on std::net.
// The coordinator server (Go) always answers with Content-Length and
// honors Connection: close, so the client only needs that subset.
// The daemon side serves a small JSON control API on loopback.
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::time::Duration;


#[derive(Debug)]
pub enum HttpError {
    // Could not open a connection (server down / offline).
    Connect(String),
    // Connection was established but closed before any response byte:
    // exactly the "server committed durably but dropped the receipt" case.
    Lost(String),
    // Timeout while talking to the server.
    Timeout(String),
    Protocol(String),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::Connect(s) => write!(f, "connect failed: {}", s),
            HttpError::Lost(s) => write!(f, "connection closed before response (receipt lost): {}", s),
            HttpError::Timeout(s) => write!(f, "timeout: {}", s),
            HttpError::Protocol(s) => write!(f, "protocol error: {}", s),
        }
    }
}

pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

impl Response {
    pub fn json(&self) -> Result<serde_json::Value, HttpError> {
        serde_json::from_slice(&self.body)
            .map_err(|e| HttpError::Protocol(format!("bad json in response: {}", e)))
    }
}

fn parse_url(url: &str) -> Result<(String, u16, String, bool), HttpError> {
    let tls = url.starts_with("https://");
    let rest = if tls {
        &url["https://".len()..]
    } else if let Some(r) = url.strip_prefix("http://") {
        r
    } else {
        return Err(HttpError::Protocol(format!("unsupported url {}", url)));
    };
    let (hp, path) = match rest.find('/') {
        Some(i) => (&rest[..i], rest[i..].to_string()),
        None => (rest, "/".to_string()),
    };
    let (host, port) = match hp.rsplit_once(':') {
        Some((h, pt)) => (
            h.to_string(),
            pt.parse::<u16>()
                .map_err(|_| HttpError::Protocol("bad port".into()))?,
        ),
        None => (hp.to_string(), if tls { 443 } else { 80 }),
    };
    Ok((host, port, path, tls))
}

/// Perform one request. Every request uses a fresh connection with
/// `Connection: close`; responses are read to EOF (loopback only).
pub fn request(
    url: &str,
    method: &str,
    body: Option<&[u8]>,
    timeout: Duration,
) -> Result<Response, HttpError> {
    let (host, port, path, tls) = parse_url(url)?;
    if tls {
        return Err(HttpError::Protocol("https not supported (loopback only)".into()));
    }
    let addr = format!("{}:{}", host, port);
    let sa: SocketAddr = addr
        .parse()
        .map_err(|_| HttpError::Protocol(format!("bad address {}", addr)))?;
    let mut stream = TcpStream::connect_timeout(&sa, timeout)
        .map_err(|e| HttpError::Connect(format!("{} ({})", addr, e)))?;
    stream
        .set_read_timeout(Some(timeout))
        .and_then(|_| stream.set_write_timeout(Some(timeout)))
        .map_err(|e| HttpError::Connect(e.to_string()))?;

    let mut req = format!(
        "{} {} HTTP/1.1\r\nHost: {}:{}\r\nConnection: close\r\nUser-Agent: syncclient/0.1\r\n",
        method, path, host, port
    );
    match body {
        Some(b) => {
            req.push_str(&format!("Content-Type: application/json\r\nContent-Length: {}\r\n\r\n", b.len()));
        }
        None => req.push_str("\r\n"),
    }
    stream
        .write_all(req.as_bytes())
        .map_err(|e| HttpError::Lost(format!("write request: {}", e)))?;
    if let Some(b) = body {
        stream
            .write_all(b)
            .map_err(|e| HttpError::Lost(format!("write body: {}", e)))?;
    }
    stream.flush().ok();

    let mut raw = Vec::new();
    match stream.read_to_end(&mut raw) {
        Ok(_) => {}
        Err(e) => {
            // A timeout after we already have the full body is fine.
            if !raw.is_empty() && e.kind() == std::io::ErrorKind::WouldBlock {
                // fall through to parsing below
            } else if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::TimedOut {
                return Err(HttpError::Timeout(format!("{} ({})", addr, e)));
            } else {
                return Err(HttpError::Lost(format!("{} ({})", addr, e)));
            }
        }
    }
    if raw.is_empty() {
        // Connected, sent everything, then the server closed without
        // answering: the receipt was lost after durable commit.
        return Err(HttpError::Lost(format!("no bytes from {}", addr)));
    }
    parse_response(&raw)
}

fn parse_response(raw: &[u8]) -> Result<Response, HttpError> {
    let sep = find_subslice(raw, b"\r\n\r\n")
        .ok_or_else(|| HttpError::Protocol("no header terminator".into()))?;
    let head = std::str::from_utf8(&raw[..sep])
        .map_err(|_| HttpError::Protocol("non-utf8 headers".into()))?;
    let mut lines = head.split("\r\n");
    let status_line = lines
        .next()
        .ok_or_else(|| HttpError::Protocol("empty response".into()))?;
    let mut parts = status_line.split_whitespace();
    let _ver = parts.next().unwrap_or("HTTP/1.1");
    let status: u16 = parts
        .next()
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| HttpError::Protocol(format!("bad status line {:?}", status_line)))?;
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for ln in lines {
        if let Some((k, v)) = ln.split_once(':') {
            let k = k.trim().to_ascii_lowercase();
            let v = v.trim();
            if k == "content-length" {
                content_length = v.parse().ok();
            } else if k == "transfer-encoding" && v.to_ascii_lowercase().contains("chunked") {
                chunked = true;
            }
        }
    }
    let body_raw = &raw[sep + 4..];
    let body = if chunked {
        decode_chunked(body_raw)?
    } else if let Some(n) = content_length {
        if body_raw.len() < n {
            return Err(HttpError::Protocol(format!(
                "body truncated: have {} want {}",
                body_raw.len(),
                n
            )));
        }
        body_raw[..n].to_vec()
    } else {
        body_raw.to_vec() // read to EOF; fine for Connection: close
    };
    Ok(Response { status, body })
}

fn decode_chunked(mut data: &[u8]) -> Result<Vec<u8>, HttpError> {
    let mut out = Vec::new();
    loop {
        let nl = find_subslice(data, b"\r\n")
            .ok_or_else(|| HttpError::Protocol("bad chunk header".into()))?;
        let size_str = std::str::from_utf8(&data[..nl])
            .map_err(|_| HttpError::Protocol("bad chunk size".into()))?;
        let size = usize::from_str_radix(size_str.trim().split(';').next().unwrap_or("0"), 16)
            .map_err(|_| HttpError::Protocol("bad chunk size".into()))?;
        data = &data[nl + 2..];
        if size == 0 {
            break;
        }
        if data.len() < size + 2 {
            return Err(HttpError::Protocol("chunk truncated".into()));
        }
        out.extend_from_slice(&data[..size]);
        data = &data[size + 2..];
    }
    Ok(out)
}

fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || needle.len() > hay.len() {
        return None;
    }
    (0..=hay.len() - needle.len()).find(|&i| &hay[i..i + needle.len()] == needle)
}

// ---------------- tiny HTTP server (daemon control plane) ----------------

pub struct Req {
    pub method: String,
    pub path: String,
    pub query: String,
    pub body: Vec<u8>,
}

/// Serve requests on loopback with one thread per connection until the
/// process is killed. `handler` returns (status, body).
pub fn serve<F>(addr: &str, handler: F) -> std::io::Result<()>
where
    F: Fn(&Req) -> (u16, Vec<u8>) + Send + Sync + 'static,
{
    let listener = TcpListener::bind(addr)?;
    let handler = std::sync::Arc::new(handler);
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let handler = handler.clone();
        std::thread::spawn(move || {
            let _ = handle_conn(&mut stream, &*handler);
        });
    }
    Ok(())
}

fn handle_conn<F>(stream: &mut TcpStream, handler: &F) -> std::io::Result<()>
where
    F: Fn(&Req) -> (u16, Vec<u8>),
{
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    let _ = stream.set_write_timeout(Some(Duration::from_secs(30)));
    let mut buf = Vec::new();
    let mut tmp = [0u8; 16384];
    // Read until end of headers.
    let head_end = loop {
        let n = match stream.read(&mut tmp) {
            Ok(0) => return Ok(()),
            Ok(n) => n,
            Err(_) => return Ok(()),
        };
        buf.extend_from_slice(&tmp[..n]);
        if let Some(i) = find_subslice(&buf, b"\r\n\r\n") {
            break i;
        }
        if buf.len() > 1024 * 1024 {
            return Ok(());
        }
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let target = parts.next().unwrap_or("").to_string();
    let mut content_length = 0usize;
    for ln in lines {
        if let Some((k, v)) = ln.split_once(':') {
            if k.trim().eq_ignore_ascii_case("content-length") {
                content_length = v.trim().parse().unwrap_or(0);
            }
        }
    }
    if content_length > 64 * 1024 * 1024 {
        return Ok(());
    }
    let mut body = buf[head_end + 4..].to_vec();
    while body.len() < content_length {
        let n = match stream.read(&mut tmp) {
            Ok(0) => break,
            Ok(n) => n,
            Err(_) => break,
        };
        body.extend_from_slice(&tmp[..n]);
    }
    body.truncate(content_length);
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target, String::new()),
    };
    let req = Req { method, path, query, body };
    let (status, resp_body) = handler(&req);
    let status_text = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        409 => "Conflict",
        422 => "Unprocessable Entity",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let resp = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        status,
        status_text,
        resp_body.len()
    );
    stream.write_all(resp.as_bytes())?;
    stream.write_all(&resp_body)?;
    Ok(())
}
