use std::process::{Command, Stdio, Child};
use std::path::PathBuf;
use std::io::{Write, BufRead, BufReader};
use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use tauri::{Manager, AppHandle, Emitter};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use std::sync::{Mutex, Arc};
use std::sync::atomic::{AtomicU64, AtomicBool, Ordering};
use std::thread;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
// =============================================================================
// RATE LIMITER
// =============================================================================

/// Rate limiter to prevent command spam from frontend
static RATE_LIMITER: Mutex<Option<HashMap<String, Instant>>> = Mutex::new(None);

/// Rate limit configuration (command -> minimum interval in milliseconds)
fn get_rate_limit_ms(command: &str) -> Option<u64> {
    match command {
        "send_message" => Some(500),
        "generate_speech" => Some(100),  // Reduced from 1000ms to allow faster TTS prefetch
        "create_qube" => Some(5000),
        "anchor_session" => Some(2000),
        "analyze_image" => Some(1000),
        "mint_qube" => Some(5000),
        "save_api_key" => Some(1000),
        _ => None, // No rate limit for other commands
    }
}

/// Check if a command is rate limited. Returns Ok(()) if allowed, Err with message if blocked.
fn check_rate_limit(command: &str) -> Result<(), String> {
    let min_interval_ms = match get_rate_limit_ms(command) {
        Some(ms) => ms,
        None => return Ok(()), // No rate limit configured
    };

    let mut limiter_guard = RATE_LIMITER.lock().map_err(|_| "Rate limiter lock poisoned")?;
    let limiter = limiter_guard.get_or_insert_with(HashMap::new);

    let now = Instant::now();

    if let Some(last_call) = limiter.get(command) {
        let elapsed = now.duration_since(*last_call);
        if elapsed < Duration::from_millis(min_interval_ms) {
            let wait_ms = min_interval_ms - elapsed.as_millis() as u64;
            return Err(format!(
                "Please wait {} ms before retrying this action",
                wait_ms
            ));
        }
    }

    limiter.insert(command.to_string(), now);
    Ok(())
}

// =============================================================================
// PERSISTENT SIDECAR PROCESS
// =============================================================================

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);
static SIDECAR_READY: AtomicBool = AtomicBool::new(false);

struct SidecarProcess {
    child: Child,
    stdin_tx: std::sync::mpsc::Sender<String>,
    pending: Arc<Mutex<HashMap<String, PendingRequest>>>,
    _reader_thread: Option<thread::JoinHandle<()>>,
    _writer_thread: Option<thread::JoinHandle<()>>,
}

struct PendingRequest {
    response_tx: tokio::sync::oneshot::Sender<Result<serde_json::Value, String>>,
    app_handle: Option<AppHandle>,
}

static SIDECAR_PROCESS: Mutex<Option<SidecarProcess>> = Mutex::new(None);

fn start_sidecar(app_handle: &AppHandle) -> Result<(), String> {
    // Hold the lock through the entire spawn+store to prevent TOCTOU races.
    // Multiple threads calling start_sidecar() concurrently will serialize here.
    let mut guard = SIDECAR_PROCESS.lock().map_err(|_| "Sidecar lock poisoned")?;
    if guard.is_some() { return Ok(()); }

    eprintln!("[SIDECAR] Starting persistent backend process...");

    let (mut cmd, is_bundled) = create_backend_command();
    let project_root = get_python_project_path();
    cmd.current_dir(&project_root);

    if is_bundled {
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                let hf_models = exe_dir.join("models").join("huggingface");
                if hf_models.exists() { cmd.env("HF_HOME", &hf_models); }
            }
        }
    }

    if !is_bundled {
        let bridge_path = get_python_bridge_path();
        if !bridge_path.exists() {
            return Err(format!("Python bridge not found at: {}", bridge_path.display()));
        }
        cmd.arg(&bridge_path);
    }

    cmd.arg("server")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("[SIDECAR] Failed to spawn: {}", e))?;

    let child_stdin = child.stdin.take()
        .ok_or_else(|| "[SIDECAR] Failed to capture stdin".to_string())?;
    let (stdin_tx, stdin_rx) = std::sync::mpsc::channel::<String>();

    let writer_thread = thread::spawn(move || {
        let mut writer = std::io::BufWriter::new(child_stdin);
        while let Ok(line) = stdin_rx.recv() {
            if writer.write_all(line.as_bytes()).is_err() { break; }
            if writer.write_all(b"\n").is_err() { break; }
            if writer.flush().is_err() { break; }
        }
        // Mark sidecar as not ready so new requests fail fast instead of timing out
        eprintln!("[SIDECAR] Writer thread exiting — stdin pipe broken");
        SIDECAR_READY.store(false, Ordering::SeqCst);
    });

    let child_stdout = child.stdout.take()
        .ok_or_else(|| "[SIDECAR] Failed to capture stdout".to_string())?;

    if let Some(child_stderr) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(child_stderr);
            for line in reader.lines() {
                match line {
                    Ok(l) => eprintln!("[SIDECAR stderr] {}", l),
                    Err(_) => break,
                }
            }
        });
    }

    let pending: Arc<Mutex<HashMap<String, PendingRequest>>> =
        Arc::new(Mutex::new(HashMap::new()));
    let pending_clone = Arc::clone(&pending);
    let app_handle_clone = app_handle.clone();

    let reader_thread = thread::spawn(move || {
        let reader = BufReader::new(child_stdout);
        for line in reader.lines() {
            match line {
                Ok(json_line) => {
                    if json_line.trim().is_empty() { continue; }
                    let parsed: serde_json::Value = match serde_json::from_str(&json_line) {
                        Ok(v) => v,
                        Err(e) => { eprintln!("[SIDECAR] Parse error: {} - {}", e, json_line); continue; }
                    };

                    if parsed.get("ready").and_then(|v| v.as_bool()).unwrap_or(false) {
                        eprintln!("[SIDECAR] Backend is ready");
                        SIDECAR_READY.store(true, Ordering::SeqCst);
                        continue;
                    }

                    let req_id = match parsed.get("id").and_then(|v| v.as_str()) {
                        Some(id) => id.to_string(),
                        None => { eprintln!("[SIDECAR] Response missing id"); continue; }
                    };

                    if let Some(stream_type) = parsed.get("stream").and_then(|v| v.as_str()) {
                        if let Some(data) = parsed.get("data") {
                            let event_name = match stream_type {
                                "tool_call" => "tool-call-event",
                                "chain_state_event" => "chain-state-event",
                                _ => stream_type,
                            };
                            let handle = {
                                let guard = pending_clone.lock().ok();
                                guard.and_then(|g| g.get(&req_id).and_then(|p| p.app_handle.clone()))
                            };
                            let handle = handle.as_ref().unwrap_or(&app_handle_clone);
                            let _ = handle.emit(event_name, data);
                        }
                        continue;
                    }

                    let mut pguard = match pending_clone.lock() { Ok(g) => g, Err(_) => continue };
                    if let Some(pending_req) = pguard.remove(&req_id) {
                        if let Some(error) = parsed.get("error") {
                            // Tag as application error so retry logic doesn't mask it
                            let err_msg = format!("[APP] {}", error.as_str().unwrap_or("Unknown sidecar error"));
                            let _ = pending_req.response_tx.send(Err(err_msg));
                        } else if let Some(result) = parsed.get("result") {
                            let _ = pending_req.response_tx.send(Ok(result.clone()));
                        } else {
                            let _ = pending_req.response_tx.send(Err("Missing result/error".to_string()));
                        }
                    }
                }
                Err(e) => { eprintln!("[SIDECAR] Read error: {}", e); break; }
            }
        }
        SIDECAR_READY.store(false, Ordering::SeqCst);
        if let Ok(mut pguard) = pending_clone.lock() {
            for (_, p) in pguard.drain() {
                let _ = p.response_tx.send(Err("Sidecar process terminated".to_string()));
            }
        }
    });

    // Store the sidecar while still holding the lock — prevents TOCTOU race
    *guard = Some(SidecarProcess {
        child,
        stdin_tx,
        pending,
        _reader_thread: Some(reader_thread),
        _writer_thread: Some(writer_thread),
    });

    // Release the lock before the ready-wait so other operations aren't blocked
    drop(guard);

    let start = Instant::now();
    while !SIDECAR_READY.load(Ordering::SeqCst) {
        if start.elapsed() > Duration::from_secs(30) {
            stop_sidecar();
            return Err("[SIDECAR] Timed out waiting for ready signal".to_string());
        }
        thread::sleep(Duration::from_millis(50));
    }

    eprintln!("[SIDECAR] Started in {:?}", start.elapsed());
    Ok(())
}

fn stop_sidecar() {
    SIDECAR_READY.store(false, Ordering::SeqCst);
    let mut guard = match SIDECAR_PROCESS.lock() { Ok(g) => g, Err(_) => return };
    if let Some(mut sidecar) = guard.take() {
        eprintln!("[SIDECAR] Shutting down...");
        let _ = sidecar.stdin_tx.send(r#"{"id":"shutdown","command":"shutdown"}"#.to_string());
        thread::sleep(Duration::from_millis(500));
        let _ = sidecar.child.kill();
        match sidecar.child.wait() {
            Ok(status) => eprintln!("[SIDECAR] Stopped with exit status: {}", status),
            Err(e) => eprintln!("[SIDECAR] Stopped (wait error: {})", e),
        }
    }
}

async fn sidecar_execute(
    command: &str,
    args: &[String],
    secrets: &HashMap<&str, &str>,
    app_handle: Option<&AppHandle>,
    timeout_secs: Option<u64>,
) -> Result<serde_json::Value, String> {
    if !SIDECAR_READY.load(Ordering::SeqCst) {
        return Err("Sidecar not ready".to_string());
    }

    let req_id = format!("req_{}", REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst));
    let request = serde_json::json!({
        "id": req_id, "command": command, "args": args, "secrets": secrets,
    });
    let request_line = serde_json::to_string(&request)
        .map_err(|e| format!("Failed to serialize request: {}", e))?;

    let (tx, rx) = tokio::sync::oneshot::channel();
    let stdin_tx = {
        let guard = SIDECAR_PROCESS.lock().map_err(|_| "Sidecar lock poisoned")?;
        let sidecar = guard.as_ref().ok_or_else(|| "Sidecar not running".to_string())?;
        {
            let mut pending = sidecar.pending.lock().map_err(|_| "Pending lock poisoned")?;
            pending.insert(req_id.clone(), PendingRequest {
                response_tx: tx, app_handle: app_handle.cloned(),
            });
        }
        sidecar.stdin_tx.clone()
    };

    stdin_tx.send(request_line)
        .map_err(|_| "Failed to send to sidecar stdin".to_string())?;

    let timeout = Duration::from_secs(timeout_secs.unwrap_or(60));
    match tokio::time::timeout(timeout, rx).await {
        Ok(Ok(result)) => result,
        Ok(Err(_)) => Err("Sidecar connection lost".to_string()),
        Err(_) => {
            if let Ok(guard) = SIDECAR_PROCESS.lock() {
                if let Some(sidecar) = guard.as_ref() {
                    if let Ok(mut pending) = sidecar.pending.lock() {
                        pending.remove(&req_id);
                    }
                }
            }
            Err(format!("Sidecar command '{}' timed out after {}s", command, timeout.as_secs()))
        }
    }
}

async fn sidecar_execute_with_retry(
    command: &str,
    args: Vec<String>,
    secrets: HashMap<&str, &str>,
    app_handle: Option<&AppHandle>,
    timeout_secs: Option<u64>,
) -> Result<serde_json::Value, String> {
    match sidecar_execute(command, &args, &secrets, app_handle, timeout_secs).await {
        Ok(result) => return Ok(result),
        Err(e) => {
            // Application errors (from Python handler raising) should propagate directly.
            // Only retry/fallback on sidecar infrastructure errors.
            if e.starts_with("[APP] ") {
                return Err(e[6..].to_string());
            }
            eprintln!("[SIDECAR] Command '{}' failed: {} — retrying...", command, e);
        }
    }

    if let Some(handle) = app_handle {
        stop_sidecar();
        if let Err(e) = start_sidecar(handle) {
            eprintln!("[SIDECAR] Restart failed: {} — falling back to subprocess", e);
            return fallback_subprocess_execute(command, &args, &secrets, app_handle).await;
        }
        match sidecar_execute(command, &args, &secrets, app_handle, timeout_secs).await {
            Ok(result) => return Ok(result),
            Err(e) => {
                if e.starts_with("[APP] ") {
                    return Err(e[6..].to_string());
                }
                eprintln!("[SIDECAR] Retry failed: {} — falling back to subprocess", e);
            }
        }
    }

    fallback_subprocess_execute(command, &args, &secrets, app_handle).await
}

async fn fallback_subprocess_execute(
    command: &str,
    args: &[String],
    secrets: &HashMap<&str, &str>,
    app_handle: Option<&AppHandle>,
) -> Result<serde_json::Value, String> {
    eprintln!("[SIDECAR FALLBACK] Executing '{}' via subprocess", command);
    let mut cmd = prepare_backend_command()?;
    cmd.arg(command);
    for arg in args { cmd.arg(arg); }
    let secrets_owned: HashMap<&str, &str> = secrets.clone();

    let (stdout, _stderr) = if let Some(handle) = app_handle {
        execute_with_secrets_streaming(cmd, secrets_owned, handle)?
    } else {
        execute_with_secrets(cmd, secrets_owned)?
    };

    serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse subprocess response: {}. Output: {}", e, stdout))
}

// Helper functions to find Python and gui_bridge paths
fn find_python_path() -> Result<String, String> {
    // Try common Python paths
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;

        // Try python from PATH first
        if Command::new("python")
            .arg("--version")
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .is_ok()
        {
            return Ok("python".to_string());
        }
        // Try py launcher
        if Command::new("py")
            .arg("--version")
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .is_ok()
        {
            return Ok("py".to_string());
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        // Try venv python first (ensures correct packages are available)
        let project_root = get_python_project_path();
        let venv_python = project_root.join("venv").join("bin").join("python3");
        if venv_python.exists() {
            return Ok(venv_python.to_string_lossy().to_string());
        }

        // Try python3 from PATH, then python
        if Command::new("python3").arg("--version").output().is_ok() {
            return Ok("python3".to_string());
        }
        if Command::new("python").arg("--version").output().is_ok() {
            return Ok("python".to_string());
        }
    }

    Err("Python not found".to_string())
}

// =============================================================================
// ERROR SANITIZATION
// =============================================================================

/// Sanitize backend error messages to avoid leaking internal details to the frontend.
/// Logs the full error for debugging while returning a user-friendly message.
fn sanitize_backend_error(raw_error: &str, context: &str) -> String {
    // Log full error for debugging (will appear in Tauri console/logs)
    eprintln!("[BACKEND ERROR] {}: {}", context, raw_error);

    let error_lower = raw_error.to_lowercase();

    // Authentication errors - pass through (user needs to know)
    if error_lower.contains("invalid password")
        || error_lower.contains("authentication failed")
        || error_lower.contains("wrong password")
    {
        return "Invalid password".to_string();
    }

    // Not found errors - make generic but informative
    if error_lower.contains("not found") {
        if error_lower.contains("qube") {
            return "Qube not found".to_string();
        }
        if error_lower.contains("user") {
            return "User not found".to_string();
        }
        if error_lower.contains("file") || error_lower.contains("path") {
            return "File not found".to_string();
        }
        return format!("{} not found", context);
    }

    // Timeout errors
    if error_lower.contains("timeout") || error_lower.contains("timed out") {
        return "Operation timed out. Please try again.".to_string();
    }

    // API/Network errors - be specific to avoid matching log messages like "api_key_load_failed"
    if error_lower.contains("api request failed")
        || error_lower.contains("api error")
        || error_lower.contains("api connection")
        || (error_lower.contains("api") && error_lower.contains("unreachable"))
    {
        return "API request failed. Please check your connection and try again.".to_string();
    }

    if error_lower.contains("connection") || error_lower.contains("network") {
        return "Network error. Please check your connection.".to_string();
    }

    // Rate limit from external APIs
    if error_lower.contains("rate limit") || error_lower.contains("too many requests") {
        return "Rate limited by API. Please wait a moment and try again.".to_string();
    }

    // Permission errors
    if error_lower.contains("permission") || error_lower.contains("access denied") {
        return "Permission denied".to_string();
    }

    // Validation errors - these are often user-facing already
    if error_lower.contains("invalid") || error_lower.contains("validation") {
        // Extract the first line which is usually the user-facing message
        if let Some(first_line) = raw_error.lines().next() {
            if first_line.len() < 200 {
                return first_line.to_string();
            }
        }
        return "Invalid input. Please check your data and try again.".to_string();
    }

    // Blockchain/minting specific errors - can pass through
    if error_lower.contains("insufficient") || error_lower.contains("balance") {
        return "Insufficient funds for this operation".to_string();
    }

    if error_lower.contains("already minted") || error_lower.contains("already registered") {
        return raw_error
            .lines()
            .next()
            .unwrap_or("Already registered")
            .to_string();
    }

    // Generic fallback - don't expose internal details
    format!(
        "{} failed. Please try again or check the logs for details.",
        context
    )
}



/// Generate a secure temporary file path with random suffix
fn secure_temp_path(prefix: &str) -> PathBuf {
    let temp_dir = std::env::temp_dir();
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let pid = std::process::id();
    // Combine PID, timestamp, and a portion of memory address for uniqueness
    let random_suffix = format!("{}_{:x}_{:x}", pid, timestamp, &temp_dir as *const _ as usize);
    temp_dir.join(format!("qubes_{}_{}.tmp", prefix, random_suffix))
}

// Input validation function to prevent path traversal and command injection
fn validate_identifier(input: &str, field_name: &str) -> Result<(), String> {
    // Check for empty input
    if input.is_empty() {
        return Err(format!("{} cannot be empty", field_name));
    }

    // Check for path traversal attempts
    if input.contains("..") || input.contains("/") || input.contains("\\") {
        return Err(format!("{} contains invalid characters (path traversal attempt)", field_name));
    }

    // Check for null bytes or other control characters
    if input.chars().any(|c| c.is_control()) {
        return Err(format!("{} contains invalid control characters", field_name));
    }

    // Limit length to prevent DoS
    if input.len() > 256 {
        return Err(format!("{} is too long (max 256 characters)", field_name));
    }

    Ok(())
}

