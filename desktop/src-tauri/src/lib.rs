use tauri::Manager;
use tauri_plugin_updater::UpdaterExt;

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
            // Auto-update check on startup (non-blocking)
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let _ = check_for_updates(handle).await;
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

async fn check_for_updates(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    // Wait a few seconds before checking (let app settle)
    tokio::time::sleep(std::time::Duration::from_secs(3)).await;

    let updater = app.updater()?;
    let response = updater.check().await?;

    if let Some(update) = response {
        let version = update.version.clone();
        log::info!("Update available: v{}", version);

        // Download and install
        let mut downloaded: usize = 0;
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

        // Restart to apply update
        app.restart();
    } else {
        log::info!("App is up to date");
    }

    Ok(())
}
