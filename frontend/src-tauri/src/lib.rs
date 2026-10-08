use std::path::{Path, PathBuf};
use tauri::{Emitter, Manager};

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

    // Build-Exe.bat zips `dist\sidecar\PMA` with `-C dist\sidecar PMA`, so every
    // entry is prefixed `PMA/` and Expand-Archive lands the exe at <extract_dir>/PMA/PMA.exe.
    let extract_dir = app_local_data_dir
        .join("sidecar")
        .join(env!("CARGO_PKG_VERSION"));
    let extracted_sidecar = extract_dir.join("PMA").join("PMA.exe");
    // The exe appears long before Expand-Archive finishes, so its presence alone
    // would bless an interrupted extraction forever; the marker is written last.
    let marker = extract_dir.join(".extracted");
    if extracted_sidecar.exists() && marker.exists() {
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

    std::fs::write(&marker, env!("CARGO_PKG_VERSION"))
        .map_err(|err| format!("Failed to write extraction marker {}: {err}", marker.display()))?;

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

/// Working directory for the backend. `data/`, `.env` and the settings files are
/// cwd-relative (app/config.py, settings_store.py, providers/cache.py), so it has
/// to be writable. Dev uses the repo root: `tauri dev` runs with cwd = src-tauri,
/// where `app/main.py` does not exist, and it shares `data/` with browser mode.
/// An installed build must not inherit its cwd (the MSI shortcut starts in
/// Program Files) and uses the per-user app-local-data dir instead.
fn sidecar_workdir(debug: bool, repo_root: &Path, app_local_data_dir: &Path) -> PathBuf {
    if debug {
        repo_root.to_path_buf()
    } else {
        app_local_data_dir.to_path_buf()
    }
}

/// Backend child handle plus the last startup/exit failure, so the webview can
/// ask for it after the fact (an event alone is lost if emitted before it listens).
#[derive(Default)]
struct BackendProc {
    child: std::sync::Mutex<Option<std::process::Child>>,
    error: std::sync::Mutex<Option<String>>,
}

fn report_backend_failure<R: tauri::Runtime>(app: &tauri::AppHandle<R>, msg: String) {
    eprintln!("[BACKEND] {msg}");
    if let Ok(mut slot) = app.state::<BackendProc>().error.lock() {
        *slot = Some(msg.clone());
    }
    let _ = app.emit("backend-error", msg);
}

fn kill_backend<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    let child = app
        .state::<BackendProc>()
        .child
        .lock()
        .ok()
        .and_then(|mut slot| slot.take());
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Named mutex in the per-session namespace: a second launch would otherwise start
/// a second backend (watcher, OCR drain, vacuum) on the same data directory.
/// The handle is deliberately never closed -- it must live as long as the process.
#[cfg(target_os = "windows")]
fn another_instance_running() -> bool {
    use winapi::shared::winerror::ERROR_ALREADY_EXISTS;
    use winapi::um::errhandlingapi::GetLastError;
    use winapi::um::synchapi::CreateMutexW;

    let name: Vec<u16> = "Local\\com.pma.app.single-instance\0"
        .encode_utf16()
        .collect();
    unsafe {
        let handle = CreateMutexW(std::ptr::null_mut(), 0, name.as_ptr());
        !handle.is_null() && GetLastError() == ERROR_ALREADY_EXISTS
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "windows")]
    if another_instance_running() {
        return;
    }

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
            app.manage(BackendProc::default());

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
                    match resolve_prod_sidecar(&app_handle) {
                        Ok(path) => (path, vec![]),
                        Err(msg) => {
                            report_backend_failure(&app_handle, msg);
                            return;
                        }
                    }
                };

                let workdir = match app_handle.path().app_local_data_dir() {
                    Ok(dir) => sidecar_workdir(
                        debug,
                        &Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(".."),
                        &dir,
                    ),
                    Err(err) => {
                        report_backend_failure(
                            &app_handle,
                            format!("Failed to resolve app local data directory: {err}"),
                        );
                        return;
                    }
                };
                if let Err(err) = std::fs::create_dir_all(&workdir) {
                    report_backend_failure(
                        &app_handle,
                        format!(
                            "Failed to create backend directory {}: {err}",
                            workdir.display()
                        ),
                    );
                    return;
                }

                let mut child = match Command::new(&cmd_str)
                    .args(args)
                    .current_dir(&workdir)
                    .env("PORT", port.to_string())
                    .env("X_LOCAL_ACCESS_TOKEN", &token_for_spawn)
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                {
                    Ok(child) => child,
                    Err(err) => {
                        report_backend_failure(
                            &app_handle,
                            format!("Failed to start backend ({cmd_str}): {err}"),
                        );
                        return;
                    }
                };

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
                if let Some(stdout) = child.stdout.take() {
                    tauri::async_runtime::spawn_blocking(move || {
                        let reader = BufReader::new(stdout);
                        for l in reader.lines().map_while(Result::ok) {
                            println!("[BACKEND] {}", l);
                        }
                    });
                }

                // P10-2: Drain stderr to prevent process hangs if the buffer fills
                if let Some(stderr) = child.stderr.take() {
                    tauri::async_runtime::spawn_blocking(move || {
                        let reader = BufReader::new(stderr);
                        for l in reader.lines().map_while(Result::ok) {
                            eprintln!("[BACKEND ERROR] {}", l);
                        }
                    });
                }

                // The child lives in managed state so the Exit hook can kill it; the
                // watcher polls try_wait (a blocking wait() would hold the lock) and
                // stops quietly once the Exit hook has taken the child.
                if let Ok(mut slot) = app_handle.state::<BackendProc>().child.lock() {
                    *slot = Some(child);
                }
                // P10-1: keep the poll off the async threads
                tauri::async_runtime::spawn_blocking(move || loop {
                    std::thread::sleep(std::time::Duration::from_millis(500));
                    let code = {
                        let state = app_handle.state::<BackendProc>();
                        let Ok(mut slot) = state.child.lock() else {
                            return;
                        };
                        let Some(child) = slot.as_mut() else {
                            return;
                        };
                        match child.try_wait() {
                            Ok(None) => continue,
                            Ok(Some(status)) => status.code(),
                            Err(_) => None,
                        }
                    };
                    app_handle
                        .state::<BackendProc>()
                        .child
                        .lock()
                        .ok()
                        .and_then(|mut slot| slot.take());
                    report_backend_failure(
                        &app_handle,
                        format!("Backend exited unexpectedly (exit code {code:?})"),
                    );
                    return;
                });
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_backend_info,
            get_backend_error,
            open_file
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Windows also has the job object; this covers dev on every platform.
            if let tauri::RunEvent::Exit = event {
                kill_backend(app);
            }
        });
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

