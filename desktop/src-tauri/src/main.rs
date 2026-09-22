mod python_bridge;
mod resource_layout;

use python_bridge::{PythonBridge, SafeBrowserToolResult, SafeRuntimeSnapshot};
use serde_json::Value;
use tauri::image::Image;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, PhysicalPosition, WindowEvent};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const PANEL_WIDTH: f64 = 276.0;
const VISIBILITY_EVENT: &str = "jarvis://visibility";

/// Payload for the window visibility event: one boolean, never content.
fn visibility_payload(visible: bool) -> Value {
    serde_json::json!({ "visible": visible })
}

fn announce_visibility(app: &tauri::AppHandle, label: &str, visible: bool) {
    let _ = app.emit_to(
        tauri::EventTarget::labeled(label),
        VISIBILITY_EVENT,
        visibility_payload(visible),
    );
}

/// Show or hide a panel window and tell its webview what just happened.
///
/// The frontend has to stop the 3D render loop the moment the window goes
/// away. Making that a guess from `document.visibilityState` and `blur` leaves
/// the loop running whenever AppKit orders the window out without a matching
/// DOM event, so the shell states the new state after the OS call returned.
/// A failed show/hide is not announced: the webview keeps what it already has
/// (2026-09-22).
fn set_window_visible(window: &tauri::WebviewWindow, visible: bool) {
    let applied = if visible {
        window.show()
    } else {
        window.hide()
    };
    if applied.is_err() {
        return;
    }
    if visible {
        let _ = window.set_focus();
    }
    announce_visibility(window.app_handle(), window.label(), visible);
}

/// Report the calling window's current visibility.
///
/// The visibility event is a change notification, so a webview that boots
/// while the window is hidden has no way to learn that from the event stream
/// alone. The frontend asks once during wiring and keeps the render lifecycle
/// hidden until this answers (2026-09-22).
#[tauri::command]
fn window_is_visible(window: tauri::WebviewWindow) -> bool {
    window.is_visible().unwrap_or(false)
}

#[tauri::command]
async fn fetch_runtime_snapshot(
    bridge: tauri::State<'_, PythonBridge>,
    token_id: Option<String>,
) -> Result<SafeRuntimeSnapshot, String> {
    let bridge = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || bridge.fetch_snapshot(token_id.as_deref()))
        .await
        .map_err(|_| "snapshot_worker_failed".to_string())?
        .map_err(|error| error.to_string())
}

// These parameters mirror the invoke payload keys one for one; grouping or
// renaming them would change the Tauri command contract the frontend calls.
#[allow(clippy::too_many_arguments)]
#[tauri::command]
async fn fetch_settings_action(
    bridge: tauri::State<'_, PythonBridge>,
    action: String,
    query: Option<String>,
    node_id: Option<String>,
    chat_id: Option<String>,
    model: Option<String>,
    explicit_opt_in: Option<bool>,
    token_id: Option<String>,
) -> Result<Value, String> {
    let bridge = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        bridge.fetch_settings_action(
            &action,
            query.as_deref(),
            node_id.as_deref(),
            chat_id.as_deref(),
            model.as_deref(),
            explicit_opt_in,
            token_id.as_deref(),
        )
    })
    .await
    .map_err(|_| "settings_worker_failed".to_string())?
    .map_err(|error| error.to_string())
}

#[tauri::command]
async fn run_browser_tool(
    bridge: tauri::State<'_, PythonBridge>,
    job_id: String,
    task: String,
    token_id: Option<String>,
) -> Result<SafeBrowserToolResult, String> {
    let bridge = bridge.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        bridge.run_browser_tool(&job_id, &task, token_id.as_deref())
    })
    .await
    .map_err(|_| "browser_tool_worker_failed".to_string())?
    .map_err(|error| error.to_string())
}

#[tauri::command]
fn cancel_python(bridge: tauri::State<'_, PythonBridge>, token_id: String) -> bool {
    bridge.cancel(&token_id)
}

#[tauri::command]
fn cancel_model_swap(bridge: tauri::State<'_, PythonBridge>, token_id: String) -> bool {
    bridge.cancel_model_swap(&token_id)
}

#[tauri::command]
fn start_voice_session(bridge: tauri::State<'_, PythonBridge>) -> Result<(), String> {
    bridge
        .start_voice_session()
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("settings")
        .ok_or_else(|| "settings_window_missing".to_string())?;
    window
        .show()
        .map_err(|_| "settings_show_failed".to_string())?;
    // The window is on screen now, so the renderer may resume even if focusing
    // it fails; announcing only after `set_focus` left a visible settings
    // window frozen whenever focus was refused (2026-09-22).
    announce_visibility(&app, window.label(), true);
    window
        .set_focus()
        .map_err(|_| "settings_focus_failed".to_string())
}

fn make_tray_icon() -> Image<'static> {
    const SIZE: u32 = 18;
    let mut rgba = vec![0_u8; (SIZE * SIZE * 4) as usize];
    let center = (SIZE as f64 - 1.0) / 2.0;
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as f64 - center;
            let dy = y as f64 - center;
            let radius = (dx * dx + dy * dy).sqrt();
            let ring = (5.6..=7.1).contains(&radius);
            let nucleus = radius <= 2.1;
            if ring || nucleus {
                let index = ((y * SIZE + x) * 4) as usize;
                rgba[index] = 0;
                rgba[index + 1] = 0;
                rgba[index + 2] = 0;
                rgba[index + 3] = if nucleus { 220 } else { 190 };
            }
        }
    }
    Image::new_owned(rgba, SIZE, SIZE)
}