#[derive(Debug, Serialize, Deserialize)]
struct AuthResponse {
    success: bool,
    user_id: Option<String>,
    data_dir: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ModelInfo {
    value: String,
    label: String,
    description: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ProviderInfo {
    value: String,
    label: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct AvailableModelsResponse {
    providers: Vec<ProviderInfo>,
    models: std::collections::HashMap<String, Vec<ModelInfo>>,
    defaults: std::collections::HashMap<String, String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChatResponse {
    success: bool,
    qube_id: Option<String>,
    qube_name: Option<String>,
    message: Option<String>,
    response: Option<String>,
    timestamp: Option<i64>,  // Unix timestamp in seconds from MESSAGE block
    block_number: Option<i64>,  // Sequence number for ACTION block association
    current_model: Option<String>,
    current_provider: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct SpeechResponse {
    success: bool,
    audio_path: Option<String>,
    total_chunks: Option<i32>,
    qube_id: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct DeleteResponse {
    success: bool,
    qube_id: Option<String>,
    swept_sats: Option<i64>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct SaveImageResponse {
    success: bool,
    qube_id: Option<String>,
    file_path: Option<String>,
    filename: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct UploadAvatarToIpfsResponse {
    success: bool,
    qube_id: Option<String>,
    qube_name: Option<String>,
    avatar_ipfs_cid: Option<String>,
    ipfs_gateway_url: Option<String>,
    message: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct AnalyzeImageResponse {
    success: bool,
    qube_id: Option<String>,
    description: Option<String>,
    error: Option<String>,
}



#[derive(Debug, Serialize, Deserialize)]
struct ConfiguredKeysResponse {
    providers: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct APIKeyResponse {
    success: bool,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ValidationResponse {
    valid: bool,
    message: String,
    details: Option<serde_json::Value>,
}

// P2P Response Structs
#[derive(Debug, Serialize, Deserialize)]
struct OnlineQubesResponse {
    success: bool,
    online: Option<Vec<String>>,
    count: Option<i32>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct IntroductionResponse {
    success: bool,
    relay_id: Option<String>,
    status: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct PendingIntroduction {
    relay_id: String,
    from_commitment: String,
    from_name: String,
    conversation_id: String,
    block_hash: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct PendingIntroductionsResponse {
    success: bool,
    pending: Option<Vec<PendingIntroduction>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct AcceptIntroductionResponse {
    success: bool,
    from_name: Option<String>,
    from_commitment: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct GenerateIntroductionResponse {
    success: bool,
    message: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct EvaluateIntroductionResponse {
    success: bool,
    recommendation: Option<String>,
    reasoning: Option<String>,
    response_message: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ProcessP2PMessageResponse {
    success: bool,
    response: Option<String>,
    model_used: Option<String>,
    usage: Option<serde_json::Value>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct Connection {
    commitment: String,
    name: String,
    accepted_at: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct ConnectionsResponse {
    success: bool,
    connections: Option<Vec<Connection>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct P2PSessionResponse {
    success: bool,
    session_id: Option<String>,
    participants: Option<Vec<serde_json::Value>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct P2PSessionsResponse {
    success: bool,
    sessions: Option<Vec<serde_json::Value>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct BlockPreferencesResponse {
    individual_auto_anchor: bool,
    individual_anchor_threshold: i32,
    group_auto_anchor: bool,
    group_anchor_threshold: i32,
    auto_sync_ipfs_on_anchor: bool,
    auto_sync_ipfs_periodic: bool,
    auto_sync_ipfs_interval: i32,
}

#[derive(Debug, Serialize, Deserialize)]
struct RelationshipSettingsResponse {
    difficulty: String,
    description: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct GoogleTTSPathResponse {
    path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct SetPathResponse {
    success: bool,
    path: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct SkillsResponse {
    success: bool,
    qube_id: Option<String>,
    skills: Option<Vec<serde_json::Value>>,
    last_updated: Option<String>,
    summary: Option<serde_json::Value>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct OwnerInfoResponse {
    success: bool,
    owner_info: Option<serde_json::Value>,
    summary: Option<serde_json::Value>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct DifficultyPreset {
    name: String,
    description: String,
    min_interactions: MinInteractionsMap,
}

#[derive(Debug, Serialize, Deserialize)]
struct MinInteractionsMap {
    acquaintance: i32,
    friend: i32,
    close_friend: i32,
    best_friend: i32,
}

#[derive(Debug, Serialize, Deserialize)]
struct RelationshipResponse {
    success: bool,
    relationships: Vec<Relationship>,
    stats: serde_json::Value,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct Relationship {
    entity_id: String,
    entity_name: String,
    entity_type: String,
    status: String,
    trust: f32,
    has_met: bool,
    is_best_friend: bool,
    // Core Trust Metrics (5) - foundational earned qualities
    honesty: f32,
    reliability: f32,
    support: f32,
    loyalty: f32,
    respect: f32,
    // Social Metrics - Positive (14)
    friendship: f32,
    affection: f32,
    engagement: f32,
    depth: f32,
    humor: f32,
    understanding: f32,
    compatibility: f32,
    admiration: f32,
    warmth: f32,
    openness: f32,
    patience: f32,
    empowerment: f32,
    responsiveness: f32,
    expertise: f32,
    // Social Metrics - Negative (10)
    antagonism: f32,
    resentment: f32,
    annoyance: f32,
    distrust: f32,
    rivalry: f32,
    tension: f32,
    condescension: f32,
    manipulation: f32,
    dismissiveness: f32,
    betrayal: f32,
    // Tracked Statistics
    messages_sent: u32,
    messages_received: u32,
    collaborations_successful: u32,
    collaborations_failed: u32,
    first_contact: Option<u64>,
    last_interaction: Option<u64>,
    days_known: u32,
    // Clearance System (v2)
    clearance_profile: String,
    clearance_categories: Vec<String>,
    clearance_expires_at: Option<u64>,
    #[serde(default)]
    clearance_field_grants: Vec<String>,
    #[serde(default)]
    clearance_field_denials: Vec<String>,
    // Tags
    #[serde(default)]
    tags: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct VisualizerSettingsResponse {
    enabled: bool,
    waveform_style: i32,
    color_theme: String,
    gradient_style: String,
    sensitivity: i32,
    animation_smoothness: String,
    audio_offset_ms: i32,
    frequency_range: i32,
    #[serde(default)]
    output_monitor: i32,
}

#[derive(Debug, Serialize, Deserialize)]
struct TrustPersonalityResponse {
    trust_profile: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct GenericSuccessResponse {
    success: bool,
    message: Option<String>,
    error: Option<String>,
}

// Model Preferences Response
#[derive(Debug, Serialize, Deserialize)]
struct ModelPreferenceData {
    model: String,
    reason: Option<String>,
    set_at: Option<u64>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct ModelPreferencesResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    preferences: std::collections::HashMap<String, ModelPreferenceData>,
    current_model: Option<String>,
    current_override: Option<String>,
    genesis_model: Option<String>,
    #[serde(default)]
    model_locked: bool,
    locked_to: Option<String>,
    #[serde(default)]
    revolver_mode: bool,
    #[serde(default)]
    revolver_mode_pool: Vec<String>,
    #[serde(default)]
    autonomous_mode: bool,
    #[serde(default)]
    autonomous_mode_pool: Vec<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct ModelLockResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    locked: bool,
    locked_to: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct RevolverModeResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    revolver_mode: bool,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct AutonomousModeResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    enabled: bool,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct RevolverModePoolResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    pool: Vec<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct AutonomousModePoolResponse {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    pool: Vec<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ResetModelResponse {
    success: bool,
    genesis_model: Option<String>,
    error: Option<String>,
}

// Clearance Request System
#[derive(Debug, Serialize, Deserialize)]
struct ClearanceRequestData {
    request_id: String,
    requester_id: String,
    requester_name: String,
    requested_level: String,
    requested_categories: Vec<String>,
    reason: Option<String>,
    status: String,
    created_at: u64,
    expires_at: u64,
    resolved_at: Option<u64>,
    resolved_by: Option<String>,
    denial_reason: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ClearanceRequestsResponse {
    success: bool,
    requests: Vec<ClearanceRequestData>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ClearanceRequestResponse {
    success: bool,
    request: Option<ClearanceRequestData>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct AuditLogResponse {
    success: bool,
    entries: Option<Vec<serde_json::Value>>,
    count: Option<i32>,
    error: Option<String>,
}

// Clearance Profile v2 Structs
#[derive(Debug, Serialize, Deserialize)]
struct ClearanceProfileData {
    name: String,
    level: i32,
    description: String,
    categories: Vec<String>,
    fields: Vec<String>,
    excluded_fields: Vec<String>,
    icon: String,
    color: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct ClearanceProfilesResponse {
    success: bool,
    profiles: Option<std::collections::HashMap<String, ClearanceProfileData>>,
    auto_suggest_enabled: Option<bool>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TagData {
    name: String,
    description: String,
    icon: String,
    color: String,
    is_default: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct TagsResponse {
    success: bool,
    tags: Option<std::collections::HashMap<String, TagData>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TraitDefinitionData {
    name: String,
    category: String,
    description: String,
    icon: String,
    color: String,
    polarity: String,
    #[serde(default)]
    is_warning: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct TraitsResponse {
    success: bool,
    traits: Option<std::collections::HashMap<String, TraitDefinitionData>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TagUpdateResponse {
    success: bool,
    tags: Option<Vec<String>>,
    removed: Option<bool>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ClearanceSuggestionResponse {
    success: bool,
    current_profile: Option<String>,
    suggested_profile: Option<String>,
    reason: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct SetClearanceResponse {
    success: bool,
    clearance_profile: Option<String>,
    field_grants: Option<Vec<String>>,
    field_denials: Option<Vec<String>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct MonitorInfo {
    id: usize,
    name: String,
    width: u32,
    height: u32,
    x: i32,
    y: i32,
    is_primary: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct MonitorsResponse {
    monitors: Vec<MonitorInfo>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TimelineDataPoint {
    block_number: u64,
    timestamp: u64,
    trust: f32,
    compatibility: f32,
    friendship: f32,
    affection: f32,
}

#[derive(Debug, Serialize, Deserialize)]
struct TimelineResponse {
    success: bool,
    timeline: Vec<TimelineDataPoint>,
    error: Option<String>,
}

// Chain Sync Response Types (NFT-Bundled Storage)
#[derive(Debug, Serialize, Deserialize)]
struct SyncToChainResponse {
    success: bool,
    ipfs_cid: Option<String>,
    encrypted_key: Option<String>,
    merkle_root: Option<String>,
    chain_length: Option<u32>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TransferQubeResponse {
    success: bool,
    transfer_txid: Option<String>,
    recipient_address: Option<String>,
    ipfs_cid: Option<String>,
    local_deleted: Option<bool>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ImportFromWalletResponse {
    success: bool,
    qube_id: Option<String>,
    qube_name: Option<String>,
    qube_dir: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ExportQubeResponse {
    success: bool,
    file_path: Option<String>,
    block_count: Option<u32>,
    qube_name: Option<String>,
    qube_id: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ImportQubeResponse {
    success: bool,
    qube_id: Option<String>,
    qube_name: Option<String>,
    block_count: Option<u32>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ExportAccountBackupResponse {
    success: bool,
    file_path: Option<String>,
    qube_count: Option<u32>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ImportAccountBackupResponse {
    success: bool,
    imported_count: Option<u32>,
    skipped_count: Option<u32>,
    user_id: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct PrepareQubeMintResponse {
    pending_id: String,
    qube_id: String,
    wc_transaction: String,
    category_id: String,
    commitment: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct WalletQubeInfo {
    qube_id: String,
    qube_name: String,
    category_id: String,
    ipfs_cid: String,
    chain_length: u32,
    sync_timestamp: u64,
    #[serde(default)]
    already_imported: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct ScanWalletResponse {
    success: bool,
    qubes: Option<Vec<WalletQubeInfo>>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct ResolvePublicKeyResponse {
    success: bool,
    public_key: Option<String>,
    found: bool,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct Qube {
    qube_id: String,
    name: String,
    ai_provider: String,
    ai_model: String,
    genesis_prompt: String,
    favorite_color: String,
    voice_model: Option<String>,
    tts_enabled: Option<bool>,
    creator: Option<String>,
    birth_timestamp: Option<u64>,
    home_blockchain: Option<String>,
    created_at: String,
    trust_score: Option<f32>,
    memory_blocks_count: Option<u32>,
    friends_count: Option<u32>,
    total_relationships: Option<u32>,
    best_friend: Option<String>,
    close_friends: Option<u32>,
    acquaintances: Option<u32>,
    strangers: Option<u32>,
    highest_trust: Option<f32>,
    lowest_trust: Option<f32>,
    total_messages_sent: Option<u32>,
    total_messages_received: Option<u32>,
    total_collaborations: Option<u32>,
    successful_joint_tasks: Option<u32>,
    failed_joint_tasks: Option<u32>,
    avg_reliability: Option<f32>,
    avg_honesty: Option<f32>,
    avg_responsiveness: Option<f32>,
    avg_compatibility: Option<f32>,
    status: String,
    // Blockchain metadata
    nft_category_id: Option<String>,
    mint_txid: Option<String>,
    recipient_address: Option<String>,
    public_key: Option<String>,
    genesis_block_hash: Option<String>,
    commitment: Option<String>,
    bcmr_uri: Option<String>,
    avatar_ipfs_cid: Option<String>,
    avatar_url: Option<String>,
    avatar_local_path: Option<String>,
    network: Option<String>,
    block_breakdown: Option<serde_json::Value>,
    // Wallet fields (P2SH co-signing wallet)
    wallet_address: Option<String>,
    wallet_owner_pubkey: Option<String>,
    wallet_qube_pubkey: Option<String>,
    wallet_owner_q_address: Option<String>,  // Owner's 'q' address (standard BCH)
}

// Check if we're running as a bundled distribution (has qubes-backend sidecar)
fn is_bundled_distribution() -> bool {
    get_bundled_backend_path().is_some()
}

// Diagnostic info for backend path resolution
#[derive(Debug, Clone, Serialize)]
struct BackendDiagnostics {
    exe_path: String,
    exe_dir: String,
    sidecar_name: String,
    paths_checked: Vec<String>,
    found_path: Option<String>,
    is_dev_mode: bool,
    #[cfg(not(target_os = "windows"))]
    is_executable: Option<bool>,
}

// Get path to the bundled backend executable (Tauri sidecar) with diagnostics
fn get_bundled_backend_path_with_diagnostics() -> (Option<PathBuf>, BackendDiagnostics) {
    let mut diagnostics = BackendDiagnostics {
        exe_path: String::new(),
        exe_dir: String::new(),
        sidecar_name: String::new(),
        paths_checked: Vec::new(),
        found_path: None,
        is_dev_mode: cfg!(dev),
        #[cfg(not(target_os = "windows"))]
        is_executable: None,
    };

    // Skip bundled backend in dev mode - Python is much faster for development
    if cfg!(dev) {
        return (None, diagnostics);
    }

    #[cfg(target_os = "windows")]
    let sidecar_name = "qubes-backend.exe";
    #[cfg(not(target_os = "windows"))]
    let sidecar_name = "qubes-backend";

    diagnostics.sidecar_name = sidecar_name.to_string();

    if let Ok(exe_path) = std::env::current_exe() {
        diagnostics.exe_path = exe_path.display().to_string();

        if let Some(exe_dir) = exe_path.parent() {
            diagnostics.exe_dir = exe_dir.display().to_string();

            // Check heavy bundle first: qubes-backend/ subfolder with --onedir output
            // This is the primary distribution format (ZIP bundle with all deps)
            #[cfg(target_os = "windows")]
            let bundle_path = exe_dir.join("qubes-backend").join("qubes-backend.exe");
            #[cfg(not(target_os = "windows"))]
            let bundle_path = exe_dir.join("qubes-backend").join("qubes-backend");

            diagnostics.paths_checked.push(format!("{} (exists: {})", bundle_path.display(), bundle_path.exists()));

            if bundle_path.exists() {
                diagnostics.found_path = Some(bundle_path.display().to_string());
                return (Some(bundle_path), diagnostics);
            }

            // Check for Tauri sidecar (placed next to main exe)
            // Validate file size to reject dummy placeholders from CI builds
            let sidecar_path = exe_dir.join(sidecar_name);
            diagnostics.paths_checked.push(format!("{} (exists: {})", sidecar_path.display(), sidecar_path.exists()));

            if sidecar_path.exists() {
                let is_valid = std::fs::metadata(&sidecar_path)
                    .map(|m| m.len() > 1024) // Real PyInstaller exe is many MB, dummy is ~11 bytes
                    .unwrap_or(false);

                if is_valid {
                    diagnostics.found_path = Some(sidecar_path.display().to_string());

                    // On Unix, check if it's executable
                    #[cfg(not(target_os = "windows"))]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        if let Ok(metadata) = std::fs::metadata(&sidecar_path) {
                            let mode = metadata.permissions().mode();
                            diagnostics.is_executable = Some(mode & 0o111 != 0);
                        }
                    }

                    return (Some(sidecar_path), diagnostics);
                } else {
                    diagnostics.paths_checked.push(format!("{} (skipped: file too small, likely placeholder)", sidecar_path.display()));
                }
            }

            // Also check macOS bundle location (inside .app/Contents/MacOS/)
            #[cfg(target_os = "macos")]
            {
                if let Some(contents_dir) = exe_dir.parent() {
                    let resources_path = contents_dir.join("Resources").join(sidecar_name);
                    diagnostics.paths_checked.push(format!("{} (exists: {})", resources_path.display(), resources_path.exists()));

                    if resources_path.exists() {
                        diagnostics.found_path = Some(resources_path.display().to_string());
                        return (Some(resources_path), diagnostics);
                    }
                }
            }
        }
    }

    (None, diagnostics)
}

// Get path to the bundled backend executable (Tauri sidecar)
// In dev mode, always use Python directly for faster iteration
fn get_bundled_backend_path() -> Option<PathBuf> {
    let (path, _) = get_bundled_backend_path_with_diagnostics();
    path
}

// Get the path to the Python project directory
fn get_python_project_path() -> PathBuf {
    // First check if we're running as a bundled distribution
    if is_bundled_distribution() {
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                // Return the exe directory for bundled distribution
                return exe_dir.to_path_buf();
            }
        }
    }

    // Get current directory and navigate to project root
    let mut path = std::env::current_dir().unwrap();

    // Navigate up from release build directory if needed
    // Path could be: qubes-gui/src-tauri/target/release (when running exe)
    //            or: qubes-gui/src-tauri (when running dev)
    //            or: Qubes (when running from project root)

    // Keep going up until we find gui_bridge.py or reach the Qubes folder
    loop {
        // Check if gui_bridge.py exists in current directory
        let bridge_path = path.join("gui_bridge.py");
        if bridge_path.exists() {
            return path;
        }

        // Check folder name to stop at appropriate level
        let folder_name = path.file_name().and_then(|n| n.to_str());

        // If we're at "Qubes" folder, this is it
        if folder_name == Some("Qubes") {
            return path;
        }

        // If we're in release/debug, keep going up
        if folder_name == Some("release") || folder_name == Some("debug") {
            path.pop(); // Go up from release/debug
            continue;
        }

        // If we're in target, go up
        if folder_name == Some("target") {
            path.pop(); // Go up from target
            continue;
        }

        // If we're in src-tauri, go up
        if folder_name == Some("src-tauri") {
            path.pop(); // Go up from src-tauri
            continue;
        }

        // If we're in qubes-gui, go up to Qubes
        if folder_name == Some("qubes-gui") {
            path.pop(); // Go up from qubes-gui to Qubes
            return path;
        }

        // If we can't go up anymore, return current path
        if !path.pop() {
            return std::env::current_dir().unwrap();
        }
    }
}

fn get_python_bridge_path() -> PathBuf {
    // If bundled, return the backend exe path
    if let Some(bundled_path) = get_bundled_backend_path() {
        return bundled_path;
    }
    // Otherwise, return the gui_bridge.py path for development
    let mut path = get_python_project_path();
    path.push("gui_bridge.py");
    path
}

// Create a command to run the backend (bundled exe or Python script)
// Returns (Command, bool) where bool indicates if we should skip adding the bridge path
fn create_backend_command() -> (Command, bool) {
    // Check if we're running bundled distribution
    if let Some(bundled_path) = get_bundled_backend_path() {
        #[allow(unused_mut)] // mut needed on Windows for creation_flags()
        let mut cmd = Command::new(&bundled_path);
        // Hide console window on Windows
        #[cfg(target_os = "windows")]
        {
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        return (cmd, true); // true = skip bridge path argument
    }

    // Development mode - use Python
    let python = find_python_path().unwrap_or_else(|_| "python".to_string());
    #[allow(unused_mut)] // mut needed on Windows for creation_flags()
    let mut cmd = Command::new(&python);

    // Hide console window on Windows
    #[cfg(target_os = "windows")]
    {
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    // If using system python (not venv python), set PYTHONPATH to include
    // the venv's site-packages. This handles the common case on NTFS mounts
    // where venv/bin/python3 is a broken Windows symlink but the packages
    // in venv/lib/ are still accessible.
    #[cfg(not(target_os = "windows"))]
    {
        let project_root = get_python_project_path();
        let venv_python = project_root.join("venv").join("bin").join("python3");
        if !venv_python.exists() {
            // System python — inject venv site-packages via PYTHONPATH
            let site_packages = project_root.join("venv").join("lib");
            if site_packages.exists() {
                // Find python3.X directory under lib/
                if let Ok(entries) = std::fs::read_dir(&site_packages) {
                    for entry in entries.flatten() {
                        let sp = entry.path().join("site-packages");
                        if sp.exists() {
                            // Only include site-packages — project root is already
                            // in sys.path via Python's script-directory auto-add
                            eprintln!("[BACKEND] Using system python with PYTHONPATH={}", sp.display());
                            cmd.env("PYTHONPATH", sp.display().to_string());
                            break;
                        }
                    }
                }
            }
        }
    }

    (cmd, false) // false = need to add bridge path argument
}

/// Prepares a backend command ready for adding command name and arguments.
/// Handles both bundled distribution and development mode automatically.
/// Returns the configured Command with current_dir and bridge_path (if needed) already set.
fn prepare_backend_command() -> Result<Command, String> {
    let (mut cmd, is_bundled) = create_backend_command();
    let project_root = get_python_project_path();

    cmd.current_dir(&project_root);

    // Set HF_HOME for bundled HuggingFace models (heavy bundle)
    if is_bundled {
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(exe_dir) = exe_path.parent() {
                let hf_models = exe_dir.join("models").join("huggingface");
                if hf_models.exists() {
                    cmd.env("HF_HOME", &hf_models);
                }
            }
        }
    }

    // In development mode, add the bridge script path
    if !is_bundled {
        let bridge_path = get_python_bridge_path();
        if !bridge_path.exists() {
            return Err(format!("Python bridge not found at: {}", bridge_path.display()));
        }
        cmd.arg(&bridge_path);
    }

    Ok(cmd)
}

/// Execute a backend command with secrets passed via stdin instead of command line arguments.
/// This prevents secrets from appearing in process listings (ps, tasklist, etc.).
///
/// # Arguments
/// * `cmd` - The prepared Command (from prepare_backend_command)
/// * `secrets` - HashMap of secret names to values (e.g., {"password": "...", "api_key": "..."})
///
/// # Returns
/// * Ok((stdout, stderr)) on success
/// * Err(error_message) on failure
fn execute_with_secrets(
    mut cmd: Command,
    secrets: HashMap<&str, &str>,
) -> Result<(String, String), String> {
    // Configure stdin as piped so we can write secrets
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Spawn the process with detailed error logging
    let mut child = cmd.spawn().map_err(|e| {
        // Get diagnostic info about the backend path
        let (backend_path, diagnostics) = get_bundled_backend_path_with_diagnostics();

        let backend_info = match backend_path {
            Some(p) => format!("Backend path: {}", p.display()),
            None => "Backend path: NOT FOUND (will use Python fallback)".to_string(),
        };

        // Log detailed diagnostics
        eprintln!("[BACKEND SPAWN ERROR] Failed to spawn process: {}", e);
        eprintln!("[BACKEND SPAWN ERROR] Error kind: {:?}", e.kind());
        eprintln!("[BACKEND SPAWN ERROR] {}", backend_info);
        eprintln!("[BACKEND SPAWN ERROR] Exe path: {}", diagnostics.exe_path);
        eprintln!("[BACKEND SPAWN ERROR] Exe dir: {}", diagnostics.exe_dir);
        eprintln!("[BACKEND SPAWN ERROR] Sidecar name: {}", diagnostics.sidecar_name);
        eprintln!("[BACKEND SPAWN ERROR] Paths checked: {:?}", diagnostics.paths_checked);
        eprintln!("[BACKEND SPAWN ERROR] Found path: {:?}", diagnostics.found_path);
        eprintln!("[BACKEND SPAWN ERROR] Is dev mode: {}", diagnostics.is_dev_mode);

        #[cfg(not(target_os = "windows"))]
        eprintln!("[BACKEND SPAWN ERROR] Is executable: {:?}", diagnostics.is_executable);

        // Return a more helpful error message
        format!(
            "Backend failed to start: {} ({}). Backend path: {:?}. Check if the backend executable exists and is runnable.",
            e,
            e.kind(),
            diagnostics.found_path.unwrap_or_else(|| "not found".to_string())
        )
    })?;

    // Write secrets as JSON to stdin
    if let Some(mut stdin) = child.stdin.take() {
        let secrets_json = serde_json::to_string(&secrets)
            .map_err(|e| format!("Failed to serialize secrets: {}", e))?;

        // Write JSON followed by newline
        stdin
            .write_all(secrets_json.as_bytes())
            .map_err(|e| format!("Failed to write secrets to stdin: {}", e))?;
        stdin
            .write_all(b"\n")
            .map_err(|e| format!("Failed to write newline to stdin: {}", e))?;

        // stdin is dropped here, closing the pipe
    }

    // Wait for the process to complete and get output
    let output = child
        .wait_with_output()
        .map_err(|e| format!("Failed to wait for Python process: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(sanitize_backend_error(&stderr, "Backend"));
    }

    Ok((stdout, stderr))
}

/// Execute a command with secrets passed via stdin, with a timeout.
/// Returns an error if the command doesn't complete within the timeout.
///
/// IMPORTANT: Uses background threads to drain stdout/stderr while polling,
/// preventing pipe buffer deadlocks (OS pipe buffers are ~64KB; if the child
/// writes more than that to stdout or stderr, it blocks waiting for the parent
/// to read, but the parent is waiting for the child to exit — classic deadlock).
#[allow(dead_code)]
fn execute_with_secrets_timeout(
    mut cmd: Command,
    secrets: HashMap<&str, &str>,
    timeout_secs: u64,
) -> Result<(String, String), String> {
    use std::time::{Duration, Instant};
    use std::thread;
    use std::io::Read;
    use std::sync::{Arc, Mutex};

    // Configure stdin as piped so we can write secrets
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Spawn the process
    let mut child = cmd.spawn().map_err(|e| {
        format!("Failed to spawn backend process: {}", e)
    })?;

    // Write secrets as JSON to stdin
    if let Some(mut stdin) = child.stdin.take() {
        let secrets_json = serde_json::to_string(&secrets)
            .map_err(|e| format!("Failed to serialize secrets: {}", e))?;
        stdin
            .write_all(secrets_json.as_bytes())
            .map_err(|e| format!("Failed to write secrets to stdin: {}", e))?;
        stdin
            .write_all(b"\n")
            .map_err(|e| format!("Failed to write newline to stdin: {}", e))?;
        // stdin is dropped here, closing the pipe
    }

    // Drain stdout and stderr in background threads to prevent pipe buffer deadlock.
    let stdout_bytes = Arc::new(Mutex::new(Vec::new()));
    let stderr_bytes = Arc::new(Mutex::new(Vec::new()));

    let stdout_handle = if let Some(mut stdout) = child.stdout.take() {
        let buf = Arc::clone(&stdout_bytes);
        Some(thread::spawn(move || {
            let mut data = Vec::new();
            let _ = stdout.read_to_end(&mut data);
            *buf.lock().unwrap() = data;
        }))
    } else {
        None
    };

    let stderr_handle = if let Some(mut stderr) = child.stderr.take() {
        let buf = Arc::clone(&stderr_bytes);
        Some(thread::spawn(move || {
            let mut data = Vec::new();
            let _ = stderr.read_to_end(&mut data);
            *buf.lock().unwrap() = data;
        }))
    } else {
        None
    };

    // Poll for completion with timeout
    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);

    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                // Process finished — join reader threads
                if let Some(h) = stdout_handle { let _ = h.join(); }
                if let Some(h) = stderr_handle { let _ = h.join(); }

                let stdout = String::from_utf8_lossy(&stdout_bytes.lock().unwrap()).to_string();
                let stderr = String::from_utf8_lossy(&stderr_bytes.lock().unwrap()).to_string();

                if !status.success() {
                    return Err(sanitize_backend_error(&stderr, "Backend"));
                }

                return Ok((stdout, stderr));
            }
            Ok(None) => {
                // Still running, check timeout
                if start.elapsed() > timeout {
                    // Kill the process
                    let _ = child.kill();
                    // Join reader threads (they'll finish once the killed process closes pipes)
                    if let Some(h) = stdout_handle { let _ = h.join(); }
                    if let Some(h) = stderr_handle { let _ = h.join(); }
                    return Err(format!(
                        "Operation timed out after {} seconds. The backend process was terminated.",
                        timeout_secs
                    ));
                }
                // Sleep a bit before checking again
                thread::sleep(Duration::from_millis(100));
            }
            Err(e) => {
                return Err(format!("Error waiting for process: {}", e));
            }
        }
    }
}

/// Execute a command with secrets, streaming stderr for tool events.
/// Emits Tauri events for lines matching __TOOL_EVENT__ prefix.
fn execute_with_secrets_streaming(
    mut cmd: Command,
    secrets: HashMap<&str, &str>,
    app_handle: &AppHandle,
) -> Result<(String, String), String> {
    use std::io::{BufRead, BufReader, Read};
    use std::thread;
    use std::sync::mpsc;

    // Configure stdin/stdout/stderr as piped
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Spawn the process
    let mut child = cmd.spawn().map_err(|e| {
        format!("Failed to spawn backend process: {}", e)
    })?;

    // Write secrets as JSON to stdin
    if let Some(mut stdin) = child.stdin.take() {
        let secrets_json = serde_json::to_string(&secrets)
            .map_err(|e| format!("Failed to serialize secrets: {}", e))?;
        stdin
            .write_all(secrets_json.as_bytes())
            .map_err(|e| format!("Failed to write secrets to stdin: {}", e))?;
        stdin
            .write_all(b"\n")
            .map_err(|e| format!("Failed to write newline to stdin: {}", e))?;
        // stdin is dropped here, closing the pipe
    }

    // Take stdout and stderr to read in separate threads
    let stdout_pipe = child.stdout.take();
    let stderr_pipe = child.stderr.take();

    // Read stdout in a thread
    let (stdout_tx, stdout_rx) = mpsc::channel::<String>();
    let stdout_thread = thread::spawn(move || {
        let mut stdout_content = String::new();
        if let Some(mut stdout) = stdout_pipe {
            let _ = stdout.read_to_string(&mut stdout_content);
        }
        stdout_tx.send(stdout_content).ok();
    });

    // Read stderr in a thread, emitting tool events as they arrive
    let app_handle_clone = app_handle.clone();
    let (stderr_tx, stderr_rx) = mpsc::channel::<String>();
    let stderr_thread = thread::spawn(move || {
        let mut stderr_lines = Vec::new();
        if let Some(stderr) = stderr_pipe {
            let reader = BufReader::new(stderr);
            for line in reader.lines() {
                if let Ok(line) = line {
                    // Check for tool event prefix
                    if line.starts_with("__TOOL_EVENT__") {
                        let json_str = &line[14..]; // Skip "__TOOL_EVENT__" prefix
                        if let Ok(event_data) = serde_json::from_str::<serde_json::Value>(json_str) {
                            // Emit Tauri event to frontend
                            let _ = app_handle_clone.emit("tool-call-event", &event_data);
                        }
                    } else {
                        // Regular stderr line
                        stderr_lines.push(line);
                    }
                }
            }
        }
        stderr_tx.send(stderr_lines.join("\n")).ok();
    });

    // Wait for the process to complete
    let status = child.wait().map_err(|e| {
        format!("Failed to wait for backend process: {}", e)
    })?;

    // Wait for reader threads to finish
    let _ = stdout_thread.join();
    let _ = stderr_thread.join();

    let stdout = stdout_rx.recv().unwrap_or_default();
    let stderr = stderr_rx.recv().unwrap_or_default();

    if !status.success() {
        return Err(sanitize_backend_error(&stderr, "Backend"));
    }

    Ok((stdout, stderr))
}

// =============================================================================
// EVENT WATCHER COMMANDS
// =============================================================================

#[tauri::command]
async fn start_event_watcher_cmd(
    app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    password: String
) -> Result<serde_json::Value, String> {
    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    // The sidecar starts a long-running subscription and returns immediately.
    // Stream events arrive as JSONL lines and the reader thread emits them
    // as "chain-state-event" Tauri events.
    let result = sidecar_execute_with_retry("watch-events", args, secrets, Some(&app_handle), None).await?;

    Ok(result)
}

#[tauri::command]
async fn stop_event_watcher_cmd(
    app_handle: AppHandle,
    user_id: String,
    qube_id: String
) -> Result<serde_json::Value, String> {
    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];
    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("stop-watch-events", args, secrets, Some(&app_handle), None).await?;

    Ok(result)
}

// =============================================================================
// AUTHENTICATION
// =============================================================================

#[tauri::command]
async fn authenticate(app_handle: AppHandle, username: String, password: String) -> Result<AuthResponse, String> {
    // If running as AppImage without a real backend, block authentication
    if std::env::var("APPIMAGE").is_ok() && !is_bundled_distribution() {
        return Err("APPIMAGE_NO_BACKEND".to_string());
    }

    // Validate inputs
    validate_identifier(&username, "username")?;

    let args = vec![username];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("authenticate", args, secrets, Some(&app_handle), None).await?;

    let auth_response: AuthResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse authenticate response: {}", e))?;

    // Fire-and-forget warm-up to pre-import heavy modules in the sidecar.
    // Uses sidecar_execute (NOT with_retry) so failure doesn't trigger sidecar restart.
    if auth_response.success {
        tokio::spawn(async move {
            let _ = sidecar_execute(
                "warm-up", &vec![], &HashMap::new(), None, None,
            ).await;
        });
    }

    Ok(auth_response)
}

#[tauri::command]
async fn get_available_models(app_handle: AppHandle, ) -> Result<AvailableModelsResponse, String> {

    // No authentication required - this is public model metadata
    let args: Vec<String> = vec![];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-available-models", args, secrets, Some(&app_handle), None).await?;

    let response: AvailableModelsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-available-models response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn list_qubes(app_handle: AppHandle, user_id: String, password: String) -> Result<Vec<Qube>, String> {
    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("list-qubes", args, secrets, Some(&app_handle), None).await?;

    let qubes: Vec<Qube> = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse list-qubes response: {}", e))?;

    Ok(qubes)
}

#[tauri::command]
async fn save_cropped_avatar(base64_data: String) -> Result<String, String> {
    use std::io::Write;
    use base64::{Engine as _, engine::general_purpose};
    let bytes = general_purpose::STANDARD
        .decode(&base64_data)
        .map_err(|e| format!("Invalid base64: {}", e))?;
    let temp_dir = std::env::temp_dir().join("qubes_avatars");
    std::fs::create_dir_all(&temp_dir)
        .map_err(|e| format!("Failed to create temp dir: {}", e))?;
    let filename = format!("cropped_{}.png", std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis());
    let path = temp_dir.join(&filename);
    let mut file = std::fs::File::create(&path)
        .map_err(|e| format!("Failed to create temp file: {}", e))?;
    file.write_all(&bytes)
        .map_err(|e| format!("Failed to write temp file: {}", e))?;
    Ok(path.to_string_lossy().to_string())
}

#[tauri::command]
async fn create_qube(app_handle: AppHandle,
    user_id: String,
    name: String,
    genesis_prompt: String,
    ai_provider: String,
    ai_model: String,
    voice_model: String,
    owner_pubkey: String,  // NFT address derived from this by backend
    password: String,
    encrypt_genesis: bool,
    favorite_color: String,
    avatar_file: Option<String>,
    generate_avatar: bool,
    avatar_style: Option<String>,
) -> Result<Qube, String> {

    // Rate limit check
    check_rate_limit("create_qube")?;

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let mut args = vec![user_id, "--name".to_string(), name, "--genesis-prompt".to_string(), genesis_prompt, "--ai-provider".to_string(), ai_provider, "--ai-model".to_string(), ai_model, "--voice-model".to_string(), voice_model, "--owner-pubkey".to_string(), owner_pubkey, "--encrypt-genesis".to_string(), (if encrypt_genesis { "true" } else { "false" }).to_string(), "--favorite-color".to_string(), favorite_color];

    if let Some(avatar_path) = avatar_file {
        args.push("--avatar-file".to_string());
        args.push(avatar_path);
    } else if generate_avatar {
        args.push("--generate-avatar".to_string());
        if let Some(style) = avatar_style {
            args.push("--avatar-style".to_string());
            args.push(style);
        }
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("create-qube", args, secrets, Some(&app_handle), None).await?;

    let qube: Qube = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse create-qube response: {}", e))?;

    Ok(qube)

}

#[tauri::command]
async fn prepare_qube_mint(app_handle: AppHandle,
    user_id: String,
    name: String,
    genesis_prompt: String,
    ai_provider: String,
    ai_model: String,
    voice_model: String,
    owner_pubkey: String,
    user_address: String,
    password: String,
    encrypt_genesis: bool,
    favorite_color: String,
    avatar_file: Option<String>,
    generate_avatar: bool,
    avatar_style: Option<String>,
) -> Result<PrepareQubeMintResponse, String> {

    check_rate_limit("prepare_qube_mint")?;
    validate_identifier(&user_id, "user_id")?;

    let mut args = vec![
        user_id,
        "--name".to_string(), name,
        "--genesis-prompt".to_string(), genesis_prompt,
        "--ai-provider".to_string(), ai_provider,
        "--ai-model".to_string(), ai_model,
        "--voice-model".to_string(), voice_model,
        "--owner-pubkey".to_string(), owner_pubkey,
        "--user-address".to_string(), user_address,
        "--encrypt-genesis".to_string(), (if encrypt_genesis { "true" } else { "false" }).to_string(),
        "--favorite-color".to_string(), favorite_color,
    ];

    if let Some(avatar_path) = avatar_file {
        args.push("--avatar-file".to_string());
        args.push(avatar_path);
    } else if generate_avatar {
        args.push("--generate-avatar".to_string());
        if let Some(style) = avatar_style {
            args.push("--avatar-style".to_string());
            args.push(style);
        }
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("prepare-qube-mint", args, secrets, Some(&app_handle), None).await?;

    let response: PrepareQubeMintResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse prepare-qube-mint response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn finalize_qube_mint(app_handle: AppHandle,
    user_id: String,
    pending_id: String,
    mint_txid: String,
    password: String,
) -> Result<Qube, String> {

    check_rate_limit("finalize_qube_mint")?;
    validate_identifier(&user_id, "user_id")?;

    let args = vec![
        user_id,
        "--pending-id".to_string(), pending_id,
        "--mint-txid".to_string(), mint_txid,
    ];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    // 180s timeout: finalize includes BCMR generation, IPFS upload, and server sync
    let result = sidecar_execute_with_retry("finalize-qube-mint", args, secrets, Some(&app_handle), Some(180)).await?;

    let qube: Qube = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse finalize-qube-mint response: {}", e))?;

    Ok(qube)
}

#[tauri::command]
async fn list_pending_registrations(app_handle: AppHandle, 
    user_id: String,
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("list-pending-registrations", args, secrets, Some(&app_handle), None).await?;

    let result: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse list-pending-registrations response: {}", e))?;

    Ok(result)

}

#[tauri::command]
async fn send_message(app_handle: AppHandle, user_id: String, qube_id: String, message: String, password: String) -> Result<ChatResponse, String> {
    // Rate limit check
    check_rate_limit("send_message")?;

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    // In sidecar mode, message goes via JSON — no CLI length limit
    let args = vec![user_id, qube_id, message];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("send-message", args, secrets, Some(&app_handle), Some(300)).await?;

    let chat_response: ChatResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse send-message response: {}", e))?;

    Ok(chat_response)
}

#[tauri::command]
async fn generate_speech(app_handle: AppHandle, user_id: String, qube_id: String, text: String, password: String) -> Result<SpeechResponse, String> {
    // Rate limit check
    check_rate_limit("generate_speech")?;

    let args = vec![user_id, qube_id, text];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    // 120s timeout: cloud TTS is fast, local TTS (Kokoro/Qwen3) may need model loading time
    let result = sidecar_execute_with_retry("generate-speech", args, secrets, Some(&app_handle), Some(120)).await?;

    let speech_response: SpeechResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse generate-speech response: {}", e))?;

    Ok(speech_response)
}

// ========== Voice Settings Commands ==========

#[tauri::command]
async fn get_voice_settings(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-voice-settings", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-voice-settings response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_voice_settings(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    voice_library_ref: Option<String>,
    tts_enabled: Option<bool>
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, qube_id];

    if let Some(ref voice_ref) = voice_library_ref {
        args.push("--voice-library-ref".to_string());
        args.push(voice_ref.clone());
    }

    if let Some(enabled) = tts_enabled {
        args.push("--tts-enabled".to_string());
        args.push(enabled.to_string());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("update-voice-settings", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-voice-settings response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn preview_voice(app_handle: AppHandle, 
    user_id: String,
    text: String,
    voice_type: String,
    language: Option<String>,
    design_prompt: Option<String>,
    clone_audio_path: Option<String>,
    clone_audio_text: Option<String>,
    preset_voice: Option<String>
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, text, voice_type];

    if let Some(lang) = language {
        args.push("--language".to_string());
        args.push(lang);
    }

    if let Some(prompt) = design_prompt {
        args.push("--design-prompt".to_string());
        args.push(prompt);
    }

    if let Some(path) = clone_audio_path {
        args.push("--clone-audio-path".to_string());
        args.push(path);
    }

    if let Some(audio_text) = clone_audio_text {
        args.push("--clone-audio-text".to_string());
        args.push(audio_text);
    }

    if let Some(preset) = preset_voice {
        args.push("--preset-voice".to_string());
        args.push(preset);
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("preview-voice", args, secrets, Some(&app_handle), Some(180)).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse preview-voice response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn add_voice_to_library(app_handle: AppHandle, 
    user_id: String,
    name: String,
    voice_type: String,
    language: Option<String>,
    design_prompt: Option<String>,
    clone_audio_path: Option<String>,
    clone_audio_text: Option<String>
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, name, voice_type];

    if let Some(lang) = language {
        args.push("--language".to_string());
        args.push(lang);
    }

    if let Some(prompt) = design_prompt {
        args.push("--design-prompt".to_string());
        args.push(prompt);
    }

    if let Some(path) = clone_audio_path {
        args.push("--clone-audio-path".to_string());
        args.push(path);
    }

    if let Some(audio_text) = clone_audio_text {
        args.push("--clone-audio-text".to_string());
        args.push(audio_text);
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("add-voice-to-library", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse add-voice-to-library response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_voice_from_library(app_handle: AppHandle, user_id: String, voice_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, voice_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("delete-voice-from-library", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-voice-from-library response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_voice_library(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-voice-library", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-voice-library response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn check_qwen3_status(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("check-qwen3-status", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-qwen3-status response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn check_kokoro_status(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("check-kokoro-status", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-kokoro-status response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_tts_progress(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-tts-progress", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-tts-progress response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn download_qwen3_model(app_handle: AppHandle, user_id: String, model_name: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, model_name];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("download-qwen3-model", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse download-qwen3-model response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_qwen3_download_progress(app_handle: AppHandle, user_id: String, download_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, download_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-qwen3-download-progress", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-qwen3-download-progress response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn cancel_qwen3_download(app_handle: AppHandle, user_id: String, download_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, download_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("cancel-qwen3-download", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse cancel-qwen3-download response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_qwen3_model(app_handle: AppHandle, user_id: String, model_name: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, model_name];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("delete-qwen3-model", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-qwen3-model response: {}", e))?;

    Ok(response)

}

// ========== GPU Acceleration Commands ==========

#[tauri::command]
async fn check_gpu_acceleration(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("check-gpu-acceleration", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-gpu-acceleration response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn install_gpu_acceleration(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("install-gpu-acceleration", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse install-gpu-acceleration response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_gpu_install_progress(app_handle: AppHandle, user_id: String, install_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, install_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-gpu-install-progress", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-gpu-install-progress response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn uninstall_gpu_acceleration(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("uninstall-gpu-acceleration", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse uninstall-gpu-acceleration response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_qwen3_preferences(app_handle: AppHandle, 
    user_id: String,
    model_variant: Option<String>,
    use_flash_attention: Option<bool>
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id];

    if let Some(variant) = model_variant {
        args.push("--model-variant".to_string());
        args.push(variant);
    }

    if let Some(flash) = use_flash_attention {
        args.push("--use-flash-attention".to_string());
        args.push(flash.to_string());
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-qwen3-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-qwen3-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn record_voice_clone_audio(app_handle: AppHandle, user_id: String, duration_seconds: Option<i32>) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id];

    if let Some(duration) = duration_seconds {
        args.push("--duration-seconds".to_string());
        args.push(duration.to_string());
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("record-voice-clone-audio", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse record-voice-clone-audio response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn save_recorded_audio(user_id: String, audio_data: Vec<u8>) -> Result<serde_json::Value, String> {
    use std::io::Write;

    // Save the audio data to a temp file and convert to WAV via backend
    let mut cmd = prepare_backend_command()?;
    cmd.arg("save-recorded-audio")
        .arg(&user_id)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to spawn backend: {}", e))?;

    // Write audio data to stdin and explicitly drop to signal EOF
    {
        let mut stdin = child.stdin.take()
            .ok_or_else(|| "Failed to open stdin".to_string())?;
        stdin.write_all(&audio_data)
            .map_err(|e| format!("Failed to write audio data: {}", e))?;
        // stdin is dropped here when it goes out of scope, signaling EOF
    }

    let output = child.wait_with_output()
        .map_err(|e| format!("Failed to execute backend: {}", e))?;

    if !output.status.success() {
        let error = String::from_utf8_lossy(&output.stderr);
        return Err(sanitize_backend_error(&error, "Save recorded audio"));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let response: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse JSON response: {}. Output: {}", e, stdout))?;

    Ok(response)
}

#[tauri::command]
async fn transcribe_audio(app_handle: AppHandle, user_id: String, audio_path: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, audio_path];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("transcribe-audio", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse transcribe-audio response: {}", e))?;

    Ok(response)

}

// ========== End Voice Settings Commands ==========

// ========== WSL2 TTS Setup Commands ==========

#[tauri::command]
async fn check_wsl2_tts_status(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("check-wsl2-tts-status", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-wsl2-tts-status response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn setup_wsl2_tts(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    // This is a long-running operation, so use a longer timeout
    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("setup-wsl2-tts", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse setup-wsl2-tts response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_wsl2_tts_setup_progress(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-wsl2-tts-setup-progress", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-wsl2-tts-setup-progress response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn start_wsl2_tts_server(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("start-wsl2-tts-server", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse start-wsl2-tts-server response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn stop_wsl2_tts_server(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("stop-wsl2-tts-server", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse stop-wsl2-tts-server response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn uninstall_wsl2_tts(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("uninstall-wsl2-tts", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse uninstall-wsl2-tts response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn install_wsl2(app_handle: AppHandle, ) -> Result<serde_json::Value, String> {

    // One-click WSL2 installation (requires admin, shows UAC prompt)
    let args: Vec<String> = vec![];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("install-wsl2", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse install-wsl2 response: {}", e))?;

    Ok(response)

}

// ========== End WSL2 TTS Setup Commands ==========

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[tauri::command]
fn force_exit() {
    std::process::exit(0);
}

#[tauri::command]
async fn check_sessions(app_handle: AppHandle, user_id: String, qube_id: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("check-sessions", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-sessions response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn anchor_session(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<serde_json::Value, String> {
    // Rate limit check
    check_rate_limit("anchor_session")?;

    let args = vec![user_id, qube_id.clone()];
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("anchor-session", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse anchor-session response: {}", e))?;

    // Notify frontend so it can trigger IPFS sync
    if response.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
        let _ = app_handle.emit("anchor-complete", serde_json::json!({ "qube_id": qube_id }));
    }

    Ok(response)
}

#[tauri::command]
async fn discard_session(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("discard-session", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse discard-session response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_session_block(app_handle: AppHandle, user_id: String, qube_id: String, block_number: i32, password: String, timestamp: Option<i64>) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, qube_id, block_number.to_string()];

    if let Some(ts) = timestamp {
        args.push(ts.to_string());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("delete-session-block", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-session-block response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn discard_last_block(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<serde_json::Value, String> {

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("discard-last-block", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse discard-last-block response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_audio_base64(file_path: String) -> Result<String, String> {
    use std::fs;
    use std::path::Path;
    use base64::{Engine as _, engine::general_purpose};

    // Convert to absolute path if relative
    let path = Path::new(&file_path);
    let absolute_path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        // Get project root (go up from src-tauri)
        let project_root = get_python_project_path();
        project_root.join(&file_path)
    };

    // Check if file exists
    if !absolute_path.exists() {
        return Err(format!("Audio file does not exist: {:?}", absolute_path));
    }

    // Read the audio file
    let audio_data = fs::read(&absolute_path)
        .map_err(|e| format!("Failed to read audio file {:?}: {}", absolute_path, e))?;

    // Validate file has content
    if audio_data.is_empty() {
        return Err(format!("Audio file is empty: {:?}", absolute_path));
    }

    // Convert to base64
    let base64_data = general_purpose::STANDARD.encode(&audio_data);

    // Determine MIME type based on file extension
    let mime_type = if file_path.ends_with(".wav") {
        "audio/wav"
    } else if file_path.ends_with(".mp3") {
        "audio/mpeg"
    } else {
        "audio/mpeg"  // Default fallback
    };

    // Return as data URL with correct MIME type
    Ok(format!("data:{};base64,{}", mime_type, base64_data))
}

/// PID of the currently playing native audio process (for stop functionality)
static NATIVE_AUDIO_PID: std::sync::Mutex<Option<u32>> = std::sync::Mutex::new(None);

/// Play audio file natively using system audio player.
/// On Linux, uses pw-play (PipeWire) or aplay as fallback.
/// On Windows, uses MCI (Media Control Interface) via PowerShell.
/// Returns duration in seconds parsed from audio file header.
/// Emits "audio-playback-ended" event when playback finishes.
#[tauri::command]
async fn play_audio_native(
    app_handle: tauri::AppHandle,
    file_path: String,
) -> Result<serde_json::Value, String> {
    use std::path::Path;

    let path = Path::new(&file_path);
    let absolute_path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        get_python_project_path().join(&file_path)
    };

    if !absolute_path.exists() {
        return Err(format!("Audio file does not exist: {:?}", absolute_path));
    }

    // Parse audio duration from file header (WAV or MP3)
    let duration_secs = {
        let data = std::fs::read(&absolute_path)
            .map_err(|e| format!("Failed to read audio file: {}", e))?;

        if data.len() >= 44 && &data[0..4] == b"RIFF" {
            // WAV: parse from header
            let channels = u16::from_le_bytes([data[22], data[23]]) as f64;
            let sample_rate = u32::from_le_bytes([data[24], data[25], data[26], data[27]]) as f64;
            let bits_per_sample = u16::from_le_bytes([data[34], data[35]]) as f64;
            let data_size = u32::from_le_bytes([data[40], data[41], data[42], data[43]]) as f64;
            if sample_rate > 0.0 && channels > 0.0 && bits_per_sample > 0.0 {
                data_size / (sample_rate * channels * bits_per_sample / 8.0)
            } else {
                0.0
            }
        } else if data.len() >= 4 && (data[0] == 0xFF && (data[1] & 0xE0) == 0xE0
                                       || &data[0..3] == b"ID3") {
            // MP3: parse MPEG frame header for bitrate, then calculate duration
            let file_size = data.len() as f64;

            // Skip ID3v2 tag if present
            let mut offset: usize = 0;
            if data.len() >= 10 && &data[0..3] == b"ID3" {
                let id3_size = ((data[6] as usize & 0x7F) << 21)
                    | ((data[7] as usize & 0x7F) << 14)
                    | ((data[8] as usize & 0x7F) << 7)
                    | (data[9] as usize & 0x7F);
                offset = 10 + id3_size;
            }

            // Find first MPEG sync frame and parse bitrate
            let mut bitrate_kbps: u32 = 0;
            while offset + 4 <= data.len() {
                if data[offset] == 0xFF && (data[offset + 1] & 0xE0) == 0xE0 {
                    let b1 = data[offset + 1];
                    let b2 = data[offset + 2];
                    let version = (b1 >> 3) & 3;   // 0=2.5, 2=2, 3=1
                    let layer = (b1 >> 1) & 3;     // 1=III, 2=II, 3=I
                    let br_idx = (b2 >> 4) as usize;

                    // Bitrate lookup tables (kbps)
                    const MPEG1_L3: [u32; 16] = [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320,0];
                    const MPEG2_L3: [u32; 16] = [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160,0];

                    bitrate_kbps = match (version, layer) {
                        (3, 1) => MPEG1_L3[br_idx],        // MPEG1 Layer III
                        (2, 1) | (0, 1) => MPEG2_L3[br_idx], // MPEG2/2.5 Layer III
                        _ => 0,
                    };
                    break;
                }
                offset += 1;
            }

            if bitrate_kbps > 0 {
                file_size * 8.0 / (bitrate_kbps as f64 * 1000.0)
            } else {
                // Fallback: assume 128kbps
                file_size * 8.0 / 128000.0
            }
        } else {
            // Unknown format - let the player try anyway, estimate 30s
            eprintln!("[play_audio_native] Unknown audio format, estimating 30s duration");
            30.0
        }
    };

    eprintln!("[play_audio_native] Playing {:?}, duration: {:.1}s", absolute_path, duration_secs);

    let path_str = absolute_path.to_string_lossy().to_string();

    // Find the best available audio player (platform-specific)
    let (player_cmd, player_args, append_path): (String, Vec<String>, bool) = if cfg!(target_os = "windows") {
        // Use ffplay (ffmpeg) for audio playback - no GUI, supports WAV/MP3
        ("ffplay".to_string(), vec!["-nodisp".to_string(), "-autoexit".to_string(), "-loglevel".to_string(), "quiet".to_string()], true)
    } else if cfg!(target_os = "linux") {
        // Try pw-play (PipeWire) first, then ffplay (universal), then aplay (ALSA, WAV only)
        let is_mp3 = path_str.ends_with(".mp3");
        if std::process::Command::new("pw-play").arg("--help").output().is_ok() {
            ("pw-play".to_string(), vec![], true)
        } else if is_mp3 || std::process::Command::new("ffplay").arg("-version").output().is_ok() {
            // aplay can't play MP3, so prefer ffplay for MP3 files or as general fallback
            ("ffplay".to_string(), vec!["-nodisp".to_string(), "-autoexit".to_string(), "-loglevel".to_string(), "quiet".to_string()], true)
        } else {
            ("aplay".to_string(), vec![], true)
        }
    } else if cfg!(target_os = "macos") {
        ("afplay".to_string(), vec![], true)
    } else {
        return Err("Native audio playback not supported on this platform".to_string());
    };

    let player_name = player_cmd.clone();

    // Spawn playback in background thread
    std::thread::spawn(move || {
        let mut cmd = std::process::Command::new(&player_cmd);
        cmd.args(&player_args);
        if append_path {
            cmd.arg(&path_str);
        }
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }

        match cmd.spawn() {
            Ok(mut child) => {
                // Store PID for stop functionality
                if let Ok(mut lock) = NATIVE_AUDIO_PID.lock() {
                    *lock = Some(child.id());
                }

                match child.wait() {
                    Ok(status) => {
                        if !status.success() {
                            eprintln!("[play_audio_native] Player exited with error status: {}", status);
                        }
                    }
                    Err(e) => {
                        eprintln!("[play_audio_native] Failed to wait for player: {}", e);
                    }
                }

                // Clear PID
                if let Ok(mut lock) = NATIVE_AUDIO_PID.lock() {
                    *lock = None;
                }
            }
            Err(e) => {
                eprintln!("[play_audio_native] Failed to spawn player: {}", e);
            }
        }

        // Emit event when playback ends
        let _ = app_handle.emit("audio-playback-ended", ());
    });

    Ok(serde_json::json!({
        "duration": duration_secs,
        "player": player_name,
    }))
}

/// Stop any native audio playback
#[tauri::command]
async fn stop_audio_native() -> Result<(), String> {
    // Try to kill the tracked audio process by PID
    if let Ok(mut lock) = NATIVE_AUDIO_PID.lock() {
        if let Some(pid) = lock.take() {
            if cfg!(target_os = "windows") {
                let _ = std::process::Command::new("taskkill")
                    .args(&["/F", "/PID", &pid.to_string(), "/T"])
                    .output();
            } else {
                // Unix: kill process by PID
                let _ = std::process::Command::new("kill")
                    .arg(pid.to_string())
                    .output();
            }
        }
    }
    Ok(())
}

#[tauri::command]
async fn get_qube_blocks(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    limit: Option<i32>,
    offset: Option<i32>
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    
    // Use defaults if not provided (100 blocks, offset 0)
    let limit_val = limit.unwrap_or(100);
    let offset_val = offset.unwrap_or(0);

    let args = vec![user_id, qube_id, limit_val.to_string(), offset_val.to_string()];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-qube-blocks", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-qube-blocks response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn recall_last_context(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("recall-last-context", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse recall-last-context response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_qube_config(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    ai_model: Option<String>,
    voice_model: Option<String>,
    favorite_color: Option<String>,
    tts_enabled: Option<bool>,
    evaluation_model: Option<String>,
    password: Option<String>,
) -> Result<serde_json::Value, String> {

    let args = vec![user_id, qube_id, ai_model.unwrap_or_default(), voice_model.unwrap_or_default(), favorite_color.unwrap_or_default(), (match tts_enabled {
        Some(true) => "true",
        Some(false) => "false",
        None => "",
    }).to_string(), evaluation_model.unwrap_or_default()];

    let mut secrets = HashMap::new();
    if let Some(ref pwd) = password {
        secrets.insert("password", pwd.as_str());
    }

    let result = sidecar_execute_with_retry("update-qube-config", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-qube-config response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_qube_avatar(app_handle: AppHandle, user_id: String, qube_id: String, avatar_path: String) -> Result<serde_json::Value, String> {
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;
    let args = vec![user_id, qube_id, avatar_path];
    let result = sidecar_execute_with_retry("update-qube-avatar", args, HashMap::new(), Some(&app_handle), None).await?;
    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-qube-avatar response: {}", e))?;
    Ok(response)
}

#[tauri::command]
async fn delete_qube(app_handle: AppHandle, user_id: String, qube_id: String, password: String, sweep_address: Option<String>) -> Result<DeleteResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id];
    if let Some(ref addr) = sweep_address {
        if !addr.is_empty() {
            args.push("--sweep-address".to_string());
            args.push(addr.clone());
        }
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("delete-qube", args, secrets, Some(&app_handle), None).await?;

    let delete_response: DeleteResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-qube response: {}", e))?;

    Ok(delete_response)

}

#[tauri::command]
async fn reset_qube(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<DeleteResponse, String> {

    // DEV ONLY: Reset qube to fresh state while preserving identity
    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("reset-qube", args, secrets, Some(&app_handle), Some(30)).await?;

    let reset_response: DeleteResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reset-qube response: {}", e))?;

    Ok(reset_response)

}

#[tauri::command]
async fn save_image(app_handle: AppHandle, user_id: String, qube_id: String, image_url: String) -> Result<SaveImageResponse, String> {

    let args = vec![user_id, qube_id, image_url];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("save-image", args, secrets, Some(&app_handle), None).await?;

    let save_response: SaveImageResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse save-image response: {}", e))?;

    Ok(save_response)

}

#[tauri::command]
async fn upload_avatar_to_ipfs(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<UploadAvatarToIpfsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("upload-avatar-to-ipfs", args, secrets, Some(&app_handle), None).await?;

    let response: UploadAvatarToIpfsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse upload-avatar-to-ipfs response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn analyze_image(app_handle: AppHandle, user_id: String, qube_id: String, image_base64: String, user_message: String, password: String) -> Result<AnalyzeImageResponse, String> {

    use std::fs;
    
    // Rate limit check
    check_rate_limit("analyze_image")?;

    // Write image data to a secure temporary file with unpredictable name
    let temp_file = secure_temp_path("image");

    fs::write(&temp_file, &image_base64)
        .map_err(|e| format!("Failed to write temp file: {}", e))?;

    let args = vec![user_id, qube_id, temp_file.to_str().unwrap().to_string(), user_message];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("analyze-image", args, secrets, Some(&app_handle), None).await?;

    // Always clean up temp file, even on error
    let _ = fs::remove_file(&temp_file);

    let analyze_response: AnalyzeImageResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse analyze-image response: {}", e))?;

    Ok(analyze_response)

}

#[tauri::command]
async fn start_multi_qube_conversation(
    app_handle: AppHandle,
    user_id: String,
    qube_ids_str: String,
    initial_prompt: String,
    password: String,
    conversation_mode: String,
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, qube_ids_str, initial_prompt, conversation_mode];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("start-multi-qube-conversation", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse start-multi-qube-conversation response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_next_speaker(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    let args = vec![user_id, conversation_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-next-speaker", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-next-speaker response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn continue_multi_qube_conversation(
    app_handle: AppHandle,
    user_id: String,
    conversation_id: String,
    password: String,
    skip_tools: Option<bool>,
    participant_ids: Option<String>,  // JSON array of participant qube IDs (optimization to skip scanning all qubes)
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, conversation_id];

    if let Some(skip) = skip_tools {
        args.push((if skip { "true" } else { "false" }).to_string());
    } else {
        args.push("false".to_string());
    }

    if let Some(ref ids) = participant_ids {
        args.push(ids.clone());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("continue-multi-qube-conversation", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse continue-multi-qube-conversation response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn run_background_turns(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    exclude_qube_ids: String,  // JSON array of qube IDs to exclude
    password: String,
) -> Result<serde_json::Value, String> {

    let args = vec![user_id, conversation_id, exclude_qube_ids];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("run-background-turns", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse run-background-turns response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn inject_multi_qube_user_message(
    app_handle: AppHandle,
    user_id: String,
    conversation_id: String,
    message: String,
    password: String,
) -> Result<serde_json::Value, String> {
    // In sidecar mode, args are sent via JSONL (no command-line length limit)
    let args = vec![user_id, conversation_id, message];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("inject-multi-qube-user-message", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse inject-multi-qube-user-message response: {}", e))
}

#[tauri::command]
async fn lock_in_multi_qube_response(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    timestamp: i64,
    password: String,
    participant_ids: Option<String>,
) -> Result<serde_json::Value, String> {

    let mut args = vec![user_id, conversation_id, timestamp.to_string()];

    if let Some(ids) = participant_ids {
        args.push("--participant-ids".to_string());
        args.push(ids);
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("lock-in-multi-qube-response", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse lock-in-multi-qube-response response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn end_multi_qube_conversation(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    anchor: bool,
    password: String,
) -> Result<serde_json::Value, String> {

    let args = vec![user_id, conversation_id, (if anchor { "true" } else { "false" }).to_string()];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("end-multi-qube-conversation", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse end-multi-qube-conversation response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_configured_api_keys(app_handle: AppHandle, user_id: String, password: String) -> Result<ConfiguredKeysResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-configured-api-keys", args, secrets, Some(&app_handle), None).await?;

    let response: ConfiguredKeysResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-configured-api-keys response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn save_api_key(app_handle: AppHandle, user_id: String, provider: String, api_key: String, password: String) -> Result<APIKeyResponse, String> {

    // Rate limit check
    check_rate_limit("save_api_key")?;
    
    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, provider];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("api_key", api_key.as_str());

    let result = sidecar_execute_with_retry("save-api-key", args, secrets, Some(&app_handle), None).await?;

    let response: APIKeyResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse save-api-key response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn validate_api_key(app_handle: AppHandle, user_id: String, provider: String, api_key: String, password: Option<String>) -> Result<ValidationResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, provider];

    let mut secrets = HashMap::new();
    secrets.insert("api_key", api_key.as_str());
    if let Some(ref pw) = password {
        secrets.insert("password", pw.as_str());
    }

    let result = sidecar_execute_with_retry("validate-api-key", args, secrets, Some(&app_handle), None).await?;

    let response: ValidationResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse validate-api-key response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_api_key(app_handle: AppHandle, user_id: String, provider: String, password: String) -> Result<APIKeyResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, provider];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("delete-api-key", args, secrets, Some(&app_handle), None).await?;

    let response: APIKeyResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-api-key response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_block_preferences(app_handle: AppHandle, user_id: String) -> Result<BlockPreferencesResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-block-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: BlockPreferencesResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-block-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_block_preferences(app_handle: AppHandle, 
    user_id: String,
    individual_auto_anchor: Option<bool>,
    individual_anchor_threshold: Option<i32>,
    group_auto_anchor: Option<bool>,
    group_anchor_threshold: Option<i32>,
    auto_sync_ipfs_on_anchor: Option<bool>,
    auto_sync_ipfs_periodic: Option<bool>,
    auto_sync_ipfs_interval: Option<i32>
) -> Result<BlockPreferencesResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let mut args = vec![user_id];

    if let Some(val) = individual_auto_anchor {
        args.push("--individual-auto-anchor".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = individual_anchor_threshold {
        args.push("--individual-anchor-threshold".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = group_auto_anchor {
        args.push("--group-auto-anchor".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = group_anchor_threshold {
        args.push("--group-anchor-threshold".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = auto_sync_ipfs_on_anchor {
        args.push("--auto-sync-ipfs-on-anchor".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = auto_sync_ipfs_periodic {
        args.push("--auto-sync-ipfs-periodic".to_string());
        args.push(val.to_string());
    }

    if let Some(val) = auto_sync_ipfs_interval {
        args.push("--auto-sync-ipfs-interval".to_string());
        args.push(val.to_string());
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-block-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: BlockPreferencesResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-block-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_relationship_difficulty(app_handle: AppHandle, user_id: String) -> Result<RelationshipSettingsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-relationship-difficulty", args, secrets, Some(&app_handle), None).await?;

    let response: RelationshipSettingsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-relationship-difficulty response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_relationship_difficulty(app_handle: AppHandle, 
    user_id: String,
    difficulty: String
) -> Result<RelationshipSettingsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    // Validate difficulty is one of the allowed values
    if !["quick", "normal", "long", "extreme"].contains(&difficulty.as_str()) {
        return Err(format!("Invalid difficulty: {}. Must be one of: quick, normal, long, extreme", difficulty));
    }

    let args = vec![user_id, difficulty];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("set-relationship-difficulty", args, secrets, Some(&app_handle), None).await?;

    let response: RelationshipSettingsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-relationship-difficulty response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_decision_config(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-decision-config", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-decision-config response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_decision_config(app_handle: AppHandle, 
    user_id: String,
    config_json: String
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, config_json];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-decision-config", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-decision-config response: {}", e))?;

    Ok(response)

}

// =============================================================================
// MEMORY RECALL CONFIG COMMANDS
// =============================================================================

#[tauri::command]
async fn get_memory_config(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-memory-config", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-memory-config response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_memory_config(app_handle: AppHandle, 
    user_id: String,
    config_json: String
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, config_json];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-memory-config", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-memory-config response: {}", e))?;

    Ok(response)

}

// =============================================================================
// ONBOARDING TUTORIAL COMMANDS
// =============================================================================

#[tauri::command]
async fn get_onboarding_preferences(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-onboarding-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-onboarding-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn mark_tutorial_seen(app_handle: AppHandle, user_id: String, tab_name: String) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, tab_name];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("mark-tutorial-seen", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse mark-tutorial-seen response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn reset_tutorial(app_handle: AppHandle, user_id: String, tab_name: String) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, tab_name];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("reset-tutorial", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reset-tutorial response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn reset_all_tutorials(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("reset-all-tutorials", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reset-all-tutorials response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_show_tutorials(app_handle: AppHandle, user_id: String, show: bool) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, (if show { "true" } else { "false" }).to_string()];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-show-tutorials", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-show-tutorials response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_difficulty_presets(app_handle: AppHandle, ) -> Result<std::collections::HashMap<String, DifficultyPreset>, String> {

    let args: Vec<String> = vec![];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-difficulty-presets", args, secrets, Some(&app_handle), None).await?;

    let response: std::collections::HashMap<String, DifficultyPreset> = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-difficulty-presets response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_qube_relationships(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: Option<String>,
) -> Result<RelationshipResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let password_str = password.unwrap_or_default();
    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password_str.as_str());

    let result = sidecar_execute_with_retry("get-qube-relationships", args, secrets, Some(&app_handle), None).await?;

    let response: RelationshipResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-qube-relationships response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_relationship_timeline(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    entity_id: String,
    password: Option<String>,
) -> Result<TimelineResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;
    validate_identifier(&entity_id, "entity_id")?;

    let password_str = password.unwrap_or_default();
    let args = vec![user_id, qube_id, entity_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password_str.as_str());

    let result = sidecar_execute_with_retry("get-relationship-timeline", args, secrets, Some(&app_handle), None).await?;

    let response: TimelineResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-relationship-timeline response: {}", e))?;

    Ok(response)

}

// Clearance Request Commands
#[tauri::command]
async fn get_pending_clearance_requests(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: Option<String>,
) -> Result<ClearanceRequestsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let password_str = password.unwrap_or_default();
    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password_str.as_str());

    let result = sidecar_execute_with_retry("get-pending-clearance-requests", args, secrets, Some(&app_handle), None).await?;

    let response: ClearanceRequestsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-pending-clearance-requests response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn approve_clearance_request(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    request_id: String,
    password: Option<String>,
    expires_in_days: Option<u32>,
) -> Result<ClearanceRequestResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let password_str = password.unwrap_or_default();
    let expires_str = expires_in_days.map(|d| d.to_string()).unwrap_or_default();

    let args = vec![user_id, qube_id, request_id, expires_str];

    let mut secrets = HashMap::new();
    secrets.insert("password", password_str.as_str());

    let result = sidecar_execute_with_retry("approve-clearance-request", args, secrets, Some(&app_handle), None).await?;

    let response: ClearanceRequestResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse approve-clearance-request response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn deny_clearance_request(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    request_id: String,
    password: Option<String>,
    reason: Option<String>,
) -> Result<ClearanceRequestResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let password_str = password.unwrap_or_default();
    let reason_str = reason.unwrap_or_default();

    let args = vec![user_id, qube_id, request_id, reason_str];

    let mut secrets = HashMap::new();
    secrets.insert("password", password_str.as_str());

    let result = sidecar_execute_with_retry("deny-clearance-request", args, secrets, Some(&app_handle), None).await?;

    let response: ClearanceRequestResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse deny-clearance-request response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_clearance_audit_log(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    limit: Option<i32>,
    entity_filter: Option<String>,
) -> Result<AuditLogResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id];

    if let Some(l) = limit {
        args.push(l.to_string());
    }

    if let Some(e) = entity_filter {
        args.push(e);
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-clearance-audit-log", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse response: {}", e))

}

// ==================== Clearance Profile v2 Commands ====================

#[tauri::command]
async fn get_clearance_profiles(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
) -> Result<ClearanceProfilesResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-clearance-profiles", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn get_available_tags(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
) -> Result<TagsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-available-tags", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn get_trait_definitions(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
) -> Result<TraitsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-trait-definitions", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn add_relationship_tag(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    entity_id: String,
    tag: String,
    password: String,
) -> Result<TagUpdateResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, entity_id, tag];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("add-relationship-tag", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn remove_relationship_tag(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    entity_id: String,
    tag: String,
    password: String,
) -> Result<TagUpdateResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, entity_id, tag];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("remove-relationship-tag", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn set_relationship_clearance(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    entity_id: String,
    profile: String,
    password: String,
    field_grants: Option<Vec<String>>,
    field_denials: Option<Vec<String>>,
    expires_in_days: Option<i32>,
) -> Result<SetClearanceResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let grants_json = field_grants.map(|v| serde_json::to_string(&v).unwrap_or_default()).unwrap_or_default();
    let denials_json = field_denials.map(|v| serde_json::to_string(&v).unwrap_or_default()).unwrap_or_default();
    let expires_str = expires_in_days.map(|d| d.to_string()).unwrap_or_default();

    let args = vec![user_id, qube_id, entity_id, profile, grants_json, denials_json, expires_str];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-relationship-clearance", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

#[tauri::command]
async fn suggest_clearance(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    entity_id: String,
) -> Result<ClearanceSuggestionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, entity_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("suggest-clearance", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Parse error: {}", e))

}

// ==================== End Clearance Profile v2 Commands ====================

#[tauri::command]
async fn get_google_tts_path(app_handle: AppHandle, user_id: String) -> Result<GoogleTTSPathResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-google-tts-path", args, secrets, Some(&app_handle), None).await?;

    let response: GoogleTTSPathResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-google-tts-path response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_google_tts_path(app_handle: AppHandle, user_id: String, path: String) -> Result<SetPathResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, path];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("set-google-tts-path", args, secrets, Some(&app_handle), None).await?;

    let response: SetPathResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-google-tts-path response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_qube_skills(app_handle: AppHandle, user_id: String, qube_id: String) -> Result<SkillsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-qube-skills", args, secrets, Some(&app_handle), None).await?;

    let response: SkillsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-qube-skills response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn save_qube_skills(app_handle: AppHandle, user_id: String, qube_id: String, skills_json: String) -> Result<SkillsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, skills_json];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("save-qube-skills", args, secrets, Some(&app_handle), None).await?;

    let response: SkillsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse save-qube-skills response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn add_skill_xp(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    skill_id: String,
    xp_amount: i32,
    evidence_block_id: Option<String>
) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id, skill_id, xp_amount.to_string()];

    if let Some(block_id) = evidence_block_id {
        args.push(block_id);
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("add-skill-xp", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse add-skill-xp response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn unlock_skill(app_handle: AppHandle, user_id: String, qube_id: String, skill_id: String) -> Result<serde_json::Value, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, skill_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("unlock-skill", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse unlock-skill response: {}", e))?;

    Ok(response)

}

// =====================================================================
// Owner Info Commands
// =====================================================================

#[tauri::command]
async fn get_owner_info(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<OwnerInfoResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-owner-info", args, secrets, Some(&app_handle), None).await?;

    let response: OwnerInfoResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-owner-info response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_owner_info_field(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    category: String,
    key: String,
    value: String,
    sensitivity: Option<String>,
    source: Option<String>,
    confidence: Option<i32>,
    block_id: Option<String>
) -> Result<GenericSuccessResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id, category, key, value];

    if let Some(sens) = sensitivity {
        args.push(sens);
    } else {
        args.push("".to_string());
    }

    if let Some(src) = source {
        args.push(src);
    } else {
        args.push("explicit".to_string());
    }

    if let Some(conf) = confidence {
        args.push(conf.to_string());
    } else {
        args.push("100".to_string());
    }

    if let Some(bid) = block_id {
        args.push(bid);
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-owner-info-field", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-owner-info-field response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_owner_info_field(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    category: String,
    key: String
) -> Result<GenericSuccessResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, category, key];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("delete-owner-info-field", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-owner-info-field response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_owner_info_sensitivity(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    category: String,
    key: String,
    sensitivity: String
) -> Result<GenericSuccessResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, category, key, sensitivity];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("update-owner-info-sensitivity", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-owner-info-sensitivity response: {}", e))?;

    Ok(response)

}

// =============================================================================
// Model Control Commands
// =============================================================================

#[tauri::command]
async fn get_model_preferences(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<ModelPreferencesResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-model-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: ModelPreferencesResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-model-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_model_lock(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    locked: bool,
    model_name: Option<String>,
    password: String,
) -> Result<ModelLockResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id, (if locked { "true" } else { "false" }).to_string()];

    if let Some(model) = &model_name {
        args.push(model.clone());
    } else {
        args.push("".to_string());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-model-lock", args, secrets, Some(&app_handle), None).await?;

    let response: ModelLockResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-model-lock response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_revolver_mode(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    enabled: bool,
    password: String,
) -> Result<RevolverModeResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, (if enabled { "true" } else { "false" }).to_string()];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-revolver-mode", args, secrets, Some(&app_handle), None).await?;

    let response: RevolverModeResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-revolver-mode response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_revolver_mode_pool(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    pool: Vec<String>,
    password: String,
) -> Result<RevolverModePoolResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let pool_json = serde_json::to_string(&pool)
        .map_err(|e| format!("Failed to serialize pool: {}", e))?;

    let args = vec![user_id, qube_id, pool_json];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-revolver-mode-pool", args, secrets, Some(&app_handle), None).await?;

    let response: RevolverModePoolResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-revolver-mode-pool response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_revolver_mode_pool(app_handle: AppHandle, 
    user_id: String,
    qube_id: String
) -> Result<RevolverModePoolResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-revolver-mode-pool", args, secrets, Some(&app_handle), None).await?;

    let response: RevolverModePoolResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-revolver-mode-pool response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_autonomous_mode_pool(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    pool: Vec<String>,
    password: String,
) -> Result<AutonomousModePoolResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let pool_json = serde_json::to_string(&pool)
        .map_err(|e| format!("Failed to serialize pool: {}", e))?;

    let args = vec![user_id, qube_id, pool_json];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-autonomous-mode-pool", args, secrets, Some(&app_handle), None).await?;

    let response: AutonomousModePoolResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-autonomous-mode-pool response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_autonomous_mode_pool(app_handle: AppHandle, 
    user_id: String,
    qube_id: String
) -> Result<AutonomousModePoolResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-autonomous-mode-pool", args, secrets, Some(&app_handle), None).await?;

    let response: AutonomousModePoolResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-autonomous-mode-pool response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn set_autonomous_mode(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    enabled: bool,
    password: String,
) -> Result<AutonomousModeResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, (if enabled { "true" } else { "false" }).to_string()];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("set-autonomous-mode", args, secrets, Some(&app_handle), None).await?;

    let response: AutonomousModeResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse set-autonomous-mode response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn clear_model_preferences(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    task_type: Option<String>
) -> Result<GenericSuccessResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, qube_id];

    if let Some(tt) = &task_type {
        args.push(tt.clone());
    }

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("clear-model-preferences", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse clear-model-preferences response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn reset_model_to_genesis(app_handle: AppHandle, user_id: String, qube_id: String) -> Result<ResetModelResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("reset-model-to-genesis", args, secrets, Some(&app_handle), None).await?;

    let response: ResetModelResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reset-model-to-genesis response: {}", e))?;

    Ok(response)

}

// =============================================================================
// Visualizer Settings
// =============================================================================

#[tauri::command]
async fn get_visualizer_settings(app_handle: AppHandle, user_id: String, qube_id: String, password: Option<String>) -> Result<VisualizerSettingsResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, password.unwrap_or_default()];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-visualizer-settings", args, secrets, Some(&app_handle), None).await?;

    let response: VisualizerSettingsResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-visualizer-settings response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn save_visualizer_settings(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    settings: String,
    password: Option<String>
) -> Result<GenericSuccessResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, settings, password.unwrap_or_default()];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("save-visualizer-settings", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse save-visualizer-settings response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_trust_personality(app_handle: AppHandle, user_id: String, qube_id: String, password: String) -> Result<TrustPersonalityResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-trust-personality", args, secrets, Some(&app_handle), None).await?;

    let response: TrustPersonalityResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-trust-personality response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_trust_personality(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    trust_profile: String
) -> Result<GenericSuccessResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    // Validate trust_profile value
    if !["cautious", "balanced", "social", "analytical"].contains(&trust_profile.as_str()) {
        return Err(format!("Invalid trust profile: {}. Must be one of: cautious, balanced, social, analytical", trust_profile));
    }

    let args = vec![user_id, qube_id, trust_profile];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("update-trust-personality", args, secrets, Some(&app_handle), None).await?;

    let response: GenericSuccessResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-trust-personality response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_available_monitors(app: tauri::AppHandle) -> Result<MonitorsResponse, String> {
    // Get monitors from the environment
    let monitors_result = app.available_monitors();

    match monitors_result {
        Ok(monitors) => {
            let monitor_info: Vec<MonitorInfo> = monitors
                .iter()
                .enumerate()
                .map(|(id, monitor)| {
                    let size = monitor.size();
                    let position = monitor.position();

                    // Simple, user-friendly monitor names
                    let display_name = if id == 0 {
                        format!("Monitor {} (Primary)", id + 1)
                    } else {
                        format!("Monitor {}", id + 1)
                    };

                    MonitorInfo {
                        id: id + 1, // Start from 1 (0 is reserved for main window)
                        name: display_name,
                        width: size.width,
                        height: size.height,
                        x: position.x,
                        y: position.y,
                        is_primary: id == 0, // First monitor is typically primary
                    }
                })
                .collect();

            Ok(MonitorsResponse {
                monitors: monitor_info,
            })
        }
        Err(e) => Err(format!("Failed to get monitors: {}", e)),
    }
}

#[tauri::command]
async fn create_visualizer_window(
    app: tauri::AppHandle,
    monitor_index: usize,
) -> Result<GenericSuccessResponse, String> {
    use tauri::Manager;
    use tauri::WebviewWindowBuilder;
    use tauri::WebviewUrl;

    // Close existing visualizer window if it exists
    if let Some(window) = app.get_webview_window("visualizer") {
        let _ = window.close();
    }

    // Get available monitors
    let monitors = app.available_monitors()
        .map_err(|e| format!("Failed to get monitors: {}", e))?;

    if monitor_index == 0 || monitor_index > monitors.len() {
        return Err(format!("Invalid monitor index: {} (valid range: 1-{})",
            monitor_index, monitors.len()));
    }

    let target_monitor = &monitors[monitor_index - 1];
    let size = target_monitor.size();
    let position = target_monitor.position();
    let scale_factor = target_monitor.scale_factor();

    // Convert physical pixels to logical pixels for DPI scaling
    let mut logical_width = (size.width as f64 / scale_factor).round();
    let logical_height = (size.height as f64 / scale_factor).round();
    let logical_x = (position.x as f64 / scale_factor).round();
    let logical_y = (position.y as f64 / scale_factor).round();

    // For monitors with negative X (positioned to the left), reduce width slightly
    // to prevent glow effects from bleeding onto adjacent monitors
    if position.x < 0 {
        logical_width -= 3.0;
    }

    // Create borderless window using logical coordinates
    match WebviewWindowBuilder::new(
        &app,
        "visualizer",
        WebviewUrl::App("visualizer".into())
    )
    .title("Qubes Visualizer")
    .position(logical_x, logical_y)
    .inner_size(logical_width, logical_height)
    .decorations(false)
    .resizable(false)
    .always_on_top(true)
    .focused(false)
    .skip_taskbar(true)
    .visible_on_all_workspaces(true)
    .build() {
        Ok(window) => {
            // Force position multiple times as Windows workaround
            for _ in 1..=3 {
                std::thread::sleep(std::time::Duration::from_millis(50));
                let _ = window.set_position(tauri::Position::Logical(tauri::LogicalPosition {
                    x: logical_x,
                    y: logical_y,
                }));
            }

            Ok(GenericSuccessResponse {
                success: true,
                message: Some(format!(
                    "Visualizer window created on monitor {} at ({}, {})",
                    monitor_index, position.x, position.y
                )),
                error: None,
            })
        },
        Err(e) => {
            Err(format!("Failed to create visualizer window: {}", e))
        }
    }
}

#[tauri::command]
async fn close_visualizer_window(app: tauri::AppHandle) -> Result<GenericSuccessResponse, String> {
    use tauri::Manager;

    if let Some(window) = app.get_webview_window("visualizer") {
        window.close().map_err(|e| format!("Failed to close window: {}", e))?;

        Ok(GenericSuccessResponse {
            success: true,
            message: Some("Visualizer window closed".to_string()),
            error: None,
        })
    } else {
        Ok(GenericSuccessResponse {
            success: true,
            message: Some("No visualizer window to close".to_string()),
            error: None,
        })
    }
}

// NFT Authentication Response Types
#[derive(Debug, Serialize, Deserialize)]
struct NftAuthResponse {
    success: bool,
    authenticated: Option<bool>,
    qube_id: Option<String>,
    public_key: Option<String>,
    category_id: Option<String>,
    nft_verified: Option<bool>,
    token: Option<String>,
    token_expires_at: Option<i64>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct NftAuthStatusResponse {
    success: bool,
    qube_id: Option<String>,
    registered: Option<bool>,
    can_authenticate: Option<bool>,
    has_nft: Option<bool>,
    category_id: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct TokenRefreshResponse {
    success: bool,
    token: Option<String>,
    expires_at: Option<i64>,
    qube_id: Option<String>,
    error: Option<String>,
}

/// Authenticate a Qube via NFT challenge-response.
/// Returns a JWT token for authenticated API requests.
#[tauri::command]
async fn authenticate_nft(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<NftAuthResponse, String> {

    // Validate inputs
    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("authenticate-nft", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse authenticate-nft response: {}", e))

}

/// Refresh an existing JWT token.
#[tauri::command]
async fn refresh_auth_token(app_handle: AppHandle, token: String) -> Result<TokenRefreshResponse, String> {

    let args: Vec<String> = vec![];

    let mut secrets = HashMap::new();
    secrets.insert("token", token.as_str());

    let result = sidecar_execute_with_retry("refresh-auth-token", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse refresh-auth-token response: {}", e))

}

/// Check if a Qube can authenticate (is registered on server).
#[tauri::command]
async fn get_nft_auth_status(app_handle: AppHandle, qube_id: String) -> Result<NftAuthStatusResponse, String> {

    // Validate inputs
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-auth-status", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-auth-status response: {}", e))

}

// P2P Command Functions

/// Get list of currently online Qubes
#[tauri::command]
async fn get_online_qubes(app_handle: AppHandle, user_id: String) -> Result<OnlineQubesResponse, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-online-qubes", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-online-qubes response: {}", e))

}

/// Generate an AI introduction message from a Qube
#[tauri::command]
async fn generate_introduction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    to_commitment: String,
    to_name: String,
    to_description: String,
    password: String,
) -> Result<GenerateIntroductionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, to_commitment, to_name, to_description];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("generate-introduction", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse generate-introduction response: {}", e))

}

/// Evaluate an incoming introduction using the Qube's AI
#[tauri::command]
async fn evaluate_introduction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    from_name: String,
    intro_message: String,
    password: String,
) -> Result<EvaluateIntroductionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, from_name, intro_message];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("evaluate-introduction", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse evaluate-introduction response: {}", e))

}

/// Process an incoming P2P message through the local Qube's AI
#[tauri::command]
async fn process_p2p_message(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    from_name: String,
    from_commitment: String,
    message: String,
    context: String,
    password: String,
) -> Result<ProcessP2PMessageResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, "--from-name".to_string(), from_name, "--from-commitment".to_string(), from_commitment, "--message".to_string(), message, "--conversation-context".to_string(), context];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("process-p2p-message", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse process-p2p-message response: {}", e))

}

/// Send an introduction request to another Qube
#[tauri::command]
async fn send_introduction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    to_commitment: String,
    message: String,
    password: String,
) -> Result<IntroductionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, to_commitment, message];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("send-introduction", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse send-introduction response: {}", e))

}

/// Get pending introduction requests
#[tauri::command]
async fn get_pending_introductions(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<PendingIntroductionsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-pending-introductions", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-pending-introductions response: {}", e))

}

/// Accept a pending introduction request
#[tauri::command]
async fn accept_introduction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    relay_id: String,
    password: String,
) -> Result<AcceptIntroductionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, relay_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("accept-introduction", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse accept-introduction response: {}", e))

}

/// Reject a pending introduction request
#[tauri::command]
async fn reject_introduction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    relay_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, relay_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("reject-introduction", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reject-introduction response: {}", e))

}

/// Get accepted connections for a Qube
#[tauri::command]
async fn get_connections(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
) -> Result<ConnectionsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-connections", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-connections response: {}", e))

}

/// Create a P2P conversation session
#[tauri::command]
async fn create_p2p_session(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    local_qubes: String,
    remote_commitments: String,
    topic: String,
    password: String,
) -> Result<P2PSessionResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id, "--local-qube-ids".to_string(), local_qubes, "--remote-commitments".to_string(), remote_commitments, "--topic".to_string(), topic];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("create-p2p-session", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse create-p2p-session response: {}", e))

}

/// Get P2P sessions for a Qube
#[tauri::command]
async fn get_p2p_sessions(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<P2PSessionsResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-p2p-sessions", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-p2p-sessions response: {}", e))

}

/// Start a P2P conversation using the same logic as local multi-qube
#[tauri::command]
async fn start_p2p_conversation(app_handle: AppHandle, 
    user_id: String,
    local_qubes: String,
    remote_connections: String,
    session_id: String,
    initial_prompt: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--local-qube-ids".to_string(), local_qubes, "--remote-connections".to_string(), remote_connections, "--session-id".to_string(), session_id, "--initial-prompt".to_string(), initial_prompt];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("start-p2p-conversation", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse start-p2p-conversation response: {}", e))

}

/// Continue P2P conversation - get next local Qube response
#[tauri::command]
async fn continue_p2p_conversation(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    session_id: String,
    local_qubes: String,
    remote_connections: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--conversation-id".to_string(), conversation_id, "--session-id".to_string(), session_id, "--local-qube-ids".to_string(), local_qubes, "--remote-connections".to_string(), remote_connections];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("continue-p2p-conversation", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse continue-p2p-conversation response: {}", e))

}

/// Inject a block received from hub into local conversation
#[tauri::command]
async fn inject_p2p_block(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    session_id: String,
    block_data: String,
    from_commitment: String,
    local_qubes: String,
    remote_connections: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--conversation-id".to_string(), conversation_id, "--session-id".to_string(), session_id, "--block-data".to_string(), block_data, "--from-commitment".to_string(), from_commitment, "--local-qube-ids".to_string(), local_qubes, "--remote-connections".to_string(), remote_connections];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("inject-p2p-block", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse inject-p2p-block response: {}", e))

}

/// Send user message in P2P conversation
#[tauri::command]
async fn send_p2p_user_message(app_handle: AppHandle, 
    user_id: String,
    conversation_id: String,
    session_id: String,
    message: String,
    local_qubes: String,
    remote_connections: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--conversation-id".to_string(), conversation_id, "--session-id".to_string(), session_id, "--message".to_string(), message, "--local-qube-ids".to_string(), local_qubes, "--remote-connections".to_string(), remote_connections];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("send-p2p-user-message", args, secrets, Some(&app_handle), None).await?;

    serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse send-p2p-user-message response: {}", e))

}

// ============================================================================
// SETUP WIZARD COMMANDS
// ============================================================================

#[derive(Debug, Serialize, Deserialize)]
struct CreateAccountResponse {
    success: bool,
    user_id: Option<String>,
    data_dir: Option<String>,
    error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct OllamaStatusResponse {
    running: bool,
    models: Vec<String>,
}

/// Response from check_first_run
#[derive(Debug, Serialize, Deserialize)]
struct FirstRunResponse {
    is_first_run: bool,
    users: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    legacy_data_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    legacy_users: Option<Vec<String>>,
}

/// Get the directory containing the application binary (for portable backups)
#[tauri::command]
fn get_bundle_dir() -> Result<String, String> {
    std::env::current_exe()
        .map_err(|e| format!("Failed to get exe path: {}", e))
        .and_then(|p| {
            p.parent()
                .map(|d| d.display().to_string())
                .ok_or_else(|| "No parent directory".to_string())
        })
}

/// Check if this is the first run (no users exist)
#[tauri::command]
async fn check_first_run() -> Result<FirstRunResponse, String> {
    // If running as AppImage without a real backend, return a descriptive error
    // so the frontend can show a helpful message instead of confusing path errors
    if std::env::var("APPIMAGE").is_ok() && !is_bundled_distribution() {
        return Err("APPIMAGE_NO_BACKEND".to_string());
    }

    // Try sidecar first (fast path, ~0ms)
    if SIDECAR_READY.load(Ordering::SeqCst) {
        eprintln!("[check_first_run] Using sidecar");
        let empty_args: Vec<String> = vec![];
        match sidecar_execute("check-first-run", &empty_args, &HashMap::new(), None, None).await {
            Ok(value) => {
                return serde_json::from_value(value)
                    .map_err(|e| format!("Sidecar parse error: {}", e));
            }
            Err(e) => {
                eprintln!("[check_first_run] Sidecar failed: {} — falling back to subprocess", e);
            }
        }
    }

    // Subprocess fallback
    eprintln!("[check_first_run] Using subprocess fallback");
    let bridge_path = get_python_bridge_path();
    let project_root = get_python_project_path();

    let (mut cmd, skip_bridge_arg) = create_backend_command();
    cmd.current_dir(&project_root);

    if !skip_bridge_arg {
        cmd.arg(&bridge_path);
    }

    let output = match cmd
        .arg("check-first-run")
        .output() {
        Ok(output) => output,
        Err(e) => {
            eprintln!("[check_first_run] Failed to execute backend: {}", e);
            return Ok(FirstRunResponse {
                is_first_run: true,
                users: vec![],
                legacy_data_dir: None,
                legacy_users: None,
            });
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        eprintln!("[check_first_run] Backend failed with status: {:?}", output.status);
        eprintln!("[check_first_run] stdout: {}", stdout.chars().take(500).collect::<String>());
        eprintln!("[check_first_run] stderr: {}", stderr.chars().take(500).collect::<String>());
        return Ok(FirstRunResponse {
            is_first_run: true,
            users: vec![],
            legacy_data_dir: None,
            legacy_users: None,
        });
    }

    eprintln!("[check_first_run] Success. stdout: {}", stdout.chars().take(200).collect::<String>());

    serde_json::from_str(&stdout.trim())
        .map_err(|e| format!("Failed to parse response: {}. Output: {}", e, stdout))
}

/// Create a new user account
#[tauri::command]
async fn create_user_account(app_handle: AppHandle, user_id: String, password: String) -> Result<CreateAccountResponse, String> {
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    match sidecar_execute_with_retry("create-user-account", args, secrets, Some(&app_handle), None).await {
        Ok(result) => {
            serde_json::from_value(result)
                .map_err(|e| format!("Failed to parse create-user-account response: {}", e))
        }
        Err(e) => {
            Ok(CreateAccountResponse {
                success: false,
                user_id: None,
                data_dir: None,
                error: Some(format!("Failed to create account: {}", e)),
            })
        }
    }
}

/// Delete a user account directory (for resetting corrupted accounts)
#[tauri::command]
async fn delete_user_account(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];
    let secrets = HashMap::new();

    sidecar_execute_with_retry("delete-user-account", args, secrets, Some(&app_handle), None).await
}

/// Change the user's master password (re-encrypts all data)
#[tauri::command]
async fn change_master_password(
    app_handle: AppHandle,
    user_id: String,
    old_password: String,
    new_password: String,
) -> Result<serde_json::Value, String> {
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];
    let mut secrets = HashMap::new();
    secrets.insert("password", old_password.as_str());
    secrets.insert("old_password", old_password.as_str());
    secrets.insert("new_password", new_password.as_str());

    sidecar_execute_with_retry("change-master-password", args, secrets, Some(&app_handle), Some(120000)).await
}

/// Migrate user data from old ./data/ path to platform-aware path
#[tauri::command]
async fn migrate_user_data(app_handle: AppHandle, old_data_dir: String, user_id: String) -> Result<serde_json::Value, String> {
    validate_identifier(&user_id, "user_id")?;

    let args = vec![old_data_dir, user_id];
    let secrets = HashMap::new();

    sidecar_execute_with_retry("migrate-user-data", args, secrets, Some(&app_handle), None).await
}

/// Get backend diagnostics - useful for debugging startup issues on Linux/macOS
#[tauri::command]
async fn get_backend_diagnostics() -> Result<serde_json::Value, String> {
    let (backend_path, diagnostics) = get_bundled_backend_path_with_diagnostics();

    // Try to get more info about the backend if found
    let mut backend_info = serde_json::json!({
        "backend_found": backend_path.is_some(),
        "backend_path": diagnostics.found_path,
        "exe_path": diagnostics.exe_path,
        "exe_dir": diagnostics.exe_dir,
        "sidecar_name": diagnostics.sidecar_name,
        "paths_checked": diagnostics.paths_checked,
        "is_dev_mode": diagnostics.is_dev_mode,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
    });

    // Add Unix-specific executable check
    #[cfg(not(target_os = "windows"))]
    {
        backend_info["is_executable"] = serde_json::json!(diagnostics.is_executable);

        // Try running a simple command to test if backend can execute
        if let Some(ref path) = backend_path {
            let test_result = Command::new(path)
                .arg("--version")
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .output();

            match test_result {
                Ok(output) => {
                    backend_info["test_run_success"] = serde_json::json!(output.status.success());
                    backend_info["test_run_stdout"] = serde_json::json!(
                        String::from_utf8_lossy(&output.stdout).to_string()
                    );
                    backend_info["test_run_stderr"] = serde_json::json!(
                        String::from_utf8_lossy(&output.stderr).to_string()
                    );
                }
                Err(e) => {
                    backend_info["test_run_success"] = serde_json::json!(false);
                    backend_info["test_run_error"] = serde_json::json!(format!("{}: {:?}", e, e.kind()));
                }
            }
        }
    }

    // On Windows, just try to check if the file exists and is accessible
    #[cfg(target_os = "windows")]
    {
        if let Some(ref path) = backend_path {
            if let Ok(metadata) = std::fs::metadata(path) {
                backend_info["file_size"] = serde_json::json!(metadata.len());
                backend_info["is_file"] = serde_json::json!(metadata.is_file());
            }
        }
    }

    // Add current working directory
    if let Ok(cwd) = std::env::current_dir() {
        backend_info["current_dir"] = serde_json::json!(cwd.display().to_string());
    }

    // Add project path that would be used
    let project_path = get_python_project_path();
    backend_info["project_path"] = serde_json::json!(project_path.display().to_string());

    Ok(backend_info)
}

/// Check Ollama status
#[tauri::command]
async fn check_ollama_status() -> Result<OllamaStatusResponse, String> {
    // Try to connect to Ollama API
    let client = reqwest::Client::new();

    match client.get("http://127.0.0.1:11434/api/tags").send().await {
        Ok(response) => {
            if response.status().is_success() {
                // Parse the models list
                if let Ok(json) = response.json::<serde_json::Value>().await {
                    let models: Vec<String> = json.get("models")
                        .and_then(|m| m.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|v| v.get("name").and_then(|n| n.as_str()).map(String::from))
                                .collect()
                        })
                        .unwrap_or_default();

                    return Ok(OllamaStatusResponse {
                        running: true,
                        models,
                    });
                }
            }
            Ok(OllamaStatusResponse {
                running: false,
                models: vec![],
            })
        }
        Err(_) => Ok(OllamaStatusResponse {
            running: false,
            models: vec![],
        }),
    }
}

/// Auto-start Ollama at app launch if not already running.
/// Runs in a background thread, silently skips if Ollama is already serving.
fn auto_start_ollama() {
    std::thread::spawn(|| {
        // Check if Ollama is already running by connecting to its API
        let is_running = std::net::TcpStream::connect_timeout(
            &"127.0.0.1:11434".parse().unwrap(),
            std::time::Duration::from_secs(2),
        ).is_ok();

        if is_running {
            eprintln!("[OLLAMA] Already running on port 11434");
            return;
        }

        eprintln!("[OLLAMA] Not running, attempting to start...");

        let exe_dir = match std::env::current_exe() {
            Ok(p) => p.parent().unwrap_or_else(|| std::path::Path::new(".")).to_path_buf(),
            Err(_) => return,
        };

        #[cfg(target_os = "windows")]
        let ollama_path = exe_dir.join("ollama").join("ollama.exe");
        #[cfg(not(target_os = "windows"))]
        let ollama_path = exe_dir.join("ollama").join("ollama");

        let models_dir = exe_dir.join("models").join("ollama");

        let mut cmd = if ollama_path.exists() {
            eprintln!("[OLLAMA] Using bundled Ollama at {}", ollama_path.display());
            Command::new(&ollama_path)
        } else {
            eprintln!("[OLLAMA] Using system Ollama");
            Command::new("ollama")
        };

        cmd.arg("serve")
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        if models_dir.exists() {
            cmd.env("OLLAMA_MODELS", &models_dir);
        }

        #[cfg(target_os = "windows")]
        {
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }

        match cmd.spawn() {
            Ok(_) => eprintln!("[OLLAMA] Started successfully"),
            Err(e) => eprintln!("[OLLAMA] Failed to start: {} (Ollama features will require manual start)", e),
        }
    });
}

/// Start bundled Ollama
#[tauri::command]
async fn start_ollama() -> Result<bool, String> {
    // Get path to bundled Ollama
    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("Failed to get exe path: {}", e))?
        .parent()
        .ok_or("Failed to get exe directory")?
        .to_path_buf();

    #[cfg(target_os = "windows")]
    let ollama_path = exe_dir.join("ollama").join("ollama.exe");
    #[cfg(not(target_os = "windows"))]
    let ollama_path = exe_dir.join("ollama").join("ollama");

    // Check for bundled models directory (heavy bundle)
    let models_dir = exe_dir.join("models").join("ollama");

    if !ollama_path.exists() {
        // Fall back to system Ollama
        let mut cmd = Command::new("ollama");
        cmd.arg("serve");
        if models_dir.exists() {
            cmd.env("OLLAMA_MODELS", &models_dir);
        }
        #[cfg(target_os = "windows")]
        {
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        cmd.spawn()
            .map_err(|e| format!("Failed to start Ollama: {}", e))?;
    } else {
        // Use bundled Ollama
        let mut cmd = Command::new(&ollama_path);
        cmd.arg("serve");
        if models_dir.exists() {
            cmd.env("OLLAMA_MODELS", &models_dir);
        }
        #[cfg(target_os = "windows")]
        {
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        cmd.spawn()
            .map_err(|e| format!("Failed to start bundled Ollama: {}", e))?;
    }

    Ok(true)
}

/// Sweep all BCH from a single Qube's wallet to a target address (without deleting the Qube)
#[tauri::command]
async fn sweep_qube_wallet(app_handle: AppHandle, user_id: String, qube_id: String, sweep_address: String, password: String) -> Result<serde_json::Value, String> {
    let args = vec![user_id, qube_id, sweep_address, password];
    let secrets = HashMap::new();
    let result = sidecar_execute_with_retry("sweep-qube-wallet", args, secrets, Some(&app_handle), None).await?;
    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse sweep-qube-wallet response: {}", e))?;
    Ok(response)
}

/// Pull an Ollama model, streaming progress events to the frontend
#[tauri::command]
async fn pull_ollama_model(app_handle: AppHandle, model_name: String) -> Result<bool, String> {
    use futures_util::StreamExt;

    let client = reqwest::Client::new();
    let body = serde_json::json!({ "name": model_name, "stream": true });

    let response = client
        .post("http://127.0.0.1:11434/api/pull")
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Failed to connect to Ollama: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("Ollama pull failed with status: {}", response.status()));
    }

    let mut stream = response.bytes_stream();
    let mut buf = String::new();

    while let Some(chunk) = stream.next().await {
        let bytes = chunk.map_err(|e| format!("Stream error: {}", e))?;
        buf.push_str(&String::from_utf8_lossy(&bytes));

        // Process complete NDJSON lines
        while let Some(pos) = buf.find('\n') {
            let line = buf[..pos].trim().to_string();
            buf = buf[pos + 1..].to_string();

            if line.is_empty() {
                continue;
            }

            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&line) {
                let payload = serde_json::json!({
                    "model": model_name,
                    "status": json.get("status").and_then(|v| v.as_str()).unwrap_or(""),
                    "completed": json.get("completed").and_then(|v| v.as_u64()),
                    "total": json.get("total").and_then(|v| v.as_u64()),
                    "digest": json.get("digest").and_then(|v| v.as_str()).unwrap_or(""),
                });
                let _ = app_handle.emit("ollama-pull-progress", payload);
            }
        }
    }

    // Emit done event
    let _ = app_handle.emit("ollama-pull-progress", serde_json::json!({
        "model": model_name,
        "status": "done",
        "completed": null,
        "total": null,
    }));

    Ok(true)
}

/// Check status of local TTS/embedding models via Python sidecar
#[tauri::command]
async fn check_local_tts_models(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {
    let args = vec![user_id];
    let secrets = HashMap::new();
    let result = sidecar_execute_with_retry("check-local-tts-models", args, secrets, Some(&app_handle), None).await?;
    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse check-local-tts-models response: {}", e))?;
    Ok(response)
}

/// Re-download/update local TTS and embedding models via Python sidecar
#[tauri::command]
async fn update_local_tts_models(app_handle: AppHandle, user_id: String) -> Result<serde_json::Value, String> {
    let args = vec![user_id];
    let secrets = HashMap::new();
    let result = sidecar_execute_with_retry("update-local-tts-models", args, secrets, Some(&app_handle), None).await?;
    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-local-tts-models response: {}", e))?;
    Ok(response)
}

/// Open external URL in default browser (using safe opener plugin)
#[tauri::command]
async fn open_external_url(url: String) -> Result<bool, String> {
    // Strict URL validation
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return Err("Invalid URL: must start with http:// or https://".to_string());
    }

    // Block URLs with suspicious characters that could be used for injection
    if url.contains('&') || url.contains('|') || url.contains(';') || url.contains('`') || url.contains('$') || url.contains('\n') || url.contains('\r') {
        return Err("Invalid URL: contains potentially dangerous characters".to_string());
    }

    // Use the safe opener plugin instead of shell commands
    tauri_plugin_opener::open_url(&url, None::<&str>)
        .map_err(|e| format!("Failed to open URL: {}", e))?;

    Ok(true)
}

/// Sync Qube to chain (backup to IPFS, encrypted to owner)
#[tauri::command]
async fn sync_to_chain(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<SyncToChainResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("sync-to-chain", args, secrets, Some(&app_handle), None).await?;

    let response: SyncToChainResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse sync-to-chain response: {}", e))?;

    Ok(response)

}

/// Transfer Qube to new owner (DESTRUCTIVE - deletes local copy)
#[tauri::command]
async fn transfer_qube(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    recipient_address: String,
    recipient_public_key: String,
    wallet_wif: String,
    password: String,
) -> Result<TransferQubeResponse, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--recipient-address".to_string(), recipient_address, "--recipient-public-key".to_string(), recipient_public_key];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("wallet_wif", wallet_wif.as_str());

    let result = sidecar_execute_with_retry("transfer-qube", args, secrets, Some(&app_handle), None).await?;

    let response: TransferQubeResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse transfer-qube response: {}", e))?;

    Ok(response)

}

/// Prepare WalletConnect-based Qube transfer (no WIF — wallet signs via WC)
#[tauri::command]
async fn prepare_transfer_wc(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    recipient_address: String,
    recipient_public_key: String,
    sender_address: String,
    master_password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![
        user_id,
        qube_id,
        recipient_address,
        recipient_public_key,
        sender_address,
    ];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());

    sidecar_execute_with_retry("prepare-transfer-wc", args, secrets, Some(&app_handle), Some(120)).await
}

/// Finalize WalletConnect Qube transfer (delete local data, refresh IPFS backup)
#[tauri::command]
async fn finalize_transfer_wc(app_handle: AppHandle,
    user_id: String,
    pending_transfer_id: String,
    transfer_txid: String,
    master_password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![
        user_id,
        pending_transfer_id,
        transfer_txid,
    ];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());

    sidecar_execute_with_retry("finalize-transfer-wc", args, secrets, Some(&app_handle), Some(60)).await
}

/// Import Qube from wallet
#[tauri::command]
async fn import_from_wallet(app_handle: AppHandle,
    user_id: String,
    wallet_wif: String,
    category_id: String,
    password: String,
) -> Result<ImportFromWalletResponse, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--category-id".to_string(), category_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("wallet_wif", wallet_wif.as_str());

    let result = sidecar_execute_with_retry("import-from-wallet", args, secrets, Some(&app_handle), None).await?;

    let response: ImportFromWalletResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse import-from-wallet response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn export_qube(app_handle: AppHandle,
    qube_id: String,
    export_path: String,
    export_password: String,
    master_password: String
) -> Result<ExportQubeResponse, String> {

    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![qube_id, export_path];

    let mut secrets = HashMap::new();
    secrets.insert("export_password", export_password.as_str());
    secrets.insert("master_password", master_password.as_str());

    let result = sidecar_execute_with_retry("export-qube", args, secrets, Some(&app_handle), None).await?;

    let response: ExportQubeResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse export-qube response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn import_qube(app_handle: AppHandle,
    import_path: String,
    import_password: String,
    master_password: String
) -> Result<ImportQubeResponse, String> {

    let args = vec![import_path];

    let mut secrets = HashMap::new();
    secrets.insert("import_password", import_password.as_str());
    secrets.insert("master_password", master_password.as_str());

    let result = sidecar_execute_with_retry("import-qube", args, secrets, Some(&app_handle), None).await?;

    let response: ImportQubeResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse import-qube response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn export_account_backup(app_handle: AppHandle,
    user_id: String,
    export_path: String,
    export_password: String,
    master_password: String
) -> Result<ExportAccountBackupResponse, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, export_path];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());
    secrets.insert("master_password", master_password.as_str());
    secrets.insert("export_password", export_password.as_str());

    let result = sidecar_execute_with_retry("export-account-backup", args, secrets, Some(&app_handle), Some(300)).await?;

    let response: ExportAccountBackupResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse export-account-backup response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn import_account_backup(app_handle: AppHandle,
    user_id: String,
    import_path: String,
    import_password: String,
    master_password: String,
    wallet_sig: Option<String>,
) -> Result<ImportAccountBackupResponse, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, import_path];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());
    secrets.insert("master_password", master_password.as_str());
    secrets.insert("import_password", import_password.as_str());
    let wallet_sig_str = wallet_sig.as_deref().unwrap_or("");
    secrets.insert("wallet_sig", wallet_sig_str);

    let result = sidecar_execute_with_retry("import-account-backup", args, secrets, Some(&app_handle), Some(300)).await?;

    let response: ImportAccountBackupResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse import-account-backup response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn export_account_backup_ipfs(app_handle: AppHandle,
    user_id: String,
    export_password: String,
    master_password: String,
    wallet_sig: Option<String>,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());
    secrets.insert("master_password", master_password.as_str());
    secrets.insert("export_password", export_password.as_str());
    let wallet_sig_str = wallet_sig.as_deref().unwrap_or("");
    secrets.insert("wallet_sig", wallet_sig_str);

    sidecar_execute_with_retry("export-account-backup-ipfs", args, secrets, Some(&app_handle), Some(300)).await
}

#[tauri::command]
async fn import_account_backup_ipfs(app_handle: AppHandle,
    user_id: String,
    ipfs_cid: String,
    import_password: String,
    master_password: String,
    wallet_sig: Option<String>,
) -> Result<ImportAccountBackupResponse, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, ipfs_cid];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());
    secrets.insert("master_password", master_password.as_str());
    secrets.insert("import_password", import_password.as_str());
    let wallet_sig_str = wallet_sig.as_deref().unwrap_or("");
    secrets.insert("wallet_sig", wallet_sig_str);

    let result = sidecar_execute_with_retry("import-account-backup-ipfs", args, secrets, Some(&app_handle), Some(300)).await?;

    let response: ImportAccountBackupResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse import-account-backup-ipfs response: {}", e))?;

    Ok(response)
}

#[tauri::command]
async fn sync_qube_to_ipfs_backup(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    export_password: String,
    master_password: String,
    wallet_sig: Option<String>,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());
    secrets.insert("master_password", master_password.as_str());
    secrets.insert("export_password", export_password.as_str());
    let wallet_sig_str = wallet_sig.as_deref().unwrap_or("");
    secrets.insert("wallet_sig", wallet_sig_str);

    sidecar_execute_with_retry("sync-qube-to-ipfs-backup", args, secrets, Some(&app_handle), Some(300)).await
}

#[tauri::command]
async fn list_account_backups_pinata(app_handle: AppHandle,
    user_id: String,
    pinata_jwt: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let mut secrets = HashMap::new();
    secrets.insert("pinata_jwt", pinata_jwt.as_str());

    sidecar_execute_with_retry("list-account-backups-pinata", args, secrets, Some(&app_handle), Some(30)).await
}

#[derive(Serialize, Deserialize)]
struct CleanupIncompleteQubesResponse {
    success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    deleted_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    deleted_dirs: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[tauri::command]
async fn cleanup_incomplete_qubes(app_handle: AppHandle,
    user_id: String,
    master_password: String
) -> Result<CleanupIncompleteQubesResponse, String> {
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];
    let mut secrets = HashMap::new();
    secrets.insert("password", master_password.as_str());

    let result = sidecar_execute_with_retry("cleanup-incomplete-qubes", args, secrets, Some(&app_handle), Some(30)).await?;

    let response: CleanupIncompleteQubesResponse = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse cleanup response: {}", e))?;

    Ok(response)
}

/// Scan wallet for Qube NFTs
#[tauri::command]
async fn scan_wallet(
    user_id: String,
    wallet_address: String,
) -> Result<ScanWalletResponse, String> {
    validate_identifier(&user_id, "user_id")?;

    let project_root = get_python_project_path();
    let (mut cmd, skip_bridge_arg) = create_backend_command();
    cmd.current_dir(&project_root);

    if !skip_bridge_arg {
        let bridge_path = get_python_bridge_path();
        cmd.arg(&bridge_path);
    }

    let output = cmd
        .arg("scan-wallet")
        .arg(&user_id)
        .arg("--wallet-address")
        .arg(&wallet_address)
        .output()
        .map_err(|e| format!("Failed to execute Python bridge: {}", e))?;

    if !output.status.success() {
        let error = String::from_utf8_lossy(&output.stderr);
        return Err(sanitize_backend_error(&error, "Operation"));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let response: ScanWalletResponse = serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse JSON response: {}. Output: {}", e, stdout))?;

    Ok(response)
}

/// Resolve public key from BCH address
#[tauri::command]
async fn resolve_public_key(
    user_id: String,
    address: String,
) -> Result<ResolvePublicKeyResponse, String> {
    validate_identifier(&user_id, "user_id")?;

    let project_root = get_python_project_path();
    let (mut cmd, skip_bridge_arg) = create_backend_command();
    cmd.current_dir(&project_root);

    if !skip_bridge_arg {
        let bridge_path = get_python_bridge_path();
        cmd.arg(&bridge_path);
    }

    let output = cmd
        .arg("resolve-public-key")
        .arg(&user_id)
        .arg("--address")
        .arg(&address)
        .output()
        .map_err(|e| format!("Failed to execute Python bridge: {}", e))?;

    if !output.status.success() {
        let error = String::from_utf8_lossy(&output.stderr);
        return Err(sanitize_backend_error(&error, "Operation"));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let response: ResolvePublicKeyResponse = serde_json::from_str(&stdout)
        .map_err(|e| format!("Failed to parse JSON response: {}. Output: {}", e, stdout))?;

    Ok(response)
}

#[tauri::command]
async fn get_debug_prompt(app_handle: AppHandle, qube_id: String) -> Result<serde_json::Value, String> {

    let args = vec![qube_id];

    let secrets = HashMap::new();

    let result = sidecar_execute_with_retry("get-debug-prompt", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-debug-prompt response: {}", e))?;

    Ok(response)

}

// =============================================================================
// GAMES Commands
// =============================================================================

#[tauri::command]
async fn start_game(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    game_type: String,
    opponent_type: String,
    opponent_id: Option<String>,
    qube_color: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, "--qube-id".to_string(), qube_id, "--game-type".to_string(), game_type, "--opponent-type".to_string(), opponent_type, "--qube-color".to_string(), qube_color];

    if let Some(ref opp_id) = opponent_id {
        args.push("--opponent-id".to_string());
        args.push(opp_id.clone());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("start-game", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse start-game response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_game_state(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-game-state", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-game-state response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_game_stats(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    game_type: Option<String>,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, "--qube-id".to_string(), qube_id];

    if let Some(gt) = game_type {
        args.push("--game-type".to_string());
        args.push(gt);
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-game-stats", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-game-stats response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn make_move(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    chess_move: String,
    player_type: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--move".to_string(), chess_move, "--player-type".to_string(), player_type];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("make-move", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse make-move response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn add_game_chat(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    message: String,
    sender_type: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--message".to_string(), message, "--sender-type".to_string(), sender_type];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("add-game-chat", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse add-game-chat response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn end_game(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    result: String,
    termination: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--result".to_string(), result, "--termination".to_string(), termination];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("end-game", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse end-game response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn abandon_game(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("abandon-game", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse abandon-game response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn request_qube_move(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("request-qube-move", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse request-qube-move response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn resign_game(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    resigning_player: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    if resigning_player != "white" && resigning_player != "black" {
        return Err("resigning_player must be 'white' or 'black'".to_string());
    }

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--resigning-player".to_string(), resigning_player];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("resign-game", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse resign-game response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn offer_draw(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    offering_player: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    if offering_player != "white" && offering_player != "black" {
        return Err("offering_player must be 'white' or 'black'".to_string());
    }

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--offering-player".to_string(), offering_player];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("offer-draw", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse offer-draw response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn respond_to_draw(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    accepting: bool,
    responding_player: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    if responding_player != "white" && responding_player != "black" {
        return Err("responding_player must be 'white' or 'black'".to_string());
    }

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--accepting".to_string(), accepting.to_string(), "--responding-player".to_string(), responding_player];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("respond-to-draw", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse respond-to-draw response: {}", e))?;

    Ok(response)

}

// =============================================================================
// Wallet Commands
// =============================================================================

#[tauri::command]
async fn get_wallet_info(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-wallet-info", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-wallet-info response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_context_preview(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-context-preview", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-context-preview response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_system_prompt_preview(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-system-prompt-preview", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-system-prompt-preview response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn propose_wallet_transaction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    to_address: String,
    amount: u64,
    memo: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--to-address".to_string(), to_address, "--amount-satoshis".to_string(), amount.to_string(), "--memo".to_string(), memo];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("propose-wallet-tx", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse propose-wallet-tx response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn approve_wallet_transaction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    tx_id: String,
    owner_wif: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--tx-id".to_string(), tx_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("owner_wif", owner_wif.as_str());

    let result = sidecar_execute_with_retry("approve-wallet-tx", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse approve-wallet-tx response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn reject_wallet_transaction(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    tx_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--tx-id".to_string(), tx_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("reject-wallet-tx", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse reject-wallet-tx response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn owner_withdraw_from_wallet(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    to_address: String,
    amount: u64,
    owner_wif: Option<String>,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--to-address".to_string(), to_address, "--amount-satoshis".to_string(), amount.to_string()];

    let wif_value = owner_wif.unwrap_or_default();
    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("owner_wif", wif_value.as_str());

    let result = sidecar_execute_with_retry("owner-withdraw", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse owner-withdraw response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_wallet_transactions(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    password: String,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let mut args = vec![user_id, "--qube-id".to_string(), qube_id];

    if let Some(lim) = limit {
        args.push("--limit".to_string());
        args.push(lim.to_string());
    }

    if let Some(off) = offset {
        args.push("--offset".to_string());
        args.push(off.to_string());
    }

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-wallet-transactions", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-wallet-transactions response: {}", e))?;

    Ok(response)

}

// ==================== WalletConnect Wallet Commands ====================

#[tauri::command]
async fn prepare_owner_withdraw_wc(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    to_address: String,
    amount: u64,
    owner_address: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--to-address".to_string(), to_address, "--amount-satoshis".to_string(), amount.to_string(), "--owner-address".to_string(), owner_address];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("prepare-owner-withdraw-wc", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse prepare-owner-withdraw-wc response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn prepare_approve_tx_wc(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    tx_id: String,
    owner_address: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--tx-id".to_string(), tx_id, "--owner-address".to_string(), owner_address];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("prepare-approve-tx-wc", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse prepare-approve-tx-wc response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn record_wallet_broadcast(app_handle: AppHandle,
    user_id: String,
    qube_id: String,
    txid: String,
    to_address: String,
    amount: u64,
    memo: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--txid".to_string(), txid, "--to-address".to_string(), to_address, "--amount-satoshis".to_string(), amount.to_string(), "--memo".to_string(), memo];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("record-wallet-broadcast", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse record-wallet-broadcast response: {}", e))?;

    Ok(response)

}

// ==================== Wallet Security Commands ====================

#[tauri::command]
async fn import_seed_phrase(app_handle: AppHandle,
    user_id: String,
    nft_address: String,
    seed_phrase: String,
    password: String,
) -> Result<serde_json::Value, String> {

    check_rate_limit("import_seed_phrase")?;
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--nft-address".to_string(), nft_address];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("seed_phrase", seed_phrase.as_str());

    let result = sidecar_execute_with_retry("import-seed-phrase", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse import-seed-phrase response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn save_owner_key(app_handle: AppHandle,
    user_id: String,
    nft_address: String,
    owner_wif: String,
    password: String,
) -> Result<serde_json::Value, String> {

    check_rate_limit("save_owner_key")?;
    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--nft-address".to_string(), nft_address];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());
    secrets.insert("owner_wif", owner_wif.as_str());

    let result = sidecar_execute_with_retry("save-owner-key", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse save-owner-key response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn delete_owner_key(app_handle: AppHandle, 
    user_id: String,
    nft_address: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id, "--nft-address".to_string(), nft_address];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("delete-owner-key", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse delete-owner-key response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn get_wallet_security(app_handle: AppHandle, 
    user_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;

    let args = vec![user_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("get-wallet-security", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse get-wallet-security response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn update_whitelist(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    whitelist: Vec<String>,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let whitelist_json = serde_json::to_string(&whitelist)
        .map_err(|e| format!("Failed to serialize whitelist: {}", e))?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--whitelist".to_string(), whitelist_json];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("update-whitelist", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse update-whitelist response: {}", e))?;

    Ok(response)

}

#[tauri::command]
async fn approve_wallet_tx_stored_key(app_handle: AppHandle, 
    user_id: String,
    qube_id: String,
    tx_id: String,
    password: String,
) -> Result<serde_json::Value, String> {

    validate_identifier(&user_id, "user_id")?;
    validate_identifier(&qube_id, "qube_id")?;

    let args = vec![user_id, "--qube-id".to_string(), qube_id, "--tx-id".to_string(), tx_id];

    let mut secrets = HashMap::new();
    secrets.insert("password", password.as_str());

    let result = sidecar_execute_with_retry("approve-wallet-tx-stored-key", args, secrets, Some(&app_handle), None).await?;

    let response: serde_json::Value = serde_json::from_value(result)
        .map_err(|e| format!("Failed to parse approve-wallet-tx-stored-key response: {}", e))?;

    Ok(response)

}

// =============================================================================
// HEAVY BUNDLE UPDATER
// =============================================================================

/// Response from check_heavy_update
#[derive(Serialize, Deserialize, Clone)]
struct HeavyUpdateInfo {
    available: bool,
    current_version: String,
    new_version: Option<String>,
    url: Option<String>,
    sha256: Option<String>,
    size: Option<u64>,
    notes: Option<String>,
}

/// Progress payload for heavy update download
#[derive(Serialize, Deserialize, Clone)]
struct HeavyUpdateProgress {
    downloaded: u64,
    total: u64,
}

/// Check if this is a heavy bundle installation (qubes-backend/ subfolder exists)
#[tauri::command]
fn is_heavy_bundle() -> bool {
    if cfg!(dev) {
        return false;
    }

    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            #[cfg(target_os = "windows")]
            let backend_path = exe_dir.join("qubes-backend").join("qubes-backend.exe");
            #[cfg(not(target_os = "windows"))]
            let backend_path = exe_dir.join("qubes-backend").join("qubes-backend");

            return backend_path.exists();
        }
    }
    false
}

/// Read the backend VERSION file. Returns "0.0.0" if missing.
#[tauri::command]
fn get_backend_version() -> String {
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            let version_path = exe_dir.join("qubes-backend").join("VERSION");
            if let Ok(version) = std::fs::read_to_string(&version_path) {
                return version.trim().to_string();
            }
        }
    }
    "0.0.0".to_string()
}

/// Get a writable directory for storing update downloads
fn get_updates_dir() -> Result<PathBuf, String> {
    let base = if cfg!(target_os = "windows") {
        std::env::var("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                dirs_fallback_home().join("AppData").join("Local")
            })
            .join("Qubes")
    } else if cfg!(target_os = "macos") {
        dirs_fallback_home()
            .join("Library")
            .join("Application Support")
            .join("Qubes")
    } else {
        std::env::var("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| dirs_fallback_home().join(".local").join("share"))
            .join("Qubes")
    };

    let updates_dir = base.join("updates");
    std::fs::create_dir_all(&updates_dir)
        .map_err(|e| format!("Failed to create updates directory: {}", e))?;
    Ok(updates_dir)
}

fn dirs_fallback_home() -> PathBuf {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// Determine the platform key for latest.json lookup
fn get_platform_key() -> &'static str {
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    { "windows-x86_64" }
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    { "linux-x86_64" }
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    { "darwin-aarch64" }
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    { "darwin-x86_64" }
    // Fallback for other architectures
    #[cfg(not(any(
        all(target_os = "windows", target_arch = "x86_64"),
        all(target_os = "linux", target_arch = "x86_64"),
        all(target_os = "macos", target_arch = "aarch64"),
        all(target_os = "macos", target_arch = "x86_64"),
    )))]
    { "unknown" }
}

/// Fetch latest.json from GitHub (primary) or qube.cash (fallback)
async fn fetch_update_manifest() -> Result<serde_json::Value, String> {
    let endpoints = [
        "https://github.com/BitFaced2/Qubes/releases/latest/download/latest.json",
        "https://qube.cash/releases/latest.json",
    ];
    let client = reqwest::Client::new();
    let mut last_error = String::new();

    for url in &endpoints {
        match client.get(*url).timeout(Duration::from_secs(15)).send().await {
            Ok(response) if response.status().is_success() => {
                match response.json::<serde_json::Value>().await {
                    Ok(manifest) => return Ok(manifest),
                    Err(e) => last_error = format!("Failed to parse manifest from {}: {}", url, e),
                }
            }
            Ok(response) => {
                last_error = format!("{} returned status: {}", url, response.status());
            }
            Err(e) => {
                last_error = format!("Failed to fetch {}: {}", url, e);
            }
        }
    }

    Err(format!("All update endpoints failed. Last error: {}", last_error))
}

/// Check for available heavy bundle updates
#[tauri::command]
async fn check_heavy_update() -> Result<HeavyUpdateInfo, String> {
    let current_version = get_backend_version();

    let manifest = fetch_update_manifest().await?;

    // Check for heavy_update section
    let heavy_update = match manifest.get("heavy_update") {
        Some(hu) => hu,
        None => {
            return Ok(HeavyUpdateInfo {
                available: false,
                current_version,
                new_version: None,
                url: None,
                sha256: None,
                size: None,
                notes: manifest.get("notes").and_then(|n| n.as_str()).map(|s| s.to_string()),
            });
        }
    };

    let new_version = heavy_update
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("0.0.0")
        .to_string();

    // Compare versions
    let available = version_is_newer(&current_version, &new_version);

    // Get platform-specific info
    let platform_key = get_platform_key();
    let platform_info = heavy_update.get(platform_key);

    let (url, sha256, size) = if let Some(pi) = platform_info {
        (
            pi.get("url").and_then(|u| u.as_str()).map(|s| s.to_string()),
            pi.get("sha256").and_then(|s| s.as_str()).map(|s| s.to_string()),
            pi.get("size").and_then(|s| s.as_u64()),
        )
    } else {
        (None, None, None)
    };

    Ok(HeavyUpdateInfo {
        available,
        current_version,
        new_version: Some(new_version),
        url,
        sha256,
        size,
        notes: manifest.get("notes").and_then(|n| n.as_str()).map(|s| s.to_string()),
    })
}

/// Simple semver comparison: returns true if new_ver > current_ver
fn version_is_newer(current: &str, new_ver: &str) -> bool {
    let parse = |v: &str| -> Vec<u32> {
        v.split('.')
            .filter_map(|p| p.parse::<u32>().ok())
            .collect()
    };
    let c = parse(current);
    let n = parse(new_ver);

    for i in 0..std::cmp::max(c.len(), n.len()) {
        let cv = c.get(i).copied().unwrap_or(0);
        let nv = n.get(i).copied().unwrap_or(0);
        if nv > cv {
            return true;
        }
        if nv < cv {
            return false;
        }
    }
    false
}

/// Download a heavy update archive with progress events
#[tauri::command]
async fn download_heavy_update(
    url: String,
    app_handle: AppHandle,
) -> Result<String, String> {
    use futures_util::StreamExt;
    use std::io::Write as IoWrite;

    let updates_dir = get_updates_dir()?;

    // Extract filename from URL
    let filename = url
        .rsplit('/')
        .next()
        .unwrap_or("qubes-update.tar.gz")
        .to_string();
    let download_path = updates_dir.join(&filename);

    // Clean up any previous download
    if download_path.exists() {
        std::fs::remove_file(&download_path)
            .map_err(|e| format!("Failed to remove old download: {}", e))?;
    }

    let client = reqwest::Client::new();
    let response = client
        .get(&url)
        .timeout(Duration::from_secs(3600)) // 1 hour timeout for large files
        .send()
        .await
        .map_err(|e| format!("Download failed: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("Download server returned status: {}", response.status()));
    }

    let total = response.content_length().unwrap_or(0);
    let mut downloaded: u64 = 0;

    let mut file = std::fs::File::create(&download_path)
        .map_err(|e| format!("Failed to create download file: {}", e))?;

    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("Download stream error: {}", e))?;
        file.write_all(&chunk)
            .map_err(|e| format!("Failed to write download data: {}", e))?;
        downloaded += chunk.len() as u64;

        // Emit progress every ~100KB to avoid flooding
        if downloaded % (100 * 1024) < chunk.len() as u64 || downloaded == total {
            let _ = app_handle.emit(
                "heavy-update-progress",
                HeavyUpdateProgress { downloaded, total },
            );
        }
    }

    file.flush()
        .map_err(|e| format!("Failed to flush download: {}", e))?;
    drop(file);

    Ok(download_path.to_string_lossy().to_string())
}

/// Verify SHA-256 hash of a downloaded update archive
#[tauri::command]
async fn verify_heavy_update(
    path: String,
    expected_sha256: String,
) -> Result<bool, String> {
    use sha2::{Sha256, Digest};

    let file = std::fs::File::open(&path)
        .map_err(|e| format!("Failed to open file for verification: {}", e))?;

    let mut reader = std::io::BufReader::new(file);
    let mut hasher = Sha256::new();

    std::io::copy(&mut reader, &mut hasher)
        .map_err(|e| format!("Failed to read file for hashing: {}", e))?;

    let hash = format!("{:x}", hasher.finalize());
    Ok(hash == expected_sha256.to_lowercase())
}

/// Move a file, falling back to copy+delete when rename fails (cross-device)
fn move_file(from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
    match std::fs::rename(from, to) {
        Ok(()) => Ok(()),
        Err(e) if e.raw_os_error() == Some(18) /* EXDEV: cross-device link */ => {
            std::fs::copy(from, to)?;
            std::fs::remove_file(from)?;
            Ok(())
        }
        Err(e) => Err(e),
    }
}

/// Move a directory, falling back to recursive copy+delete when rename fails (cross-device)
fn move_dir(from: &std::path::Path, to: &std::path::Path) -> std::io::Result<()> {
    match std::fs::rename(from, to) {
        Ok(()) => Ok(()),
        Err(e) if e.raw_os_error() == Some(18) /* EXDEV: cross-device link */ => {
            copy_dir_recursive(from, to)?;
            std::fs::remove_dir_all(from)?;
            Ok(())
        }
        Err(e) => Err(e),
    }
}

/// Recursively copy a directory
fn copy_dir_recursive(src: &std::path::Path, dst: &std::path::Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if src_path.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else {
            std::fs::copy(&src_path, &dst_path)?;
        }
    }
    Ok(())
}

/// Install a heavy update by extracting and atomically swapping directories
#[tauri::command]
async fn install_heavy_update(archive_path: String) -> Result<bool, String> {
    use flate2::read::GzDecoder;

    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("Failed to get exe path: {}", e))?
        .parent()
        .ok_or("Failed to get exe directory")?
        .to_path_buf();

    let updates_dir = get_updates_dir()?;
    let staging_dir = updates_dir.join("staging");

    // Clean up any previous staging
    if staging_dir.exists() {
        std::fs::remove_dir_all(&staging_dir)
            .map_err(|e| format!("Failed to clean staging directory: {}", e))?;
    }
    std::fs::create_dir_all(&staging_dir)
        .map_err(|e| format!("Failed to create staging directory: {}", e))?;

    // Extract tar.gz to staging
    let archive_file = std::fs::File::open(&archive_path)
        .map_err(|e| format!("Failed to open update archive: {}", e))?;
    let decoder = GzDecoder::new(archive_file);
    let mut archive = tar::Archive::new(decoder);

    archive.unpack(&staging_dir)
        .map_err(|e| format!("Failed to extract update archive: {}", e))?;

    // ── Swap backend directory ──────────────────────────────────────────
    let staged_backend = staging_dir.join("qubes-backend");
    let current_backend = exe_dir.join("qubes-backend");
    let backup_backend = exe_dir.join("qubes-backend.old");

    if staged_backend.exists() && current_backend.exists() {
        // Remove previous backup if exists
        if backup_backend.exists() {
            std::fs::remove_dir_all(&backup_backend).ok();
        }

        // Swap: rename current → .old, move staged → current
        std::fs::rename(&current_backend, &backup_backend)
            .map_err(|e| {
                format!("Failed to backup current backend: {}. No changes made.", e)
            })?;

        if let Err(e) = move_dir(&staged_backend, &current_backend) {
            // Rollback: restore from backup
            let _ = std::fs::rename(&backup_backend, &current_backend);
            return Err(format!(
                "Failed to install new backend (rolled back): {}", e
            ));
        }

        // On Unix, ensure backend binary is executable
        #[cfg(not(target_os = "windows"))]
        {
            use std::os::unix::fs::PermissionsExt;
            let backend_bin = current_backend.join("qubes-backend");
            if let Ok(metadata) = std::fs::metadata(&backend_bin) {
                let mut perms = metadata.permissions();
                perms.set_mode(0o755);
                let _ = std::fs::set_permissions(&backend_bin, perms);
            }
        }
    }

    // ── Swap covenant directory ─────────────────────────────────────────
    let staged_covenant = staging_dir.join("covenant");
    let current_covenant = exe_dir.join("covenant");
    let backup_covenant = exe_dir.join("covenant.old");

    if staged_covenant.exists() {
        if current_covenant.exists() {
            if backup_covenant.exists() {
                std::fs::remove_dir_all(&backup_covenant).ok();
            }
            std::fs::rename(&current_covenant, &backup_covenant).ok();
        }
        if let Err(e) = move_dir(&staged_covenant, &current_covenant) {
            // Non-fatal: covenant can be reinstalled manually
            eprintln!("Warning: failed to install covenant update: {}", e);
            // Rollback if we had a backup
            if backup_covenant.exists() && !current_covenant.exists() {
                let _ = std::fs::rename(&backup_covenant, &current_covenant);
            }
        }
    }

    // ── Swap node directory ──────────────────────────────────────────────
    let staged_node = staging_dir.join("node");
    let current_node = exe_dir.join("node");
    let backup_node = exe_dir.join("node.old");

    if staged_node.exists() {
        if current_node.exists() {
            if backup_node.exists() {
                std::fs::remove_dir_all(&backup_node).ok();
            }
            std::fs::rename(&current_node, &backup_node).ok();
        }
        if let Err(e) = move_dir(&staged_node, &current_node) {
            eprintln!("Warning: failed to install node update: {}", e);
            if backup_node.exists() && !current_node.exists() {
                let _ = std::fs::rename(&backup_node, &current_node);
            }
        } else {
            // On Unix, ensure node binary is executable
            #[cfg(not(target_os = "windows"))]
            {
                use std::os::unix::fs::PermissionsExt;
                let node_bin = current_node.join("node");
                if let Ok(metadata) = std::fs::metadata(&node_bin) {
                    let mut perms = metadata.permissions();
                    perms.set_mode(0o755);
                    let _ = std::fs::set_permissions(&node_bin, perms);
                }
            }
        }
    }

    // ── Swap frontend binary ────────────────────────────────────────────
    #[cfg(target_os = "windows")]
    let frontend_name = "Qubes.exe";
    #[cfg(target_os = "macos")]
    let frontend_name = "Qubes";
    #[cfg(target_os = "linux")]
    let frontend_name = "Qubes";

    let staged_frontend = staging_dir.join(frontend_name);
    let current_frontend = exe_dir.join(frontend_name);
    let backup_frontend_name = format!("{}.old", frontend_name);
    let backup_frontend = exe_dir.join(&backup_frontend_name);

    if staged_frontend.exists() && current_frontend.exists() {
        // Remove previous backup
        if backup_frontend.exists() {
            std::fs::remove_file(&backup_frontend).ok();
        }

        // Rename running exe → .old (works on both Windows and Linux)
        std::fs::rename(&current_frontend, &backup_frontend)
            .map_err(|e| format!("Failed to backup frontend binary: {}", e))?;

        if let Err(e) = move_file(&staged_frontend, &current_frontend) {
            // Rollback frontend
            let _ = std::fs::rename(&backup_frontend, &current_frontend);
            return Err(format!(
                "Failed to install new frontend (rolled back): {}", e
            ));
        }

        // On Unix, ensure new binary is executable
        #[cfg(not(target_os = "windows"))]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(metadata) = std::fs::metadata(&current_frontend) {
                let mut perms = metadata.permissions();
                perms.set_mode(0o755);
                let _ = std::fs::set_permissions(&current_frontend, perms);
            }
        }
    }

    // Clean up staging directory
    std::fs::remove_dir_all(&staging_dir).ok();

    // Clean up downloaded archive
    std::fs::remove_file(&archive_path).ok();

    Ok(true)
}

/// Clean up leftover .old files from a previous update. Called on app startup.
#[tauri::command]
fn cleanup_old_backend() -> bool {
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            // Clean up old backend directory
            let backup_backend = exe_dir.join("qubes-backend.old");
            if backup_backend.exists() {
                let _ = std::fs::remove_dir_all(&backup_backend);
            }

            // Clean up old covenant directory
            let backup_covenant = exe_dir.join("covenant.old");
            if backup_covenant.exists() {
                let _ = std::fs::remove_dir_all(&backup_covenant);
            }

            // Clean up old node directory
            let backup_node = exe_dir.join("node.old");
            if backup_node.exists() {
                let _ = std::fs::remove_dir_all(&backup_node);
            }

            // Clean up old frontend binary
            #[cfg(target_os = "windows")]
            let backup_frontend = exe_dir.join("Qubes.exe.old");
            #[cfg(not(target_os = "windows"))]
            let backup_frontend = exe_dir.join("Qubes.old");

            if backup_frontend.exists() {
                let _ = std::fs::remove_file(&backup_frontend);
            }

            return true;
        }
    }
    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            greet,
            authenticate,
            get_available_models,
            list_qubes,
            create_qube,
            prepare_qube_mint,
            finalize_qube_mint,
            save_cropped_avatar,
            list_pending_registrations,
            send_message,
            generate_speech,
            get_audio_base64,
            play_audio_native,
            stop_audio_native,
            anchor_session,
            discard_session,
            delete_session_block,
            discard_last_block,
            check_sessions,
            force_exit,
            update_qube_config,
            update_qube_avatar,
            get_qube_blocks,
            recall_last_context,
            delete_qube,
            reset_qube,
            save_image,
            upload_avatar_to_ipfs,
            analyze_image,
            start_multi_qube_conversation,
            get_next_speaker,
            continue_multi_qube_conversation,
            run_background_turns,
            inject_multi_qube_user_message,
            lock_in_multi_qube_response,
            end_multi_qube_conversation,
            get_configured_api_keys,
            save_api_key,
            validate_api_key,
            delete_api_key,
            get_block_preferences,
            update_block_preferences,
            get_relationship_difficulty,
            set_relationship_difficulty,
            get_difficulty_presets,
            get_decision_config,
            update_decision_config,
            get_memory_config,
            update_memory_config,
            get_onboarding_preferences,
            mark_tutorial_seen,
            reset_tutorial,
            reset_all_tutorials,
            update_show_tutorials,
            get_qube_relationships,
            get_relationship_timeline,
            // Clearance Requests
            get_pending_clearance_requests,
            approve_clearance_request,
            deny_clearance_request,
            get_clearance_audit_log,
            // Clearance Profiles v2
            get_clearance_profiles,
            get_available_tags,
            get_trait_definitions,
            add_relationship_tag,
            remove_relationship_tag,
            set_relationship_clearance,
            suggest_clearance,
            get_google_tts_path,
            set_google_tts_path,
            get_qube_skills,
            save_qube_skills,
            add_skill_xp,
            unlock_skill,
            // Owner Info
            get_owner_info,
            set_owner_info_field,
            delete_owner_info_field,
            update_owner_info_sensitivity,
            // Model Control
            get_model_preferences,
            set_model_lock,
            set_revolver_mode,
            set_revolver_mode_pool,
            get_revolver_mode_pool,
            set_autonomous_mode_pool,
            get_autonomous_mode_pool,
            set_autonomous_mode,
            clear_model_preferences,
            reset_model_to_genesis,
            // Visualizer
            get_visualizer_settings,
            save_visualizer_settings,
            get_trust_personality,
            update_trust_personality,
            get_available_monitors,
            create_visualizer_window,
            close_visualizer_window,
            // NFT Authentication
            authenticate_nft,
            refresh_auth_token,
            get_nft_auth_status,
            // P2P Network
            get_online_qubes,
            generate_introduction,
            evaluate_introduction,
            process_p2p_message,
            send_introduction,
            get_pending_introductions,
            accept_introduction,
            reject_introduction,
            get_connections,
            create_p2p_session,
            get_p2p_sessions,
            // P2P Conversations (uses same logic as local multi-qube)
            start_p2p_conversation,
            continue_p2p_conversation,
            inject_p2p_block,
            send_p2p_user_message,
            // Setup Wizard
            get_bundle_dir,
            check_first_run,
            create_user_account,
            delete_user_account,
            change_master_password,
            migrate_user_data,
            check_ollama_status,
            get_backend_diagnostics,
            start_ollama,
            sweep_qube_wallet,
            pull_ollama_model,
            check_local_tts_models,
            update_local_tts_models,
            open_external_url,
            // Chain Sync (NFT-Bundled Storage)
            sync_to_chain,
            transfer_qube,
            prepare_transfer_wc,
            finalize_transfer_wc,
            import_from_wallet,
            export_qube,
            import_qube,
            export_account_backup,
            import_account_backup,
            export_account_backup_ipfs,
            sync_qube_to_ipfs_backup,
            list_account_backups_pinata,
            import_account_backup_ipfs,
            cleanup_incomplete_qubes,
            scan_wallet,
            resolve_public_key,
            // Dev debugging
            get_debug_prompt,
            // Games
            start_game,
            get_game_state,
            get_game_stats,
            make_move,
            add_game_chat,
            end_game,
            abandon_game,
            request_qube_move,
            resign_game,
            offer_draw,
            respond_to_draw,
            // Wallet Commands
            get_wallet_info,
            get_context_preview,
            get_system_prompt_preview,
            propose_wallet_transaction,
            approve_wallet_transaction,
            reject_wallet_transaction,
            owner_withdraw_from_wallet,
            get_wallet_transactions,
            // WalletConnect Wallet Commands
            prepare_owner_withdraw_wc,
            prepare_approve_tx_wc,
            record_wallet_broadcast,
            // Wallet Security Commands
            import_seed_phrase,
            save_owner_key,
            delete_owner_key,
            get_wallet_security,
            update_whitelist,
            approve_wallet_tx_stored_key,
            // Event Watcher Commands
            start_event_watcher_cmd,
            stop_event_watcher_cmd,
            // Voice Settings Commands
            get_voice_settings,
            update_voice_settings,
            preview_voice,
            add_voice_to_library,
            delete_voice_from_library,
            get_voice_library,
            check_qwen3_status,
            check_kokoro_status,
            get_tts_progress,
            download_qwen3_model,
            get_qwen3_download_progress,
            cancel_qwen3_download,
            delete_qwen3_model,
            // GPU Acceleration Commands
            check_gpu_acceleration,
            install_gpu_acceleration,
            get_gpu_install_progress,
            uninstall_gpu_acceleration,
            update_qwen3_preferences,
            record_voice_clone_audio,
            save_recorded_audio,
            transcribe_audio,
            // WSL2 TTS Setup Commands
            check_wsl2_tts_status,
            setup_wsl2_tts,
            get_wsl2_tts_setup_progress,
            start_wsl2_tts_server,
            stop_wsl2_tts_server,
            uninstall_wsl2_tts,
            install_wsl2,
            // Heavy Bundle Updater
            is_heavy_bundle,
            get_backend_version,
            check_heavy_update,
            download_heavy_update,
            verify_heavy_update,
            install_heavy_update,
            cleanup_old_backend
        ])
        .setup(|app| {
            // Clean up leftover .old files from previous heavy bundle update
            cleanup_old_backend();

            // Auto-start Ollama if not already running (background, non-blocking)
            auto_start_ollama();

            // Start persistent sidecar backend process (background, non-blocking)
            let sidecar_app_handle = app.handle().clone();
            std::thread::spawn(move || {
                if let Err(e) = start_sidecar(&sidecar_app_handle) {
                    eprintln!("[SIDECAR] Failed to start on launch: {} — will use subprocess fallback", e);
                }
            });

            // Get the main and splash windows
            let splashscreen_window = app.get_webview_window("splashscreen").unwrap();
            let main_window = app.get_webview_window("main").unwrap();

            // Clone for the thread
            let main_window_clone = main_window.clone();
            let splashscreen_window_clone = splashscreen_window.clone();

            // Start TTS server in background (keeps Qwen3 model loaded for fast TTS)
            // Only in dev mode - in production, TTS works on-demand via the bundled backend
            if !is_bundled_distribution() {
                std::thread::spawn(|| {
                    if let Ok(python_path) = find_python_path() {
                        let project_root = get_python_project_path();
                        let mut cmd = std::process::Command::new(&python_path);
                        cmd.args(["-m", "audio.tts_server", "start"])
                            .current_dir(&project_root)
                            .stdout(std::process::Stdio::null())
                            .stderr(std::process::Stdio::null());

                        // Hide console window on Windows
                        #[cfg(target_os = "windows")]
                        {
                            use std::os::windows::process::CommandExt;
                            const CREATE_NO_WINDOW: u32 = 0x08000000;
                            cmd.creation_flags(CREATE_NO_WINDOW);
                        }

                        if let Err(e) = cmd.spawn() {
                            eprintln!("Failed to start TTS server: {}", e);
                        }
                    }
                });
            }

            // Wait for the main window to finish loading, then close splash
            std::thread::spawn(move || {
                // Wait 1.5 seconds to ensure main window is ready
                std::thread::sleep(std::time::Duration::from_millis(1500));

                // Show the main window
                main_window_clone.show().unwrap();

                // Close the splashscreen
                splashscreen_window_clone.close().unwrap();
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Stop sidecar and TTS server when main window closes
            if let tauri::WindowEvent::Destroyed = event {
                if window.label() == "main" {
                    stop_sidecar();

                    // Stop TTS servers (only in dev mode - production doesn't start them)
                    if !is_bundled_distribution() {
                        // Stop Windows TTS server (legacy)
                        if let Ok(python_path) = find_python_path() {
                            let project_root = get_python_project_path();
                            let mut cmd = std::process::Command::new(&python_path);
                            cmd.args(["-m", "audio.tts_server", "stop"])
                                .current_dir(&project_root)
                                .stdout(std::process::Stdio::null())
                                .stderr(std::process::Stdio::null());

                            #[cfg(target_os = "windows")]
                            {
                                use std::os::windows::process::CommandExt;
                                const CREATE_NO_WINDOW: u32 = 0x08000000;
                                cmd.creation_flags(CREATE_NO_WINDOW);
                            }

                            let _ = cmd.spawn();
                        }

                        // Stop WSL2 TTS server
                        let mut wsl_cmd = std::process::Command::new("wsl");
                        wsl_cmd.args(["-d", "Ubuntu-22.04", "--", "pkill", "-f", "tts_server.py"])
                            .stdout(std::process::Stdio::null())
                            .stderr(std::process::Stdio::null());

                        #[cfg(target_os = "windows")]
                        {
                            use std::os::windows::process::CommandExt;
                            const CREATE_NO_WINDOW: u32 = 0x08000000;
                            wsl_cmd.creation_flags(CREATE_NO_WINDOW);
                        }

                        let _ = wsl_cmd.spawn();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

