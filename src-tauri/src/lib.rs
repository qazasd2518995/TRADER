// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager,
};

/// Store the resource dir path for commands to use
struct ResourceDir(std::path::PathBuf);

/// Shared handle to sidecar stdin
struct SidecarStdin(Arc<Mutex<Option<std::process::ChildStdin>>>);

/// Buffer for RPC responses (id → JSON string), polled by frontend
struct RpcResponseBuffer(Arc<Mutex<HashMap<i64, String>>>);

/// Debug log file
struct DebugLog(Arc<Mutex<std::fs::File>>);

fn debug_log(app: &AppHandle, msg: &str) {
    if let Some(state) = app.try_state::<DebugLog>() {
        let timestamp = chrono_lite_now();
        let line = format!("[{}] {}\n", timestamp, msg);
        if let Ok(mut f) = state.0.lock() {
            let _ = Write::write_all(&mut *f, line.as_bytes());
            let _ = Write::flush(&mut *f);
        }
    }
}

fn chrono_lite_now() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs();
    let hours = (secs / 3600) % 24;
    let mins = (secs / 60) % 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", hours, mins, s)
}

#[tauri::command]
fn send_to_sidecar(
    state: tauri::State<SidecarStdin>,
    app: AppHandle,
    message: String,
) -> Result<(), String> {
    debug_log(&app, &format!("send_to_sidecar: {}", &message[..message.len().min(200)]));
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut stdin) = *guard {
        writeln!(stdin, "{}", message).map_err(|e| e.to_string())?;
        stdin.flush().map_err(|e| e.to_string())?;
        Ok(())
    } else {
        Err("Sidecar not running".into())
    }
}

/// Poll for an RPC response by id. Returns the JSON string or null if not ready.
#[tauri::command]
fn poll_rpc_response(
    state: tauri::State<RpcResponseBuffer>,
    id: i64,
) -> Option<String> {
    let mut buf = state.0.lock().ok()?;
    buf.remove(&id)
}

#[tauri::command]
fn download_ea(state: tauri::State<ResourceDir>) -> Result<String, String> {
    let search_dirs = [
        state.0.join("_up_/mt5_ea"),
        state.0.join("mt5_ea"),
        state.0.clone(),
    ];
    for dir in &search_dirs {
        if !dir.exists() {
            continue;
        }
        for entry in std::fs::read_dir(dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            if path.extension().map_or(false, |ext| ext == "mq5") {
                let desktop = dirs::desktop_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
                let dest = desktop.join(path.file_name().unwrap());
                std::fs::copy(&path, &dest).map_err(|e| e.to_string())?;
                return Ok(dest.to_string_lossy().to_string());
            }
        }
    }
    Err("EA file not found in resources".into())
}

