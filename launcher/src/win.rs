//! Windows specifics: the job that ends every child with the launcher, single instance, shell, dialogs.
use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    CreateMutexW, GetCurrentProcess, OpenProcess, QueryFullProcessImageNameW, TerminateProcess,
    PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_ICONINFORMATION, MB_OK, SW_SHOWNORMAL};

fn wide(s: &str) -> Vec<u16> {
    OsStr::new(s).encode_wide().chain(std::iter::once(0)).collect()
}

/// Put the launcher in a job that kills all its processes when the job's last handle closes. Children
/// join it as they start (uv, Python, Ollama, the model runner), and the only handle is ours: when the
/// launcher exits, normally, by Quit, or killed from Task Manager, Windows ends them all and frees their
/// RAM and VRAM.
pub fn kill_children_on_exit() -> anyhow::Result<()> {
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        anyhow::ensure!(!job.is_null(), "CreateJobObject a eșuat ({})", GetLastError());
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(job, JobObjectExtendedLimitInformation,
            &info as *const _ as *const _, std::mem::size_of_val(&info) as u32);
        anyhow::ensure!(ok != 0, "SetInformationJobObject a eșuat ({})", GetLastError());
        let ok = AssignProcessToJobObject(job, GetCurrentProcess());
        anyhow::ensure!(ok != 0, "AssignProcessToJobObject a eșuat ({})", GetLastError());
        // The handle is deliberately never closed: the process exiting closes it.
    }
    Ok(())
}

/// True for the first instance in this folder. The mutex lives as long as the process.
pub fn single_instance(root: &Path) -> bool {
    // One name per folder, so copies of the app in two folders can run side by side.
    let key = root.to_string_lossy().to_lowercase();
    let hash = key.bytes().fold(0xcbf2_9ce4_8422_2325u64, |h, b| (h ^ b as u64).wrapping_mul(0x100_0000_01b3));
    let name = wide(&format!("Local\\AvocatAI-{hash:016x}"));
    unsafe {
        let h = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        !(h.is_null() || GetLastError() == ERROR_ALREADY_EXISTS)
    }
}

/// Open a URL, file or folder with its default program. AVOCAT_NO_BROWSER skips it, for automated tests.
pub fn open(target: &str) {
    if std::env::var_os("AVOCAT_NO_BROWSER").is_some() {
        return;
    }
    let (op, t) = (wide("open"), wide(target));
    unsafe {
        ShellExecuteW(std::ptr::null_mut(), op.as_ptr(), t.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL);
    }
}

pub fn message_box(title: &str, text: &str, error: bool) {
    let (t, x) = (wide(title), wide(text));
    let icon = if error { MB_ICONERROR } else { MB_ICONINFORMATION };
    unsafe {
        MessageBoxW(std::ptr::null_mut(), x.as_ptr(), t.as_ptr(), MB_OK | icon);
    }
}

/// End this folder's own Ollama and model runner, matched by executable path, never another app's.
pub fn end_engine_processes(root: &Path) {
    let own = root.join(".runtime").join("ollama").to_string_lossy().to_lowercase();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap.is_null() || snap == -1isize as HANDLE {
            return;
        }
        let mut e: PROCESSENTRY32W = std::mem::zeroed();
        e.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut more = Process32FirstW(snap, &mut e) != 0;
        while more {
            let h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, 0, e.th32ProcessID);
            if !h.is_null() {
                let mut buf = [0u16; 1024];
                let mut len = buf.len() as u32;
                if QueryFullProcessImageNameW(h, 0, buf.as_mut_ptr(), &mut len) != 0 {
                    let path = String::from_utf16_lossy(&buf[..len as usize]).to_lowercase();
                    if path.starts_with(&own) {
                        TerminateProcess(h, 0);
                    }
                }
                CloseHandle(h);
            }
            more = Process32NextW(snap, &mut e) != 0;
        }
        CloseHandle(snap);
    }
}
