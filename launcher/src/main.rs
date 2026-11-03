//! AvocatAI.exe: sets up and runs the local app, with a tray icon instead of a console window.
//!
//! The app itself stays a web page served on 127.0.0.1. This launcher:
//! 1. keeps a single instance per folder (a second start just opens the browser);
//! 2. puts itself in a Windows job that kills every process in it when the launcher exits, however it
//!    exits, so uv, Python, Ollama and its model runner never outlive it and keep RAM or VRAM;
//! 3. extracts the app's sources embedded in the exe next to it, unless the folder is a git checkout;
//! 4. downloads uv, which brings Python and the dependencies (the app then fetches Ollama and the model);
//! 5. starts the server hidden, logging to .runtime/app.log, and opens the browser once it answers;
//! 6. on Quit asks the server to stop, so it shuts the AI engine down cleanly, then exits;
//! 7. starts the server again when it exits asking for a restart (after a settings change).
#![windows_subsystem = "windows"]

mod setup;
mod win;

use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tao::event::{Event, StartCause};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
const TITLE: &str = "Avocat AI Moldova";
/// The server exits with this code to be started again, e.g. after the LAN setting changed (app/config.py).
const RESTART_EXIT_CODE: i32 = 3;

/// Messages from the worker thread and the tray to the event loop, which owns the tray icon.
#[derive(Debug)]
enum UserEvent {
    Status(String, bool), // text, server ready
    Tray(TrayIconEvent),
    Menu(MenuEvent),
    Exit,
}

/// What the worker thread and the menu handlers share.
struct App {
    root: PathBuf,
    child: Mutex<Option<Child>>,
    port: Mutex<Option<u16>>,
    quitting: std::sync::atomic::AtomicBool,
}

impl App {
    fn url(&self) -> Option<String> {
        self.port.lock().unwrap().map(|p| format!("http://127.0.0.1:{p}"))
    }
}

fn main() {
    let root = setup::root();
    if let Err(e) = run(root) {
        win::message_box(TITLE, &format!("Programul nu a putut porni:\n\n{e:#}"), true);
    }
}

fn run(root: PathBuf) -> anyhow::Result<()> {
    // A second start opens the running instance instead of starting another server.
    if !win::single_instance(&root) {
        if let Some(port) = setup::read_port(&root) {
            win::open(&format!("http://127.0.0.1:{port}"));
        }
        return Ok(());
    }
    win::kill_children_on_exit()?;

    let app = Arc::new(App {
        root,
        child: Mutex::new(None),
        port: Mutex::new(None),
        quitting: Default::default(),
    });
    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    {
        let p = proxy.clone();
        TrayIconEvent::set_event_handler(Some(move |e| {
            let _ = p.send_event(UserEvent::Tray(e));
        }));
        let p = proxy.clone();
        MenuEvent::set_event_handler(Some(move |e| {
            let _ = p.send_event(UserEvent::Menu(e));
        }));
    }

    let open_item = MenuItem::new("Deschide Avocat AI", false, None);
    let status_item = MenuItem::new("Pregătesc…", false, None);
    let folder_item = MenuItem::new("Deschide folderul programului", true, None);
    let log_item = MenuItem::new("Deschide jurnalul", true, None);
    let restart_item = MenuItem::new("Repornește", false, None);
    let quit_item = MenuItem::new("Ieșire (oprește și motorul AI)", true, None);
    let mut tray: Option<TrayIcon> = None;

    start_worker(app.clone(), proxy.clone(), true);

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::NewEvents(StartCause::Init) => {
                let menu = Menu::new();
                let _ = menu.append_items(&[
                    &open_item,
                    &status_item,
                    &PredefinedMenuItem::separator(),
                    &folder_item,
                    &log_item,
                    &restart_item,
                    &PredefinedMenuItem::separator(),
                    &quit_item,
                ]);
                tray = TrayIconBuilder::new()
                    .with_menu(Box::new(menu))
                    .with_menu_on_left_click(false)
                    .with_tooltip(format!("{TITLE} {VERSION}: pregătesc…"))
                    .with_icon(Icon::from_resource(1, None).expect("icon resource"))
                    .build()
                    .ok();
            }
            Event::UserEvent(UserEvent::Status(text, ready)) => {
                status_item.set_text(&text);
                open_item.set_enabled(ready);
                restart_item.set_enabled(!app.quitting.load(std::sync::atomic::Ordering::SeqCst));
                if let Some(t) = &tray {
                    let _ = t.set_tooltip(Some(format!("{TITLE} {VERSION}: {text}")));
                }
            }
            // A left click opens the app; the right click shows the menu.
            Event::UserEvent(UserEvent::Tray(TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            })) => {
                if let Some(url) = app.url() {
                    win::open(&url);
                }
            }
            Event::UserEvent(UserEvent::Menu(e)) => {
                if e.id == open_item.id() {
                    if let Some(url) = app.url() {
                        win::open(&url);
                    }
                } else if e.id == folder_item.id() {
                    win::open(&app.root.to_string_lossy());
                } else if e.id == log_item.id() {
                    win::open(&setup::log_path(&app.root).to_string_lossy());
                } else if e.id == restart_item.id() {
                    restart_item.set_enabled(false);
                    let (a, p) = (app.clone(), proxy.clone());
                    std::thread::spawn(move || {
                        stop_server(&a, &p);
                        start_worker(a, p, true);
                    });
                } else if e.id == quit_item.id() {
                    quit_item.set_enabled(false);
                    restart_item.set_enabled(false);
                    app.quitting.store(true, std::sync::atomic::Ordering::SeqCst);
                    let (a, p) = (app.clone(), proxy.clone());
                    std::thread::spawn(move || {
                        stop_server(&a, &p);
                        let _ = p.send_event(UserEvent::Exit);
                    });
                }
            }
            Event::UserEvent(UserEvent::Exit) => {
                // Remove the icon first, or Windows leaves a dead one in the tray until hovered.
                tray.take();
                *control_flow = ControlFlow::Exit;
            }
            _ => {}
        }
    });
}

