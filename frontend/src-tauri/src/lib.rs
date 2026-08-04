use tauri::Manager;

fn resolve_prod_sidecar_paths(
    resource_dir: &std::path::Path,
    app_local_data_dir: &std::path::Path,
) -> Result<String, String> {
    let direct_sidecar = resource_dir.join("python").join("PMA.exe");
    if direct_sidecar.exists() {
        return Ok(direct_sidecar.to_string_lossy().to_string());
    }

    let sidecar_zip = resource_dir.join("python").join("PMA-sidecar.zip");
    if !sidecar_zip.exists() {
        return Err(format!(
            "Bundled sidecar not found at {}",
            sidecar_zip.display()
        ));
    }

    let extract_dir = app_local_data_dir
        .join("sidecar")
        .join(env!("CARGO_PKG_VERSION"))
        .join("PMA");
    let extracted_sidecar = extract_dir.join("PMA.exe");
    if extracted_sidecar.exists() {
        return Ok(extracted_sidecar.to_string_lossy().to_string());
    }

    if extract_dir.exists() {
        std::fs::remove_dir_all(&extract_dir).map_err(|err| {
            format!(
                "Failed to remove incomplete sidecar directory {}: {err}",
                extract_dir.display()
            )
        })?;
    }
    std::fs::create_dir_all(&extract_dir).map_err(|err| {
        format!(
            "Failed to create sidecar directory {}: {err}",
            extract_dir.display()
        )
    })?;

    let output = std::process::Command::new("powershell")
    .args([
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      "& { param([string]$zipPath, [string]$destinationPath) Expand-Archive -LiteralPath $zipPath -DestinationPath $destinationPath -Force }",
    ])
    .arg(&sidecar_zip)
    .arg(&extract_dir)
    .output()
    .map_err(|err| format!("Failed to run PowerShell Expand-Archive: {err}"))?;

    if !output.status.success() {
        return Err(format!(
            "Failed to extract sidecar archive. stdout: {} stderr: {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    if !extracted_sidecar.exists() {
        return Err(format!(
            "Sidecar extraction completed but {} was not found",
            extracted_sidecar.display()
        ));
    }

    Ok(extracted_sidecar.to_string_lossy().to_string())
}

fn resolve_prod_sidecar<R: tauri::Runtime>(
    app_handle: &tauri::AppHandle<R>,
) -> Result<String, String> {
    let resource_dir = app_handle
        .path()
        .resource_dir()
        .map_err(|err| format!("Failed to resolve resource directory: {err}"))?;
    let app_local_data_dir = app_handle
        .path()
        .app_local_data_dir()
        .map_err(|err| format!("Failed to resolve app local data directory: {err}"))?;
    resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            app.handle().plugin(tauri_plugin_shell::init())?;
            app.handle().plugin(tauri_plugin_dialog::init())?;

            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // ── 1. Pick a free port FIRST (before managing state) ──────────────
            let port = portpicker::pick_unused_port().unwrap_or(18234);

            // ── 2. Generate a cryptographically random session token ────────────
            use uuid::Uuid;
            let token = Uuid::new_v4().to_string();

            // ── 3. Expose port + token to frontend via IPC (state is complete) ──
            app.manage(BackendInfo {
                port,
                token: token.clone(),
            });

            // ── 4. Spawn the Python sidecar in a non-blocking async task ────────
            let app_handle = app.handle().clone();
            let token_for_spawn = token.clone();

            tauri::async_runtime::spawn(async move {
                use std::io::{BufRead, BufReader};
                use std::process::{Command, Stdio};

                let debug = cfg!(debug_assertions);
                let (cmd_str, args) = if debug {
                    // Dev mode: use uv run (no Rust rebuild needed for Python/React changes)
                    (
                        "uv".to_string(),
                        vec!["run".to_string(), "app/main.py".to_string()],
                    )
                } else {
                    // Prod mode: extract the bundled sidecar ZIP once, then run PMA.exe.
                    let sidecar_path = resolve_prod_sidecar(&app_handle)
                        .expect("Failed to prepare backend sidecar");
                    (sidecar_path, vec![])
                };

                let mut child = Command::new(&cmd_str)
                    .args(args)
                    .env("PORT", port.to_string())
                    .env("X_LOCAL_ACCESS_TOKEN", &token_for_spawn)
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                    .expect("Failed to start backend sidecar");

                #[cfg(target_os = "windows")]
                {
                    use std::os::windows::io::AsRawHandle;
                    use winapi::um::jobapi2::{AssignProcessToJobObject, SetInformationJobObject};
                    use winapi::um::winnt::{
                        JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                        JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                    };

                    unsafe {
                        let job = winapi::um::jobapi2::CreateJobObjectW(
                            std::ptr::null_mut(),
                            std::ptr::null(),
                        );
                        if !job.is_null() {
                            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                            // KILL_ON_JOB_CLOSE keeps the sidecar from being orphaned.
                            // BREAKAWAY_OK lets the backend deliberately start a process
                            // that must outlive PMA -- Ollama / LM Studio, spawned with
                            // CREATE_BREAKAWAY_FROM_JOB (see app/providers/launcher.py).
                            // Without it that spawn fails with ERROR_ACCESS_DENIED.
                            info.BasicLimitInformation.LimitFlags =
                                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;

                            let res = SetInformationJobObject(
                                job,
                                JobObjectExtendedLimitInformation,
                                &mut info as *mut _ as *mut _,
                                std::mem::size_of_val(&info) as u32,
                            );

                            if res != 0 {
                                AssignProcessToJobObject(job, child.as_raw_handle() as *mut _);
                            }
                        }
                    }
                }

                // Log backend stdout to the Tauri console
                let stdout = child.stdout.take().unwrap();
                tauri::async_runtime::spawn_blocking(move || {
                    let reader = BufReader::new(stdout);
                    for l in reader.lines().map_while(Result::ok) {
                        println!("[BACKEND] {}", l);
                    }
                });

                // P10-2: Drain stderr to prevent process hangs if the buffer fills
                let stderr = child.stderr.take().unwrap();
                tauri::async_runtime::spawn_blocking(move || {
                    let reader = BufReader::new(stderr);
                    for l in reader.lines().map_while(Result::ok) {
                        eprintln!("[BACKEND ERROR] {}", l);
                    }
                });

                // P10-1: Wrap blocking child.wait() in spawn_blocking to prevent async thread starvation
                tauri::async_runtime::spawn_blocking(move || {
                    let exit_status = child.wait().expect("Failed to wait on backend sidecar");
                    if !exit_status.success() {
                        eprintln!(
                            "[BACKEND] Sidecar exited with error: {:?}",
                            exit_status.code()
                        );
                    }
                });
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_backend_info])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

fn get_backend_info_inner(info: &BackendInfo) -> (u16, String) {
    (info.port, info.token.clone())
}

#[tauri::command]
fn get_backend_info(state: tauri::State<'_, BackendInfo>) -> (u16, String) {
    get_backend_info_inner(&state)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_backend_info_ipc_command() {
        let info = BackendInfo {
            port: 18234,
            token: "test-token-uuid-1234".to_string(),
        };
        let (port, token) = get_backend_info_inner(&info);
        assert_eq!(port, 18234);
        assert_eq!(token, "test-token-uuid-1234");
    }

    #[test]
    fn test_uuid_token_format() {
        use uuid::Uuid;
        let token = Uuid::new_v4().to_string();
        assert_eq!(token.len(), 36);
        assert!(Uuid::parse_str(&token).is_ok());
    }

    #[test]
    fn test_portpicker_in_valid_range() {
        let port = portpicker::pick_unused_port().unwrap_or(18234);
        assert!(port > 1024);
    }

    #[test]
    fn test_portpicker_non_zero() {
        let port = portpicker::pick_unused_port().unwrap_or(18234);
        assert_ne!(port, 0);
    }

    #[test]
    fn test_serialization_backend_info() {
        let info = BackendInfo {
            port: 1234,
            token: "serializable-token".to_string(),
        };
        let serialized = serde_json::to_string(&info).unwrap();
        let deserialized: BackendInfo = serde_json::from_str(&serialized).unwrap();
        assert_eq!(deserialized.port, 1234);
        assert_eq!(deserialized.token, "serializable-token");
    }

    #[test]
    fn test_resolve_sidecar_missing_both() {
        let temp_dir = std::env::temp_dir().join(format!("test_missing_both_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");
        std::fs::create_dir_all(&resource_dir).unwrap();
        std::fs::create_dir_all(&app_local_data_dir).unwrap();

        let res = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir);
        assert!(res.is_err());
        let err_msg = res.unwrap_err();
        assert!(err_msg.contains("Bundled sidecar not found"));

        let _ = std::fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn test_resolve_sidecar_direct_exe() {
        let temp_dir = std::env::temp_dir().join(format!("test_direct_exe_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");
        
        let python_dir = resource_dir.join("python");
        std::fs::create_dir_all(&python_dir).unwrap();
        let exe_path = python_dir.join("PMA.exe");
        std::fs::write(&exe_path, "mock-exe-content").unwrap();
        
        let res = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir);
        assert!(res.is_ok());
        let resolved_path = res.unwrap();
        assert!(resolved_path.contains("PMA.exe"));
        
        let _ = std::fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn test_resolve_sidecar_already_extracted() {
        let temp_dir = std::env::temp_dir().join(format!("test_already_extracted_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");

        let python_dir = resource_dir.join("python");
        std::fs::create_dir_all(&python_dir).unwrap();
        let zip_path = python_dir.join("PMA-sidecar.zip");
        std::fs::write(&zip_path, "mock-zip-content").unwrap();

        let extract_dir = app_local_data_dir
            .join("sidecar")
            .join(env!("CARGO_PKG_VERSION"))
            .join("PMA");
        std::fs::create_dir_all(&extract_dir).unwrap();
        let extracted_exe = extract_dir.join("PMA.exe");
        std::fs::write(&extracted_exe, "mock-extracted-content").unwrap();
        
        let res = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir);
        assert!(res.is_ok());
        let resolved_path = res.unwrap();
        assert_eq!(resolved_path, extracted_exe.to_string_lossy().to_string());
        
        let _ = std::fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn test_resolve_sidecar_clean_partial() {
        let temp_dir = std::env::temp_dir().join(format!("test_clean_partial_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");

        let python_dir = resource_dir.join("python");
        std::fs::create_dir_all(&python_dir).unwrap();
        let zip_path = python_dir.join("PMA-sidecar.zip");
        std::fs::write(&zip_path, "mock-zip-content").unwrap();

        let extract_dir = app_local_data_dir
            .join("sidecar")
            .join(env!("CARGO_PKG_VERSION"))
            .join("PMA");
        std::fs::create_dir_all(&extract_dir).unwrap();
        
        let marker_file = extract_dir.join("marker.txt");
        std::fs::write(&marker_file, "marker").unwrap();
        
        let res = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir);
        assert!(res.is_err());
        
        assert!(!marker_file.exists());
        
        let _ = std::fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn test_resolve_sidecar_non_existent_resource_dir() {
        let temp_dir = std::env::temp_dir().join(format!("test_non_existent_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("non_existent_resources");
        let app_local_data_dir = temp_dir.join("local_data");
        std::fs::create_dir_all(&app_local_data_dir).unwrap();

        let res = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir);
        assert!(res.is_err());
        let err_msg = res.unwrap_err();
        assert!(err_msg.contains("Bundled sidecar not found"));

        let _ = std::fs::remove_dir_all(temp_dir);
    }
}