#[tauri::command]
fn get_backend_error(state: tauri::State<'_, BackendProc>) -> Option<String> {
    state.error.lock().ok().and_then(|slot| slot.clone())
}

/// Types that are safe to hand to the OS default handler: plain documents, media
/// and text. Deliberately absent: anything that runs code or script (exe, bat, js,
/// hta, lnk, chm, msc, ...), macro-enabled Office (docm/xlsm/pptm), and html/svg
/// (script in the browser). Code files PMA indexes (.py etc.) are revealed, not run.
const OPENABLE_EXTENSIONS: &[&str] = &[
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt",
    "md", "log", "epub", "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "mp3", "wav",
    "flac", "m4a", "mp4", "mkv", "webm", "mov",
];

#[derive(Debug, PartialEq)]
enum FileAction {
    /// Hand to the default application.
    Open(PathBuf),
    /// Show in Explorer with the file selected; never executed.
    Reveal(PathBuf),
}

/// UNC and device paths (`\\server\share`, `\\?\UNC\...`, `\\.\...`). Canonicalizing
/// one makes Windows open an outbound SMB connection, which leaks NTLM credentials
/// to whoever controls the host. `\\?\C:\...` is a local path and stays allowed.
fn is_unc_or_device_path(path: &str) -> bool {
    let p = path.replace('/', "\\");
    if !p.starts_with("\\\\") {
        return false;
    }
    let b = p.strip_prefix("\\\\?\\").unwrap_or("").as_bytes();
    !(b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':')
}

/// `file.txt:stream` addresses an alternate data stream; trailing dot/space names
/// are normalised away by Win32, so they can name a different file than they show.
fn has_unsafe_file_name(name: &str) -> bool {
    name.contains(':') || name.ends_with('.') || name.ends_with(' ')
}

/// Pure decision on a canonical path: allowlisted type -> open, anything else -> reveal.
fn file_action_for(real: &Path) -> Result<FileAction, String> {
    let name = real
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or("path has no usable file name")?;
    if has_unsafe_file_name(name) {
        return Err(format!("refusing unsafe file name: {name}"));
    }
    let ext = real
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase)
        .unwrap_or_default();
    Ok(if OPENABLE_EXTENSIONS.contains(&ext.as_str()) {
        FileAction::Open(real.to_path_buf())
    } else {
        FileAction::Reveal(real.to_path_buf())
    })
}

