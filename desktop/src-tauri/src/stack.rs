use std::fs;
use std::net::ToSocketAddrs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::Duration;

use rcgen::CertifiedKey;
use serde::Serialize;
use tauri::{AppHandle, Manager};

const COMPOSE_FILE: &str = "docker-compose.dev.yml";
const HEALTH_URL: &str = "https://app.localhost/api/v1/health/live";
const APP_LOGIN: &str = "https://app.localhost/login";
const ALLOWLIST: &str = "allow 127.0.0.1;\nallow ::1;\ndeny all;\n";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct StackStatus {
    pub home: Option<String>,
    pub docker: bool,
    pub compose: bool,
    pub env_file: bool,
    pub tls: bool,
    pub hosts_ok: bool,
    pub live: bool,
    pub login_url: String,
    pub warnings: Vec<String>,
    pub error: Option<String>,
    pub log: String,
}

pub fn is_repo_home(path: &Path) -> bool {
    path.join(COMPOSE_FILE).is_file() && path.join("server").is_dir() && path.join("web").is_dir()
}

fn config_home_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    Ok(dir.join("home.txt"))
}

fn read_saved_home(app: &AppHandle) -> Option<PathBuf> {
    let path = config_home_path(app).ok()?;
    let text = fs::read_to_string(path).ok()?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(PathBuf::from(trimmed))
    }
}

fn walk_up(start: &Path) -> Option<PathBuf> {
    let mut current = start.to_path_buf();
    loop {
        if is_repo_home(&current) {
            return Some(current);
        }
        if !current.pop() {
            return None;
        }
    }
}

fn find_home(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(value) = std::env::var("SUPERBOSS_HOME") {
        let path = PathBuf::from(value.trim());
        if is_repo_home(&path) {
            return Some(path);
        }
    }
    if let Some(path) = read_saved_home(app) {
        if is_repo_home(&path) {
            return Some(path);
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        if let Some(path) = walk_up(&cwd) {
            return Some(path);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            if let Some(path) = walk_up(parent) {
                return Some(path);
            }
        }
    }
    None
}

fn hide_window(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = command;
}

fn look_path(name: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
        let exe = dir.join(format!("{name}.exe"));
        if exe.is_file() {
            return Some(exe);
        }
    }
    None
}

