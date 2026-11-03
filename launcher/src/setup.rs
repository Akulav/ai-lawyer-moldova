//! Folder layout, embedded sources, uv, and the server process.
use std::fs;
use std::io::{Read, Write};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use anyhow::Context;
use include_dir::{include_dir, Dir, DirEntry};

use crate::{runtime_dir, VERSION};

// The app's sources ship inside the exe, so a release is a single file.
static APP_DIR: Dir = include_dir!("$CARGO_MANIFEST_DIR/../app");
const PYPROJECT: &[u8] = include_bytes!("../../pyproject.toml");
const UV_LOCK: &[u8] = include_bytes!("../../uv.lock");

const UV_ZIP: &str = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// The folder the app lives in: the exe's own folder, or AVOCAT_ROOT for development. Everything, from
/// Python to the models, stays inside it; nothing is installed system-wide.
pub fn root() -> PathBuf {
    if let Some(r) = std::env::var_os("AVOCAT_ROOT") {
        return PathBuf::from(r);
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn log_path(root: &Path) -> PathBuf {
    runtime_dir(root).join("app.log")
}

/// Write the embedded sources when they are missing or from another version. A git checkout is left as is:
/// that is someone working on the code.
pub fn ensure_sources(root: &Path) -> anyhow::Result<()> {
    if root.join(".git").exists() {
        return Ok(());
    }
    let marker = runtime_dir(root).join("app-version");
    let current = fs::read_to_string(&marker).unwrap_or_default();
    if current.trim() == VERSION && root.join("app").join("main.py").exists() {
        return Ok(());
    }
    // Replace app/ as a whole, so files removed in this version do not linger. data/ is never touched.
    let app = root.join("app");
    if app.exists() {
        fs::remove_dir_all(&app).with_context(|| format!("nu pot înlocui {}", app.display()))?;
    }
    write_dir(&APP_DIR, &app)?;
    fs::write(root.join("pyproject.toml"), PYPROJECT)?;
    fs::write(root.join("uv.lock"), UV_LOCK)?;
    fs::create_dir_all(runtime_dir(root))?;
    fs::write(marker, VERSION)?;
    Ok(())
}

fn write_dir(dir: &Dir, to: &Path) -> anyhow::Result<()> {
    fs::create_dir_all(to)?;
    for entry in dir.entries() {
        let name = entry.path().file_name().unwrap_or_default();
        if name == "__pycache__" {
            continue; // compiled leftovers from a developer's tree, not sources
        }
        match entry {
            DirEntry::Dir(d) => write_dir(d, &to.join(name))?,
            DirEntry::File(f) => fs::write(to.join(name), f.contents())?,
        }
    }
    Ok(())
}

/// uv inside the folder, downloaded on the first run. It installs Python and the dependencies.
pub fn ensure_uv(root: &Path) -> anyhow::Result<PathBuf> {
    let dir = runtime_dir(root).join("uv");
    let exe = dir.join("uv.exe");
    if exe.exists() {
        return Ok(exe);
    }
    fs::create_dir_all(&dir)?;
    let mut bytes = Vec::new();
    ureq::get(UV_ZIP)
        .call()
        .context("nu pot descărca uv (verificați conexiunea la internet)")?
        .body_mut()
        .with_config()
        .limit(200 * 1024 * 1024)
        .reader()
        .read_to_end(&mut bytes)?;
    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(bytes)).context("arhiva uv nu poate fi citită")?;
    for i in 0..zip.len() {
        let mut f = zip.by_index(i)?;
        let Some(name) = f.enclosed_name().and_then(|p| p.file_name().map(|n| n.to_owned())) else { continue };
        if f.is_file() && name.to_string_lossy().ends_with(".exe") {
            let mut out = fs::File::create(dir.join(&name))?;
            std::io::copy(&mut f, &mut out)?;
            out.flush()?;
        }
    }
    anyhow::ensure!(exe.exists(), "arhiva uv nu conține uv.exe");
    Ok(exe)
}

/// Start the server hidden, as run.bat does, with its output in .runtime/app.log.
pub fn spawn_server(root: &Path, uv: &Path) -> anyhow::Result<Child> {
    let rt = runtime_dir(root);
    fs::create_dir_all(&rt)?;
    let _ = fs::remove_file(rt.join("app.port")); // a stale port would point at a server that is gone
    let log = log_path(root);
    if log.exists() {
        let _ = fs::rename(&log, rt.join("app.prev.log"));
    }
    let out = fs::File::create(&log)?;
    let child = Command::new(uv)
        .args(["run", "--quiet", "--no-dev", "--frozen", "--python", "3.12", "python", "-m", "app.main"])
        .current_dir(root)
        .env("UV_PYTHON_INSTALL_DIR", rt.join("python"))
        .env("UV_CACHE_DIR", rt.join("uv-cache"))
        .env("AVOCAT_LAUNCHER", "1")
        .env("AVOCAT_VERSION", VERSION)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(out.try_clone()?)
        .stderr(out)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .context("nu pot porni serverul")?;
    Ok(child)
}

pub fn read_port(root: &Path) -> Option<u16> {
    fs::read_to_string(runtime_dir(root).join("app.port")).ok()?.trim().parse().ok()
}

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(3)))
        .build()
        .into()
}

/// The server answers, and it is this app.
pub fn answers(port: u16) -> bool {
    agent()
        .get(&format!("http://127.0.0.1:{port}/api/identity"))
        .call()
        .ok()
        .and_then(|mut r| r.body_mut().read_json::<serde_json::Value>().ok())
        .is_some_and(|v| v["app"] == "moldova-ai-lawyer")
}

pub fn request_quit(port: u16) {
    let _ = agent().post(&format!("http://127.0.0.1:{port}/api/quit")).send_empty();
}
