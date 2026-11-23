// syncclient: the local client core.
//
// Two modes:
//   launch  - daemon with a loopback control API (used by syncctl)
//   <cmd>   - one-shot direct execution of the same engine code
//
// No third-party runtime dependencies beyond serde/serde_json/sha2.
mod engine;
mod events;
mod httpc;
mod store;

use engine::Engine;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::exit;
use std::sync::{Arc, Mutex};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        usage();
    }
    let cmd = args[0].as_str();
    let mut id = String::new();
    let mut server = String::from("http://127.0.0.1:8730");
    let mut project = String::new();
    let mut state = String::new();
    let mut listen = String::new();
    let mut positional: Vec<String> = Vec::new();
    let mut opts: std::collections::HashMap<String, String> = Default::default();

    let mut i = 1;
    while i < args.len() {
        let a = &args[i];
        match a.as_str() {
            "--id" => {
                id = args.get(i + 1).cloned().unwrap_or_default();
                i += 1;
            }
            "--server" => {
                server = args.get(i + 1).cloned().unwrap_or_default();
                i += 1;
            }
            "--project" => {
                project = args.get(i + 1).cloned().unwrap_or_default();
                i += 1;
            }
            "--state" => {
                state = args.get(i + 1).cloned().unwrap_or_default();
                i += 1;
            }
            "--listen" => {
                listen = args.get(i + 1).cloned().unwrap_or_default();
                i += 1;
            }
            "--thread" | "--turn" | "--format" | "--from" | "--to" => {
                let k = a.trim_start_matches('-').to_string();
                opts.insert(k, args.get(i + 1).cloned().unwrap_or_default());
                i += 1;
            }
            _ => positional.push(a.clone()),
        }
        i += 1;
    }

    if cmd == "launch" {
        if id.is_empty() || listen.is_empty() || project.is_empty() || state.is_empty() {
            eprintln!("launch requires --id --listen --project --state");
            exit(2);
        }
        daemon(&id, &listen, &server, Path::new(&project), Path::new(&state));
        return;
    }

    // One-shot commands need the full context.
    if project.is_empty() || state.is_empty() {
        eprintln!("direct commands require --project and --state (and --server)");
        exit(2);
    }
    let client_id = if id.is_empty() { "CLI".to_string() } else { id };

    let mut eng = match Engine::open(Path::new(&state), &client_id, &server, Path::new(&project)) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("open engine: {}", e);
            exit(1);
        }
    };

    match cmd {
        "sync" => {
            let rep = eng.sync();
            let mut v = rep.to_json();
            v["version"] = json!(eng.snap.version);
            v["error"] = json!(eng.last_error.clone().unwrap_or_default());
            println!("{}", serde_json::to_string_pretty(&v).unwrap());
            if rep.receipt_lost {
                exit(2); // commit may be durable server-side; retry with sync
            }
            if eng.last_error.is_some() {
                exit(1);
            }
        }
        "status" => println!("{}", serde_json::to_string_pretty(&eng.status_json()).unwrap()),
        "pause" => {
            eng.set_offline(true);
            println!("{}", serde_json::to_string_pretty(&eng.status_json()).unwrap());
        }
        "resume" => {
            eng.set_offline(false);
            println!("{}", serde_json::to_string_pretty(&eng.status_json()).unwrap());
        }
        "files" => println!("{}", serde_json::to_string_pretty(&eng.files_json()).unwrap()),
        "conflicts" => println!("{}", serde_json::to_string_pretty(&eng.conflicts_json()).unwrap()),
        "import-events" => {
            let file = positional
                .first()
                .map(PathBuf::from)
                .unwrap_or_else(|| usage_exit("import-events requires a JSONL file path"));
            let from = opts.get("from").and_then(|s| s.parse::<i64>().ok());
            let to = opts.get("to").and_then(|s| s.parse::<i64>().ok());
            match eng.import_events_file(&file, from, to) {
                Ok((submitted, accepted_local, pushed)) => {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&json!({
                            "submitted_lines": submitted,
                            "new_local": accepted_local,
                            "pushed_to_server": pushed,
                            "library_total": eng.store.count_events().unwrap_or(0),
                            "event_push_cursor": eng.snap.event_push_cursor,
                        }))
                        .unwrap()
                    );
                }
                Err(e) => {
                    eprintln!("import failed: {}", e);
                    exit(1);
                }
            }
        }
        "dump-events" => {
            let all = match eng.store.read_events() {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("read events: {}", e);
                    exit(1);
                }
            };
            let view = events::build_view(&all);
            let tf = nonempty(opts.get("thread"));
            let uf = nonempty(opts.get("turn"));
            let format = opts.get("format").cloned().unwrap_or_else(|| "text".into());
            if format == "json" {
                // JSON view: threads filtered, turns filtered, items structured.
                let filtered: Vec<Value> = view
                    .iter()
                    .filter(|t| tf.map_or(true, |f| t.thread_id == f))
                    .map(|t| {
                        json!({
                            "thread_id": t.thread_id,
                            "turns": t.turns.iter()
                                .filter(|tu| uf.map_or(true, |f| tu.turn_id == f))
                                .map(|tu| json!({
                                    "turn_id": tu.turn_id,
                                    "items": tu.items.iter().map(|i| i.to_json()).collect::<Vec<_>>(),
                                }))
                                .collect::<Vec<_>>(),
                        })
                    })
                    .collect();
                println!(
                    "{}",
                    serde_json::to_string_pretty(&json!({"client_id": client_id, "threads": filtered}))
                        .unwrap()
                );
            } else {
                print!("{}", events::dump_text(&view, tf, uf));
            }
        }
        "replay" => {
            let all = match eng.store.read_events() {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("read events: {}", e);
                    exit(1);
                }
            };
            let tf = nonempty(opts.get("thread"));
            let uf = nonempty(opts.get("turn"));
            match events::replay(&eng.store, &client_id, &all, tf, uf) {
                Ok(v) => println!("{}", serde_json::to_string_pretty(&v).unwrap()),
                Err(e) => {
                    eprintln!("replay failed: {}", e);
                    exit(1);
                }
            }
        }
        _ => usage(),
    }
    eng.store.release_lock();
}