fn docker_bin() -> PathBuf {
    static BIN: OnceLock<PathBuf> = OnceLock::new();
    BIN.get_or_init(|| {
        if let Some(path) = look_path("docker") {
            return path;
        }
        let program_files =
            PathBuf::from(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe");
        if program_files.is_file() {
            return program_files;
        }
        PathBuf::from("docker")
    })
    .clone()
}

fn docker_cmd() -> Command {
    let mut command = Command::new(docker_bin());
    hide_window(&mut command);
    command
}

fn git_cmd() -> Command {
    let bin = look_path("git").unwrap_or_else(|| {
        let program_files = PathBuf::from(r"C:\Program Files\Git\cmd\git.exe");
        if program_files.is_file() {
            program_files
        } else {
            PathBuf::from("git")
        }
    });
    let mut command = Command::new(bin);
    hide_window(&mut command);
    command
}

fn docker_available() -> bool {
    let mut command = docker_cmd();
    command
        .args(["version", "--format", "{{.Server.Version}}"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn host_is_loopback(name: &str) -> bool {
    (name, 443u16)
        .to_socket_addrs()
        .map(|addrs| addrs.into_iter().any(|addr| addr.ip().is_loopback()))
        .unwrap_or(false)
}

fn hosts_ok() -> bool {
    host_is_loopback("app.localhost") && host_is_loopback("objects.localhost")
}

fn run_write_hosts_script(script: &Path) -> Result<String, String> {
    let temp = std::env::temp_dir().join("superboss-write-local-hosts.ps1");
    fs::copy(script, &temp).map_err(|e| format!("无法复制 hosts 脚本：{e}"))?;
    let temp_display = temp.display().to_string().replace('\'', "''");
    let command = format!(
        "Start-Process -FilePath powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{temp_display}\"'"
    );
    let mut process = Command::new("powershell");
    process.args(["-NoProfile", "-Command", &command]);
    run_capture(&mut process)
}

fn tls_ready(home: &Path) -> bool {
    let dir = home.join("ops").join("local-tls");
    dir.join("tls.crt").is_file() && dir.join("tls.key").is_file() && dir.join("allowlist.conf").is_file()
}

pub fn health_url_allowed(url: &str) -> bool {
    reqwest::Url::parse(url)
        .ok()
        .and_then(|parsed| parsed.host_str().map(str::to_string))
        .is_some_and(|host| host == "app.localhost" || host == "127.0.0.1")
}

async fn probe_live() -> Result<(), String> {
    if !health_url_allowed(HEALTH_URL) {
        return Err("health URL host not allowed".into());
    }
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| e.to_string())?;
    let response = client
        .get(HEALTH_URL)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !response.status().is_success() {
        return Err(format!("health HTTP {}", response.status()));
    }
    let body = response.text().await.map_err(|e| e.to_string())?;
    if !body.contains("\"status\"") || !body.contains("ok") {
        return Err(format!("unexpected health body: {body}"));
    }
    Ok(())
}

fn run_capture(command: &mut Command) -> Result<String, String> {
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let output = command.output().map_err(|e| format!("无法启动进程：{e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = format!("{stdout}{stderr}");
    if output.status.success() {
        Ok(combined)
    } else {
        Err(combined.trim().to_string())
    }
}

fn compose(home: &Path, args: &[&str]) -> Result<String, String> {
    let mut command = docker_cmd();
    command
        .current_dir(home)
        .args(["compose", "--env-file", ".env", "-f", COMPOSE_FILE])
        .args(args);
    run_capture(&mut command)
}

fn ensure_env(home: &Path) -> Result<(), String> {
    let env_path = home.join(".env");
    if env_path.exists() {
        return Ok(());
    }
    let example = home.join(".env.example");
    if !example.is_file() {
        return Err("缺少 .env.example，无法创建 .env".into());
    }
    fs::copy(&example, &env_path).map_err(|e| e.to_string())?;
    Ok(())
}

fn try_import_cert(cert: &Path) -> String {
    let mut command = Command::new("certutil");
    hide_window(&mut command);
    let cert_str = match cert.to_str() {
        Some(value) => value,
        None => return "证书路径无法作为命令参数。".into(),
    };
    command.args(["-user", "-addstore", "Root", cert_str]);
    match run_capture(&mut command) {
        Ok(_) => "已把本机证书写入当前用户「受信任的根证书颁发机构」。".into(),
        Err(err) => format!(
            "自动导入证书失败。请把 {} 手动导入当前用户的「受信任的根证书颁发机构」。{}",
            cert.display(),
            err.trim()
        ),
    }
}

fn ensure_tls(home: &Path) -> Result<Vec<String>, String> {
    let mut notes = Vec::new();
    let dir = home.join("ops").join("local-tls");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let allow = dir.join("allowlist.conf");
    if !allow.exists() {
        fs::write(&allow, ALLOWLIST).map_err(|e| e.to_string())?;
    }
    let cert = dir.join("tls.crt");
    let key = dir.join("tls.key");
    if cert.exists() && key.exists() {
        return Ok(notes);
    }
    let names = vec![
        "app.localhost".to_string(),
        "objects.localhost".to_string(),
        "127.0.0.1".to_string(),
    ];
    let CertifiedKey { cert: generated, key_pair } =
        rcgen::generate_simple_self_signed(names).map_err(|e| format!("生成证书失败：{e}"))?;
    fs::write(&cert, generated.pem()).map_err(|e| e.to_string())?;
    fs::write(&key, key_pair.serialize_pem()).map_err(|e| e.to_string())?;
    notes.push(format!(
        "已生成本机 HTTPS 证书：{}",
        cert.display()
    ));
    notes.push(try_import_cert(&cert));
    Ok(notes)
}

fn empty_status(home: Option<PathBuf>, warnings: Vec<String>, error: Option<String>, log: String) -> StackStatus {
    let compose = home.as_ref().is_some_and(|path| is_repo_home(path));
    let env_file = home.as_ref().is_some_and(|path| path.join(".env").is_file());
    let tls = home.as_ref().is_some_and(|path| tls_ready(path));
    StackStatus {
        home: home.map(|path| path.display().to_string()),
        docker: docker_available(),
        compose,
        env_file,
        tls,
        hosts_ok: hosts_ok(),
        live: false,
        login_url: APP_LOGIN.to_string(),
        warnings,
        error,
        log,
    }
}

#[tauri::command]
pub async fn stack_status(app: AppHandle) -> Result<StackStatus, String> {
    let home = find_home(&app);
    let mut warnings = Vec::new();
    if home.is_none() {
        warnings.push(
            "未找到仓库。设置 SUPERBOSS_HOME，或在下方填入克隆路径。安装包只是启动器，不含 Docker 镜像与源码。"
                .into(),
        );
    }
    if !docker_available() {
        warnings.push("未检测到 Docker。请安装并启动 Docker Desktop。".into());
    }
    if !hosts_ok() {
        warnings.push(
            "hosts 未把 app.localhost 指到 127.0.0.1。请以管理员写入：127.0.0.1 app.localhost 与 127.0.0.1 objects.localhost。"
                .into(),
        );
    }
    let live = probe_live().await.is_ok();
    let mut status = empty_status(home, warnings, None, String::new());
    status.live = live;
    Ok(status)
}

#[tauri::command]
pub async fn set_home(app: AppHandle, path: String) -> Result<StackStatus, String> {
    let home = PathBuf::from(path.trim());
    if !is_repo_home(&home) {
        return Err("该路径不是 SuperBoss 仓库（需要 docker-compose.dev.yml、server/、web/）".into());
    }
    let config = config_home_path(&app)?;
    if let Some(dir) = config.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    fs::write(&config, home.to_string_lossy().as_bytes()).map_err(|e| e.to_string())?;
    stack_status(app).await
}

#[tauri::command]
pub async fn write_hosts(app: AppHandle) -> Result<StackStatus, String> {
    let home = find_home(&app).ok_or_else(|| "未找到仓库路径".to_string())?;
    let script = home.join("ops").join("write-local-hosts.ps1");
    if !script.is_file() {
        return Err(format!("缺少 {}", script.display()));
    }
    let log = tauri::async_runtime::spawn_blocking(move || run_write_hosts_script(&script))
        .await
        .map_err(|e| e.to_string())??;
    let mut status = stack_status(app).await?;
    status.log = log;
    if !status.hosts_ok {
        status.error = Some(
            "已尝试写入 hosts。若弹出了 UAC 且未同意，或写入后仍未就绪，请以管理员运行 ops/write-local-hosts.ps1。"
                .into(),
        );
    }
    Ok(status)
}

#[tauri::command]
pub async fn start_stack(app: AppHandle) -> Result<StackStatus, String> {
    let home = find_home(&app).ok_or_else(|| "未找到仓库路径".to_string())?;
    if !docker_available() {
        return Err("Docker 未运行".into());
    }
    ensure_env(&home)?;
    let tls_notes = ensure_tls(&home)?;
    let home_for_compose = home.clone();
    let log = tauri::async_runtime::spawn_blocking(move || compose(&home_for_compose, &["up", "-d"]))
        .await
        .map_err(|e| e.to_string())??;
    for _ in 0..60 {
        if probe_live().await.is_ok() {
            break;
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
    let mut status = stack_status(app).await?;
    status.warnings.extend(tls_notes);
    status.log = log;
    if !status.live {
        status.error = Some(
            "栈已启动，但 https://app.localhost/api/v1/health/live 尚未就绪。首次构建可能需要几分钟。"
                .into(),
        );
    }
    Ok(status)
}

#[tauri::command]
pub async fn stop_stack(app: AppHandle) -> Result<StackStatus, String> {
    let home = find_home(&app).ok_or_else(|| "未找到仓库路径".to_string())?;
    let log = tauri::async_runtime::spawn_blocking(move || compose(&home, &["down"]))
        .await
        .map_err(|e| e.to_string())??;
    let mut status = stack_status(app).await?;
    status.log = log;
    Ok(status)
}

#[tauri::command]
pub async fn update_stack(app: AppHandle) -> Result<StackStatus, String> {
    let home = find_home(&app).ok_or_else(|| "未找到仓库路径".to_string())?;
    let home_for_git = home.clone();
    let git_log = tauri::async_runtime::spawn_blocking(move || {
        let mut command = git_cmd();
        command.current_dir(&home_for_git).args(["pull", "--ff-only"]);
        run_capture(&mut command)
    })
    .await
    .map_err(|e| e.to_string())??;
    let home_for_compose = home.clone();
    let compose_log =
        tauri::async_runtime::spawn_blocking(move || compose(&home_for_compose, &["up", "-d", "--build"]))
            .await
            .map_err(|e| e.to_string())??;
    for _ in 0..80 {
        if probe_live().await.is_ok() {
            break;
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
    let mut status = stack_status(app).await?;
    status.log = format!("{git_log}\n{compose_log}");
    Ok(status)
}

#[cfg(test)]
mod tests {
    use super::{health_url_allowed, is_repo_home};
    use std::fs;

    #[test]
    fn repo_home_requires_compose_server_and_web() {
        let tmp = std::env::temp_dir().join(format!("sb-home-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join("server")).unwrap();
        fs::create_dir_all(tmp.join("web")).unwrap();
        assert!(!is_repo_home(&tmp));
        fs::write(tmp.join("docker-compose.dev.yml"), "x: 1\n").unwrap();
        assert!(is_repo_home(&tmp));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn health_probe_only_allows_local_hosts() {
        assert!(health_url_allowed("https://app.localhost/api/v1/health/live"));
        assert!(health_url_allowed("https://127.0.0.1/api/v1/health/live"));
        assert!(!health_url_allowed("https://example.com/api/v1/health/live"));
    }
}