fn toggle_panel(app: &tauri::AppHandle, position: PhysicalPosition<f64>) {
    let Some(window) = app.get_webview_window("jarvis") else {
        return;
    };
    if window.is_visible().unwrap_or(false) {
        set_window_visible(&window, false);
        return;
    }
    let scale = window.scale_factor().unwrap_or(1.0);
    let x = (position.x - (PANEL_WIDTH * scale / 2.0)).round() as i32;
    let y = (position.y + 12.0 * scale).round() as i32;
    let _ = window.set_position(PhysicalPosition::new(x, y));
    set_window_visible(&window, true);
}

fn ignore_terminal_hangup() {
    // LaunchAgent and open(1) already reparent to launchd. Ignore SIGHUP so a
    // terminal or nohup handoff cannot take down the menubar process.
    unsafe {
        libc::signal(libc::SIGHUP, libc::SIG_IGN);
    }
}

fn main() {
    ignore_terminal_hangup();
    let abort_shortcut = Shortcut::new(Some(Modifiers::SUPER | Modifiers::ALT), Code::Escape);
    tauri::Builder::default()
        .manage(PythonBridge::new())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    let expected =
                        Shortcut::new(Some(Modifiers::SUPER | Modifiers::ALT), Code::Escape);
                    if shortcut == &expected && event.state() == ShortcutState::Pressed {
                        let bridge = app.state::<PythonBridge>();
                        let _ = bridge.global_abort();
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            fetch_runtime_snapshot,
            fetch_settings_action,
            run_browser_tool,
            cancel_python,
            cancel_model_swap,
            open_settings,
            start_voice_session,
            window_is_visible
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            handle
                .global_shortcut()
                .register(abort_shortcut)
                .map_err(Box::<dyn std::error::Error>::from)?;
            TrayIconBuilder::new()
                .icon(make_tray_icon())
                .icon_as_template(true)
                .tooltip("OpenKakao Jarvis")
                .on_tray_icon_event(|tray, event| {
                    match event {
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            position,
                            ..
                        } => toggle_panel(tray.app_handle(), position),
                        TrayIconEvent::Click {
                            button: MouseButton::Right,
                            button_state: MouseButtonState::Up,
                            ..
                        } => {
                            let _ = open_settings(tray.app_handle().clone());
                        }
                        _ => {}
                    }
                })
                .build(&handle)?;
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // Only a hide the OS actually applied may pause the renderer:
            // announcing hidden for a window that is still on screen would
            // freeze the core while the user is looking at it.
            WindowEvent::Focused(false) if window.label() == "jarvis" && window.hide().is_ok() => {
                announce_visibility(window.app_handle(), window.label(), false);
            }
            WindowEvent::CloseRequested { api, .. }
                if window.label() == "jarvis" || window.label() == "settings" =>
            {
                api.prevent_close();
                if window.hide().is_ok() {
                    announce_visibility(window.app_handle(), window.label(), false);
                }
            }
            // Any focus of an already visible window re-states the visible
            // state. A tray click that lands while the webview is still
            // registering its listener would otherwise be the only announce of
            // that show, and it would be lost.
            WindowEvent::Focused(true) if window.is_visible().unwrap_or(false) => {
                announce_visibility(window.app_handle(), window.label(), true);
            }
            _ => {}
        })
        .run(tauri::generate_context!())
        .expect("error while running OpenKakao Jarvis");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_visibility_payload_carries_only_the_boolean() {
        assert_eq!(visibility_payload(true).to_string(), r#"{"visible":true}"#);
        assert_eq!(
            visibility_payload(false).to_string(),
            r#"{"visible":false}"#
        );
        assert_eq!(
            visibility_payload(true)
                .as_object()
                .map(|object| object.len()),
            Some(1)
        );
    }

    #[test]
    fn the_frontend_subscribes_to_the_same_event_name() {
        // The pause-on-hide contract is one string shared across two languages.
        // If the two sides drift apart, the panel silently keeps rendering
        // behind a hidden window, which is exactly the waste this signal stops.
        let wiring = include_str!("../../src/core/lifecycle-wiring.ts");
        assert!(
            wiring.contains(VISIBILITY_EVENT),
            "the frontend event name drifted from VISIBILITY_EVENT"
        );
    }

    #[test]
    fn the_tray_icon_is_an_rgba_square() {
        let icon = make_tray_icon();
        assert_eq!(icon.rgba().len(), 18 * 18 * 4);
    }

    #[test]
    fn the_frontend_asks_for_the_boot_handshake_command() {
        // The event stream carries changes only, so the webview has to be able
        // to ask for the current visibility by name. Two languages, one string.
        let wiring = include_str!("../../src/core/lifecycle-wiring.ts");
        assert!(
            wiring.contains("\"window_is_visible\""),
            "the frontend no longer invokes the boot handshake command"
        );
        assert!(
            wiring.contains(VISIBILITY_EVENT),
            "the frontend event name drifted from VISIBILITY_EVENT"
        );
    }
}
