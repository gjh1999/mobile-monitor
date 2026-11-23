mod control;
mod http;
mod projection;
mod state;
mod sync;
mod util;

use control::Shared;
use state::{ClientState, Core};
use std::env;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use util::logline;

struct Config {
    device: String,
    project: PathBuf,
    state: PathBuf,
    server: String,
    control: String,
    interval: Duration,
    auto_retry: bool,
}

fn usage() {
    eprintln!(
        "sync-client --device <id> --project <dir> --state <dir> --control <host:port> [--server <url>] [--interval-ms <n>] [--no-auto-retry]"
    );
}

fn parse_args() -> Result<Config, String> {
    let mut cfg = Config {
        device: String::new(),
        project: PathBuf::new(),
        state: PathBuf::new(),
        server: "http://127.0.0.1:7801".to_string(),
        control: String::new(),
        interval: Duration::from_millis(300),
        auto_retry: true,
    };
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut take = |name: &str| -> Result<String, String> {
            args.next()
                .ok_or_else(|| format!("missing value for {}", name))
        };
        match arg.as_str() {
            "--device" => cfg.device = take("--device")?,
            "--project" => cfg.project = PathBuf::from(take("--project")?),
            "--state" => cfg.state = PathBuf::from(take("--state")?),
            "--server" => cfg.server = take("--server")?,
            "--control" => cfg.control = take("--control")?,
            "--interval-ms" => {
                let v = take("--interval-ms")?;
                let n: u64 = v
                    .parse()
                    .map_err(|_| format!("bad --interval-ms {:?}", v))?;
                cfg.interval = Duration::from_millis(n.max(50));
            }
            "--no-auto-retry" => cfg.auto_retry = false,
            "--help" | "-h" => {
                usage();
                std::process::exit(0);
            }
            other => return Err(format!("unknown argument {:?}", other)),
        }
    }
    if cfg.device.is_empty() {
        return Err("--device is required".into());
    }
    if cfg.project.as_os_str().is_empty() {
        return Err("--project is required".into());
    }
    if cfg.state.as_os_str().is_empty() {
        return Err("--state is required".into());
    }
    if cfg.control.is_empty() {
        return Err("--control is required".into());
    }
    Ok(cfg)
}

fn main() {
    let cfg = match parse_args() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("error: {}", e);
            usage();
            std::process::exit(2);
        }
    };
    if let Err(e) = std::fs::create_dir_all(&cfg.project) {
        eprintln!("create project dir: {}", e);
        std::process::exit(1);
    }
    if let Err(e) = std::fs::create_dir_all(&cfg.state) {
        eprintln!("create state dir: {}", e);
        std::process::exit(1);
    }
    let state_path = cfg.state.join("client-state.json");
    let state = match ClientState::load_or_new(&state_path, &cfg.device) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("load state: {}", e);
            std::process::exit(1);
        }
    };
    let mut core = Core {
        state,
        state_path,
        project: cfg.project.clone(),
        server: cfg.server.clone(),
        last_error: None,
        last_pending: Vec::new(),
        server_seq: 0,
        last_history_source: None,
    };
    core.save();
    logline(&format!(
        "CLIENT start device={} project={} state={} server={} control={} interval_ms={}",
        cfg.device,
        cfg.project.display(),
        cfg.state.display(),
        cfg.server,
        cfg.control,
        cfg.interval.as_millis()
    ));
    if core.state.awaiting.is_some() || core.state.inflight.is_some() {
        logline(&format!(
            "RECEIPT_STATE awaiting={} inflight={}",
            core.state.awaiting.is_some(),
            core.state.inflight.is_some()
        ));
        if cfg.auto_retry {
            let moved = core.release_awaiting("restart-auto-retry");
            logline(&format!(
                "RECEIPT_RETRY_ALLOWED after restart (moved_to_log={}, pending op will be retried with same op_id)",
                moved
            ));
        } else {
            logline("RECEIPT_RETRY_BLOCKED after restart; POST /retry to release the retry");
        }
    }
    core.save();

    let shared = Arc::new(Shared {
        core: Mutex::new(core),
        trigger: AtomicBool::new(true),
        shutdown: AtomicBool::new(false),
    });
    let _control_thread = match control::spawn_control(&cfg.control, shared.clone()) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("control listener {}: {}", cfg.control, e);
            std::process::exit(1);
        }
    };
    logline(&format!("CLIENT ready device={}", cfg.device));

    let mut last_tick = Instant::now();
    loop {
        if shared.shutdown.load(Ordering::SeqCst) {
            break;
        }
        let triggered = shared.trigger.swap(false, Ordering::SeqCst);
        if triggered || last_tick.elapsed() >= cfg.interval {
            {
                let mut core = shared.core.lock().unwrap();
                sync::sync_once(&mut core);
            }
            last_tick = Instant::now();
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    {
        let mut core = shared.core.lock().unwrap();
        core.save();
        logline(&format!("CLIENT shutdown device={}", core.state.device_id));
    }
    std::thread::sleep(Duration::from_millis(200));
}
