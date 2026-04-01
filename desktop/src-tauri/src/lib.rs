use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

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

            let handle = app.handle().clone();

            // Launch Python engine sidecar
            tauri::async_runtime::spawn(async move {
                launch_engine_sidecar(&handle).await;
            });

            // Auto-update check (separate task)
            let handle2 = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = check_for_updates(handle2).await {
                    log::warn!("Update check failed: {}", e);
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

async fn launch_engine_sidecar(app: &tauri::AppHandle) {
    // Small delay to let the window render
    tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    log::info!("Launching BotStrike engine sidecar...");

    let shell = app.shell();
    let sidecar = shell.sidecar("binaries/botstrike-engine");

    match sidecar {
        Ok(command) => {
            match command.spawn() {
                Ok((mut rx, child)) => {
                    log::info!("Engine sidecar started (pid: {})", child.pid());

                    // Store child handle for cleanup
                    // Read stdout/stderr in background
                    tauri::async_runtime::spawn(async move {
                        use tauri_plugin_shell::process::CommandEvent;
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(line) => {
                                    let text = String::from_utf8_lossy(&line);
                                    if !text.trim().is_empty() {
                                        log::info!("[engine] {}", text.trim());
                                    }
                                }
                                CommandEvent::Stderr(line) => {
                                    let text = String::from_utf8_lossy(&line);
                                    if !text.trim().is_empty() {
                                        log::warn!("[engine] {}", text.trim());
                                    }
                                }
                                CommandEvent::Terminated(status) => {
                                    log::warn!("Engine sidecar terminated: {:?}", status);
                                    break;
                                }
                                _ => {}
                            }
                        }
                    });
                }
                Err(e) => {
                    log::error!("Failed to spawn engine sidecar: {}", e);
                    log::info!("Falling back to manual mode — run: python -m server.bridge");
                }
            }
        }
        Err(e) => {
            log::error!("Sidecar binary not found: {}", e);
            log::info!("Engine sidecar not bundled — run manually: python -m server.bridge");
        }
    }
}

async fn check_for_updates(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    tokio::time::sleep(std::time::Duration::from_secs(5)).await;

    log::info!("Checking for updates...");
    let updater = app.updater()?;
    let response = updater.check().await?;

    if let Some(update) = response {
        let version = update.version.clone();
        let current = update.current_version.clone();
        log::info!("Update available: {} -> {}", current, version);

        let should_update = app
            .dialog()
            .message(format!(
                "BotStrike v{} is available (current: v{}).\n\nDo you want to download and install it now?",
                version, current
            ))
            .title("Update Available")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Download & Install".into(),
                "Later".into(),
            ))
            .blocking_show();

        if !should_update {
            log::info!("User skipped update");
            return Ok(());
        }

        log::info!("Downloading update v{}...", version);
        let mut total_downloaded: usize = 0;

        update
            .download_and_install(
                |chunk_length, content_length| {
                    total_downloaded += chunk_length;
                    if let Some(total) = content_length {
                        let pct = (total_downloaded as f64 / total as f64 * 100.0) as u32;
                        if pct % 25 == 0 {
                            log::info!("Downloading: {}% ({}/{})", pct, total_downloaded, total);
                        }
                    }
                },
                || {
                    log::info!("Download complete, preparing install...");
                },
            )
            .await?;

        app.dialog()
            .message(format!(
                "BotStrike v{} installed successfully.\n\nThe app will restart now.",
                version
            ))
            .title("Update Installed")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::Ok)
            .blocking_show();

        log::info!("Restarting to apply v{}", version);
        app.restart();
    } else {
        log::info!("App is up to date");
    }

    Ok(())
}