fn nonempty(s: Option<&String>) -> Option<&str> {
    match s {
        Some(v) if !v.is_empty() => Some(v.as_str()),
        _ => None,
    }
}

fn usage_exit(msg: &str) -> ! {
    eprintln!("{}", msg);
    exit(2);
}

fn usage() {
    eprintln!(
        "syncclient <launch|sync|status|pause|resume|files|conflicts|import-events|dump-events|replay>\n\
         launch: --id ID --listen 127.0.0.1:PORT --server URL --project DIR --state DIR\n\
         direct: sync|status|... --server URL --project DIR --state DIR [--id ID]"
    );
    exit(2);
}

// ---------------- daemon ----------------

fn daemon(id: &str, listen: &str, server: &str, project: &Path, state: &Path) {
    let eng = Engine::open(state, id, server, project).expect("open engine");
    let shared = Arc::new(Mutex::new(eng));
    let addr = listen.to_string();
    eprintln!(
        "[{}] daemon on {} server={} project={} state={}",
        id,
        addr,
        server,
        project.display(),
        state.display()
    );
    let _ = httpc::serve(&addr, move |req| handle(req, &shared));
}

fn handle(req: &httpc::Req, shared: &Mutex<Engine>) -> (u16, Vec<u8>) {
    let body_json = |b: &[u8]| -> Value {
        serde_json::from_slice(b).unwrap_or(Value::Null)
    };
    let ok = |v: Value| -> (u16, Vec<u8>) {
        (200, serde_json::to_vec_pretty(&v).expect("serialize"))
    };
    let err = |code: u16, msg: &str| -> (u16, Vec<u8>) {
        (code, serde_json::to_vec(&json!({"error": msg})).expect("serialize"))
    };

    let mut eng = match shared.lock() {
        Ok(g) => g,
        Err(_) => return err(500, "engine poisoned"),
    };
    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/status") => ok(eng.status_json()),
        ("POST", "/sync") => {
            let rep = eng.sync();
            let mut v = rep.to_json();
            v["version"] = json!(eng.snap.version);
            v["error"] = json!(eng.last_error.clone().unwrap_or_default());
            ok(v)
        }
        ("POST", "/pause") => {
            eng.set_offline(true);
            ok(eng.status_json())
        }
        ("POST", "/resume") => {
            eng.set_offline(false);
            ok(eng.status_json())
        }
        ("GET", "/files") => ok(eng.files_json()),
        ("GET", "/conflicts") => ok(eng.conflicts_json()),
        ("POST", "/import") => {
            let v = body_json(&req.body);
            let file = match v.get("file").and_then(|x| x.as_str()) {
                Some(f) if !f.is_empty() => PathBuf::from(f),
                _ => return err(400, "missing file"),
            };
            let from = v.get("from").and_then(|x| x.as_i64());
            let to = v.get("to").and_then(|x| x.as_i64());
            match eng.import_events_file(&file, from, to) {
                Ok((submitted, accepted_local, pushed)) => ok(json!({
                    "submitted_lines": submitted,
                    "new_local": accepted_local,
                    "pushed_to_server": pushed,
                    "library_total": eng.store.count_events().unwrap_or(0),
                })),
                Err(e) => err(422, &e),
            }
        }
        ("GET", "/dump") => {
            let all = match eng.store.read_events() {
                Ok(v) => v,
                Err(e) => return err(500, &e.to_string()),
            };
            let view = events::build_view(&all);
            let qf = |k: &str| -> Option<String> {
                req.query.split('&').find_map(|pair| {
                    let (k2, v) = pair.split_once('=')?;
                    if k2 == k && !v.is_empty() {
                        Some(v.to_string())
                    } else {
                        None
                    }
                })
            };
            let tf = qf("thread");
            let uf = qf("turn");
            let format = qf("format").unwrap_or_else(|| "text".into());
            if format == "json" {
                let filtered: Vec<Value> = view
                    .iter()
                    .filter(|t| tf.as_deref().map_or(true, |f| t.thread_id == f))
                    .map(|t| {
                        json!({
                            "thread_id": t.thread_id,
                            "turns": t.turns.iter()
                                .filter(|tu| uf.as_deref().map_or(true, |f| tu.turn_id == f))
                                .map(|tu| json!({
                                    "turn_id": tu.turn_id,
                                    "items": tu.items.iter().map(|i| i.to_json()).collect::<Vec<_>>(),
                                }))
                                .collect::<Vec<_>>(),
                        })
                    })
                    .collect();
                ok(json!({"client_id": eng.client_id, "threads": filtered}))
            } else {
                ok(json!({
                    "client_id": eng.client_id,
                    "text": events::dump_text(&view, tf.as_deref(), uf.as_deref()),
                }))
            }
        }
        ("POST", "/replay") => {
            let v = body_json(&req.body);
            let all = match eng.store.read_events() {
                Ok(ev) => ev,
                Err(e) => return err(500, &e.to_string()),
            };
            let tf = v.get("thread").and_then(|x| x.as_str()).filter(|s| !s.is_empty());
            let uf = v.get("turn").and_then(|x| x.as_str()).filter(|s| !s.is_empty());
            match events::replay(&eng.store, &eng.client_id, &all, tf, uf) {
                Ok(out) => ok(out),
                Err(e) => err(422, &e),
            }
        }
        ("GET", "/ping") => ok(json!({"client_id": eng.client_id, "ok": true})),
        _ => err(404, "no such route"),
    }
}
