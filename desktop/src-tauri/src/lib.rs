use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // Auto-update check on startup
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = check_for_updates(handle).await {
                    log::warn!("Update check failed: {}", e);
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

async fn check_for_updates(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let updater = app.updater_builder().build()?;
    if let Some(update) = updater.check().await? {
        let version = update.version.clone();
        log::info!("Update available: v{}", version);

        // Use dialog to ask user
        let do_update = tauri_plugin_dialog::MessageDialogBuilder::new(
            "Update Available",
            format!(
                "BotStrike v{} is available. Would you like to update now?",
                version
            ),
        )
        .kind(tauri_plugin_dialog::MessageDialogKind::Info)
        .ok_button_label("Update")
        .cancel_button_label("Later")
        .blocking_show();

        if do_update {
            log::info!("Downloading update v{}...", version);
            let mut downloaded = 0;
            update
                .download_and_install(
                    |chunk_length, content_length| {
                        downloaded += chunk_length;
                        log::info!(
                            "Downloaded {} / {}",
                            downloaded,
                            content_length.unwrap_or(0)
                        );
                    },
                    || {
                        log::info!("Download complete, restarting...");
                    },
                )
                .await?;
            app.restart();
        }
    } else {
        log::info!("No updates available");
    }
    Ok(())
}