/// Only an existing, absolute, local regular file is acted on: no URLs, no UNC, no
/// directories. The returned path is the canonical one, so what was checked is what
/// is opened. `tauri-plugin-shell`'s JS `open` is URL-only by default.
fn plan_file_action(path: &str) -> Result<FileAction, String> {
    if is_unc_or_device_path(path) {
        return Err("refusing UNC or device path".into());
    }
    let p = PathBuf::from(path);
    if !p.is_absolute() {
        return Err("not an absolute file path".into());
    }
    // `:` is legal only in the drive prefix (`C:` / `\\?\C:`).
    let after_drive = path.strip_prefix("\\\\?\\").unwrap_or(path);
    if after_drive.get(2..).is_some_and(|rest| rest.contains(':')) {
        return Err("refusing path containing ':' (alternate data stream)".into());
    }
    let real = std::fs::canonicalize(&p).map_err(|e| format!("cannot open {path}: {e}"))?;
    if !real.is_file() {
        return Err(format!("not a file: {path}"));
    }
    file_action_for(&real)
}

/// ShellExecute and Explorer handle `C:\x` better than the `\\?\C:\x` form canonicalize returns.
fn display_path(p: &Path) -> String {
    let s = p.to_string_lossy();
    match s.strip_prefix("\\\\?\\") {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => rest.to_string(),
        _ => s.into_owned(),
    }
}

