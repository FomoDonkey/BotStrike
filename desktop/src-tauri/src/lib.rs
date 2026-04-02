use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;
use std::process::Command;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
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

            // Collect possible engine paths
            let mut engine_paths: Vec<std::path::PathBuf> = Vec::new();

            // Resource dir (bundled with app)
            if let Ok(resource) = app.path().resource_dir() {
                engine_paths.push(resource.join("binaries").join("engine").join("botstrike-engine.exe"));
            }
            // Next to main exe
            if let Ok(exe) = std::env::current_exe() {
                if let Some(dir) = exe.parent() {
                    engine_paths.push(dir.join("engine").join("botstrike-engine.exe"));
                    engine_paths.push(dir.join("botstrike-engine.exe"));
                }
            }

            std::thread::spawn(move || {
                launch_engine(&engine_paths);
            });

            // Auto-update
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_secs(10)).await;
                let _ = check_for_updates(handle).await;
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn launch_engine(paths: &[std::path::PathBuf]) {
    // Check if bridge already running
    if std::net::TcpStream::connect("127.0.0.1:9420").is_ok() {
        log::info!("Bridge already running on :9420, skipping engine launch");
        return;
    }

    for path in paths {
        if path.exists() {
            log::info!("Launching engine: {}", path.display());

            // Set working dir to engine folder (so _internal/ is found)
            let work_dir = path.parent().unwrap_or(path);

            match Command::new(path)
                .current_dir(work_dir)
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
            {
                Ok(child) => {
                    log::info!("Engine started (pid: {})", child.id());
                    return;
                }
                Err(e) => {
                    log::error!("Failed: {} — {}", path.display(), e);
                }
            }
        }
    }

    log::warn!("Engine not found. Run manually: python -m server.bridge");
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
    update.download_and_install(
        |c, t| { dl += c; if let Some(t) = t { let p = (dl as u64)*100/(t as u64); if p%25==0 { log::info!("{}%", p); } } },
        || log::info!("Installed"),
    ).await?;

    app.dialog()
        .message("Update installed. Restarting.")
        .title("Ready")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::Ok)
        .blocking_show();

    app.restart();
    Ok(())
}
