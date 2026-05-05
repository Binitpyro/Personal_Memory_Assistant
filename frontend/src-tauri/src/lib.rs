use tauri::Manager;

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
          use std::process::{Command, Stdio};
          use std::io::{BufRead, BufReader};

          let debug = cfg!(debug_assertions);
          let (cmd_str, args) = if debug {
              // Dev mode: use uv run (no Rust rebuild needed for Python/React changes)
              ("uv".to_string(), vec!["run".to_string(), "app/main.py".to_string()])
          } else {
              // Prod mode: use bundled standalone Python sidecar
              let res_dir = app_handle.path().resource_dir().unwrap();
              let sidecar_path = res_dir.join("python").join("PMA.exe");
              (sidecar_path.to_string_lossy().to_string(), vec![])
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
                  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
              };

              unsafe {
                  let job = winapi::um::jobapi2::CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
                  if !job.is_null() {
                      let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                      info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

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
              for line in reader.lines() {
                  if let Ok(l) = line {
                      println!("[BACKEND] {}", l);
                  }
              }
          });

          // P10-2: Drain stderr to prevent process hangs if the buffer fills
          let stderr = child.stderr.take().unwrap();
          tauri::async_runtime::spawn_blocking(move || {
              let reader = BufReader::new(stderr);
              for line in reader.lines() {
                  if let Ok(l) = line {
                      eprintln!("[BACKEND ERROR] {}", l);
                  }
              }
          });

          // P10-1: Wrap blocking child.wait() in spawn_blocking to prevent async thread starvation
          tauri::async_runtime::spawn_blocking(move || {
              let exit_status = child.wait().expect("Failed to wait on backend sidecar");
              if !exit_status.success() {
                  eprintln!("[BACKEND] Sidecar exited with error: {:?}", exit_status.code());
              }
          });
      });

      Ok(())
    })
    .invoke_handler(tauri::generate_handler![get_backend_info])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}

struct BackendInfo {
    port: u16,
    token: String,
}

#[tauri::command]
fn get_backend_info(state: tauri::State<'_, BackendInfo>) -> (u16, String) {
    (state.port, state.token.clone())
}