#[tauri::command]
fn open_file(app: tauri::AppHandle, path: String) -> Result<&'static str, String> {
    use tauri_plugin_shell::ShellExt;
    match plan_file_action(&path)? {
        FileAction::Open(real) => {
            #[allow(deprecated)]
            // Rust-side call skips the JS URL-only scope; the checks above replace it
            app.shell()
                .open(display_path(&real), None)
                .map_err(|e| e.to_string())?;
            Ok("opened")
        }
        FileAction::Reveal(real) => {
            // No shell: the path is its own argv entry. Explorer exits 1 on success, so don't read the status.
            std::process::Command::new("explorer.exe")
                .arg("/select,")
                .arg(display_path(&real))
                .spawn()
                .map_err(|e| format!("cannot reveal {path}: {e}"))?;
            Ok("revealed")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_openable_file_path_accepts_documents_only() {
        let dir = std::env::temp_dir().join(format!("test_open_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let doc = dir.join("report.PDF");
        let exe = dir.join("Setup.EXE");
        std::fs::write(&doc, "x").unwrap();
        std::fs::write(&exe, "x").unwrap();

        // Whatever is planned is the canonical path, not the raw input.
        let real_doc = std::fs::canonicalize(&doc).unwrap();
        assert_eq!(
            plan_file_action(doc.to_str().unwrap()).unwrap(),
            FileAction::Open(real_doc)
        );
        assert!(matches!(
            plan_file_action(exe.to_str().unwrap()),
            Ok(FileAction::Reveal(_))
        ));
        assert!(plan_file_action(dir.to_str().unwrap()).is_err());
        assert!(plan_file_action(dir.join("missing.pdf").to_str().unwrap()).is_err());
        assert!(plan_file_action("https://example.com/a.pdf").is_err());
        assert!(plan_file_action("report.pdf").is_err());

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn test_file_action_allowlist_opens_documents_reveals_everything_else() {
        for name in ["a.pdf", "a.DOCX", "a.xlsx", "a.csv", "a.md", "a.txt", "a.png", "a.mp4"] {
            let p = PathBuf::from(format!("C:/docs/{name}"));
            assert!(matches!(file_action_for(&p), Ok(FileAction::Open(_))), "{name}");
        }
        for name in [
            "a.py", "a.chm", "a.msc", "a.exe", "a.docm", "a.xlsm", "a.pptm", "a.html", "a.svg",
            "a.bat", "a.lnk", "a.js", "noext",
        ] {
            let p = PathBuf::from(format!("C:/docs/{name}"));
            assert!(matches!(file_action_for(&p), Ok(FileAction::Reveal(_))), "{name}");
        }
    }

    #[test]
    fn test_unc_and_device_paths_rejected_before_canonicalize() {
        for p in [
            r"\\server\share\a.pdf",
            "//server/share/a.pdf",
            r"\\?\UNC\server\share\a.pdf",
            r"\\.\C:\a.pdf",
            r"\\.\pipe\x",
        ] {
            assert!(is_unc_or_device_path(p), "{p}");
            assert!(plan_file_action(p).is_err(), "{p}");
        }
        assert!(!is_unc_or_device_path(r"C:\docs\a.pdf"));
        assert!(!is_unc_or_device_path(r"\\?\C:\docs\a.pdf"));
    }

    #[test]
    fn test_alternate_data_streams_and_trailing_dot_space_rejected() {
        assert!(plan_file_action(r"C:\docs\a.txt:hidden.exe").is_err());
        assert!(plan_file_action(r"\\?\C:\docs\a.txt:hidden").is_err());
        assert!(file_action_for(Path::new("C:/docs/a.txt:evil")).is_err());
        assert!(file_action_for(Path::new("C:/docs/a.pdf.")).is_err());
        assert!(file_action_for(Path::new("C:/docs/a.pdf ")).is_err());
    }

    #[test]
    fn test_resolve_sidecar_without_marker_is_not_reused() {
        let temp_dir = std::env::temp_dir().join(format!("test_no_marker_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");
        let python_dir = resource_dir.join("python");
        std::fs::create_dir_all(&python_dir).unwrap();
        std::fs::write(python_dir.join("PMA-sidecar.zip"), "not-a-zip").unwrap();
        let extract_dir = app_local_data_dir
            .join("sidecar")
            .join(env!("CARGO_PKG_VERSION"));
        let exe = extract_dir.join("PMA").join("PMA.exe");
        std::fs::create_dir_all(exe.parent().unwrap()).unwrap();
        std::fs::write(&exe, "half-extracted").unwrap();

        // exe present, marker absent: re-extract (and the garbage zip fails), not reuse
        assert!(resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir).is_err());
        assert!(!exe.exists());

        let _ = std::fs::remove_dir_all(temp_dir);
    }

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
            .join(env!("CARGO_PKG_VERSION"));
        let extracted_exe = extract_dir.join("PMA").join("PMA.exe");
        std::fs::create_dir_all(extracted_exe.parent().unwrap()).unwrap();
        std::fs::write(&extracted_exe, "mock-extracted-content").unwrap();
        std::fs::write(extract_dir.join(".extracted"), "done").unwrap();

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
            .join(env!("CARGO_PKG_VERSION"));
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

    #[test]
    fn test_sidecar_layout_matches_build_exe_zip() {
        // The resolver's <ver>/PMA/PMA.exe layout depends on the zip prefix the build writes.
        let build_exe = include_str!("../../../scripts/Build-Exe.bat");
        assert!(build_exe.contains(r#"-C "dist\sidecar" PMA"#));
    }

    #[cfg(windows)]
    #[test]
    fn test_resolve_sidecar_extracts_build_exe_layout() {
        // Real zip built the way Build-Exe.bat does (tar -C <parent> PMA), real Expand-Archive.
        let temp_dir = std::env::temp_dir().join(format!("test_real_extract_{}", uuid::Uuid::new_v4()));
        let resource_dir = temp_dir.join("resources");
        let app_local_data_dir = temp_dir.join("local_data");
        let staged = temp_dir.join("sidecar").join("PMA");
        std::fs::create_dir_all(staged.join("_internal")).unwrap();
        std::fs::write(staged.join("PMA.exe"), "mock-exe").unwrap();
        std::fs::write(staged.join("_internal").join("lib.dll"), "mock-dll").unwrap();
        std::fs::create_dir_all(resource_dir.join("python")).unwrap();
        let zip_path = resource_dir.join("python").join("PMA-sidecar.zip");
        let tar = std::process::Command::new("tar")
            .arg("-a")
            .arg("-c")
            .arg("-f")
            .arg(&zip_path)
            .arg("-C")
            .arg(temp_dir.join("sidecar"))
            .arg("PMA")
            .status();
        if !matches!(tar, Ok(status) if status.success()) {
            let _ = std::fs::remove_dir_all(temp_dir);
            return; // no bsdtar on this machine
        }

        let resolved = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir).unwrap();
        assert!(std::path::Path::new(&resolved).exists());
        // second launch must reuse the extraction, not redo it
        let again = resolve_prod_sidecar_paths(&resource_dir, &app_local_data_dir).unwrap();
        assert_eq!(resolved, again);

        let _ = std::fs::remove_dir_all(temp_dir);
    }

    #[test]
    fn test_sidecar_workdir_dev_uses_repo_root_prod_uses_app_data() {
        let repo = std::path::Path::new("/repo");
        let data = std::path::Path::new("/appdata");
        assert_eq!(sidecar_workdir(true, repo, data), repo);
        assert_eq!(sidecar_workdir(false, repo, data), data);
    }

    #[cfg(windows)]
    #[test]
    fn test_second_instance_is_detected() {
        // Handles are never closed, so the second call in this process sees the first's mutex.
        assert!(!another_instance_running());
        assert!(another_instance_running());
    }
}