fn status(proxy: &EventLoopProxy<UserEvent>, text: &str, ready: bool) {
    let _ = proxy.send_event(UserEvent::Status(text.to_string(), ready));
}

/// Set up and start the server on a worker thread, then watch it. `open_browser` is false when the server
/// restarted itself, because its page is already open and reloads on its own.
fn start_worker(app: Arc<App>, proxy: EventLoopProxy<UserEvent>, open_browser: bool) {
    std::thread::spawn(move || {
        if let Err(e) = start_server(&app, &proxy, open_browser) {
            status(&proxy, "Eroare la pornire, vedeți jurnalul", false);
            if !app.quitting.load(std::sync::atomic::Ordering::SeqCst) {
                win::message_box(TITLE, &format!("Programul nu a putut porni:\n\n{e:#}\n\nJurnal: {}",
                    setup::log_path(&app.root).display()), true);
            }
            return;
        }
        // Watch for an unexpected exit, so the tray says so instead of pointing at a dead page.
        loop {
            std::thread::sleep(Duration::from_secs(2));
            let mut guard = app.child.lock().unwrap();
            let Some(child) = guard.as_mut() else { return };
            if let Ok(Some(code)) = child.try_wait() {
                *guard = None;
                drop(guard);
                *app.port.lock().unwrap() = None;
                if app.quitting.load(std::sync::atomic::Ordering::SeqCst) {
                    return;
                }
                if code.code() == Some(RESTART_EXIT_CODE) {
                    status(&proxy, "Repornesc…", false);
                    start_worker(app.clone(), proxy.clone(), false);
                } else {
                    status(&proxy, &format!("S-a oprit (cod {:?}); folosiți Repornește", code.code()), false);
                }
                return;
            }
        }
    });
}

fn start_server(app: &App, proxy: &EventLoopProxy<UserEvent>, open_browser: bool) -> anyhow::Result<()> {
    let root = &app.root;
    status(proxy, "Pregătesc fișierele programului…", false);
    setup::ensure_sources(root)?;
    status(proxy, "Descarc componenta de instalare (prima pornire)…", false);
    let uv = setup::ensure_uv(root)?;
    status(proxy, "Pornesc (prima pornire descarcă Python, câteva minute)…", false);
    let child = setup::spawn_server(root, &uv)?;
    *app.child.lock().unwrap() = Some(child);

    // Python, the dependencies and the index can take minutes on the first run.
    let started = Instant::now();
    loop {
        if app.quitting.load(std::sync::atomic::Ordering::SeqCst) {
            return Ok(());
        }
        if let Some(port) = setup::read_port(root) {
            if setup::answers(port) {
                *app.port.lock().unwrap() = Some(port);
                status(proxy, &format!("Rulează pe http://127.0.0.1:{port}"), true);
                if open_browser {
                    win::open(&format!("http://127.0.0.1:{port}"));
                }
                return Ok(());
            }
        }
        if let Some(child) = app.child.lock().unwrap().as_mut() {
            if let Ok(Some(code)) = child.try_wait() {
                anyhow::bail!("serverul s-a oprit la pornire (cod {:?})", code.code());
            }
        }
        if started.elapsed() > Duration::from_secs(30 * 60) {
            anyhow::bail!("serverul nu a răspuns în 30 de minute");
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

/// Ask the server to stop, so its shutdown stops the AI engine and frees RAM and VRAM; if it does not
/// stop in time, end it. Anything still running in the job ends when the launcher exits.
fn stop_server(app: &App, proxy: &EventLoopProxy<UserEvent>) {
    status(proxy, "Opresc programul și motorul AI…", false);
    if let Some(port) = *app.port.lock().unwrap() {
        setup::request_quit(port);
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut guard = app.child.lock().unwrap();
    if let Some(child) = guard.as_mut() {
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) {
                break;
            }
            std::thread::sleep(Duration::from_millis(250));
        }
        if !matches!(child.try_wait(), Ok(Some(_))) {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    *guard = None;
    *app.port.lock().unwrap() = None;
    // uv ends with Python, but the engine started under it may need a moment more; the job covers it at exit.
    win::end_engine_processes(&app.root);
}

pub fn runtime_dir(root: &Path) -> PathBuf {
    root.join(".runtime")
}
