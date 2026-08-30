use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

const DEFAULT_BRIDGE_PORT: u16 = 9420;

/// Engine process we spawned ourselves.
/// `None` when a pre-existing bridge was found on the port or the configured bridge is remote.
struct EngineProc(Mutex<Option<Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .manage(EngineProc(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![ensure_local_engine])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Set high-res window icon (bundle icon is only for .exe file)
            if let Some(window) = app.get_webview_window("main") {
                let icon_bytes = include_bytes!("../icons/128x128.png");
                if let Ok(icon) = tauri::image::Image::from_bytes(icon_bytes) {
                    let _ = window.set_icon(icon);
                }
            }

            // The engine is NOT launched here any more: the frontend calls `ensure_local_engine`
            // only when the configured bridge URL (Settings → Connection) is loopback.

            // Auto-update
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(10)).await;
                if let Err(e) = check_for_updates(handle).await {
                    log::warn!("Updater: {}", e);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                kill_engine(app);
            }
        });
}

fn engine_candidate_paths(app: &tauri::AppHandle) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    // Resource dir (bundled with app)
    if let Ok(resource) = app.path().resource_dir() {
        paths.push(resource.join("binaries").join("engine").join("botstrike-engine.exe"));
    }
    // Next to main exe (NSIS install: AppData/Local/BotStrike/)
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            paths.push(dir.join("binaries").join("engine").join("botstrike-engine.exe"));
            paths.push(dir.join("engine").join("botstrike-engine.exe"));
            paths.push(dir.join("botstrike-engine.exe"));
        }
    }
    paths
}

fn port_open(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Called by the frontend (src/lib/engine.ts) only when the bridge URL is loopback.
/// Idempotent: returns early if something already listens on the port or if we already spawned.
/// `async` so the port probe + spawn never block the main thread.
#[tauri::command]
async fn ensure_local_engine(app: tauri::AppHandle, port: u16) -> Result<String, String> {
    if port_open(port) {
        return Ok(format!("bridge already listening on 127.0.0.1:{port}"));
    }
    let state = app.state::<EngineProc>();
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(child) = guard.as_mut() {
        if matches!(child.try_wait(), Ok(None)) {
            return Ok(format!("engine already spawned (pid {})", child.id()));
        }
    }
    let paths = engine_candidate_paths(&app);
    let child = launch_engine(&paths, port)?;
    let pid = child.id();
    *guard = Some(child);
    Ok(format!("engine started (pid {pid}) on 127.0.0.1:{port}"))
}

fn launch_engine(paths: &[PathBuf], port: u16) -> Result<Child, String> {
    for (i, path) in paths.iter().enumerate() {
        log::info!("Engine path [{}]: {} (exists: {})", i, path.display(), path.exists());
    }
    for path in paths {
        if !path.exists() {
            continue;
        }
        log::info!("Launching engine: {}", path.display());

        // Set working dir to engine folder (so PyInstaller's _internal/ is found)
        let work_dir = path.parent().unwrap_or(path);
        let mut cmd = Command::new(path);
        cmd.current_dir(work_dir)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());
        if port != DEFAULT_BRIDGE_PORT {
            cmd.arg("--port").arg(port.to_string());
        }
        match cmd.spawn() {
            Ok(child) => {
                log::info!("Engine started (pid: {})", child.id());
                return Ok(child);
            }
            Err(e) => log::error!("Failed: {} — {}", path.display(), e),
        }
    }
    Err("Engine binary not found. Run manually: python -m server.bridge".into())
}

/// Kill the engine we spawned (never touches a pre-existing bridge). Called on Exit and before restart.
fn kill_engine(app: &tauri::AppHandle) {
    if let Ok(mut guard) = app.state::<EngineProc>().0.lock() {
        if let Some(mut child) = guard.take() {
            log::info!("Stopping engine (pid {})", child.id());
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

async fn check_for_updates(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let updater = app.updater()?;

    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => { log::info!("Up to date"); return Ok(()); }
        Err(e) => { log::warn!("Update check: {}", e); return Ok(()); }
    };

    let ver = update.version.clone();
    let cur = update.current_version.clone();

    let go = app.dialog()
        .message(format!("BotStrike v{} available (current: v{}).\n\nDownload and install?", ver, cur))
        .title("Update Available")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom("Download & Install".into(), "Later".into()))
        .blocking_show();

    if !go { return Ok(()); }

    let mut dl: usize = 0;
    let mut last_pct: u64 = 0;
    update.download_and_install(
        |c, t| {
            dl += c;
            if let Some(t) = t {
                let p = (dl as u64) * 100 / (t as u64);
                if p / 25 > last_pct / 25 { log::info!("Update download {}%", p); }
                last_pct = p;
            }
        },
        || log::info!("Installed"),
    ).await?;

    app.dialog()
        .message("Update installed. Restarting.")
        .title("Ready")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::Ok)
        .blocking_show();

    // Don't leave an old engine running under the new UI.
    kill_engine(&app);
    app.restart();
}