fn spawn_sidecar(app: &AppHandle) {
    let resource_dir = app
        .path()
        .resource_dir()
        .expect("Failed to resolve resource dir");

    debug_log(app, &format!("resource_dir: {:?}", resource_dir));

    // Platform-specific sidecar binary name
    let sidecar_path = if cfg!(target_os = "macos") {
        let path = resource_dir.join("binaries/copy-trader-sidecar-aarch64-apple-darwin");
        if path.exists() {
            path
        } else {
            resource_dir.join("binaries/copy-trader-sidecar")
        }
    } else {
        let path = resource_dir.join("binaries/copy-trader-sidecar-x86_64-pc-windows-msvc.exe");
        if path.exists() {
            path
        } else {
            resource_dir.join("binaries/copy-trader-sidecar.exe")
        }
    };

    debug_log(app, &format!("sidecar_path: {:?}, exists: {}", sidecar_path, sidecar_path.exists()));

    let mut child = match Command::new(&sidecar_path)
        .current_dir(&resource_dir)
        .env("AWS_ACCESS_KEY_ID", "AKIAUOMPNW33K56GXQDV")
        .env("AWS_SECRET_ACCESS_KEY", "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")
        .env("AWS_DEFAULT_REGION", "ap-southeast-2")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => {
            debug_log(app, "Sidecar spawned successfully");
            c
        }
        Err(e) => {
            debug_log(app, &format!("FAILED to spawn sidecar: {}", e));
            return;
        }
    };

    let child_stdin = child.stdin.take().expect("Failed to open sidecar stdin");
    let stdin_state = app.state::<SidecarStdin>();
    *stdin_state.0.lock().unwrap() = Some(child_stdin);

    let stdout = child.stdout.take().expect("Failed to open sidecar stdout");
    let stderr = child.stderr.take().expect("Failed to open sidecar stderr");
    let app_handle = app.clone();
    let app_handle2 = app.clone();
    let rpc_buf = app.state::<RpcResponseBuffer>().0.clone();

    // Read stdout — RPC responses go to buffer, push events go to emit
    std::thread::spawn(move || {
        debug_log(&app_handle, "Stdout reader thread started");
        let reader = BufReader::new(stdout);
        let mut line_count = 0u64;
        for line in reader.lines() {
            match line {
                Ok(l) if !l.is_empty() => {
                    line_count += 1;

                    // Check if this is an RPC response (has "id" field at top level)
                    let is_rpc = l.starts_with("{\"id\"");

                    if line_count <= 20 || is_rpc {
                        debug_log(&app_handle, &format!(
                            "stdout #{}{}: {}",
                            line_count,
                            if is_rpc { " [RPC]" } else { "" },
                            &l[..l.len().min(300)]
                        ));
                    }
                    if line_count % 100 == 0 {
                        debug_log(&app_handle, &format!("stdout heartbeat: {} lines", line_count));
                    }

                    if is_rpc {
                        // Extract id and store in buffer for frontend polling
                        if let Some(id) = extract_rpc_id(&l) {
                            if let Ok(mut buf) = rpc_buf.lock() {
                                buf.insert(id, l.clone());
                                debug_log(&app_handle, &format!("RPC response #{} buffered", id));
                            }
                        }
                    }

                    // Also emit all lines as events (push events need this)
                    let _ = app_handle.emit("sidecar-event", &l);
                }
                Ok(_) => {}
                Err(e) => {
                    debug_log(&app_handle, &format!("stdout READ ERROR: {}", e));
                    break;
                }
            }
        }
        debug_log(&app_handle, &format!("Stdout reader EXITED after {} lines", line_count));
    });

    // Read stderr
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines() {
            match line {
                Ok(l) if !l.is_empty() => {
                    debug_log(&app_handle2, &format!("[sidecar-err] {}", &l[..l.len().min(300)]));
                }
                _ => break,
            }
        }
    });
}

/// Extract "id" field from a JSON string like {"id": 3, "result": ...}
fn extract_rpc_id(json: &str) -> Option<i64> {
    // Fast path: parse just enough to find the id
    let id_start = json.find("\"id\"")?;
    let colon = json[id_start..].find(':')?;
    let after_colon = &json[id_start + colon + 1..];
    let trimmed = after_colon.trim_start();
    // Parse the number
    let end = trimmed.find(|c: char| !c.is_ascii_digit() && c != '-')?;
    trimmed[..end].parse::<i64>().ok()
}

fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItemBuilder::with_id("show", "顯示主視窗").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "結束程式").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&show)
        .separator()
        .item(&quit)
        .build()?;

    TrayIconBuilder::new()
        .menu(&menu)
        .tooltip("黃金跟單系統")
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::DoubleClick { .. } = event {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(SidecarStdin(Arc::new(Mutex::new(None))))
        .manage(RpcResponseBuffer(Arc::new(Mutex::new(HashMap::new()))))
        .invoke_handler(tauri::generate_handler![
            send_to_sidecar,
            poll_rpc_response,
            download_ea
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let resource_dir = handle.path().resource_dir().expect("Failed to resolve resource dir");
            app.manage(ResourceDir(resource_dir));

            // Debug log file
            let log_dir = dirs::config_dir()
                .unwrap_or_default()
                .join("黃金跟單系統");
            let _ = std::fs::create_dir_all(&log_dir);
            let log_file = std::fs::OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(log_dir.join("rust_debug.log"))
                .expect("Failed to create debug log");
            app.manage(DebugLog(Arc::new(Mutex::new(log_file))));

            debug_log(&handle, "=== App starting ===");
            setup_tray(&handle)?;
            spawn_sidecar(&handle);
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
