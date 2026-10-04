//! Native display-sleep and session-switch signals. These public notifications
//! do not, by themselves, attest a physical screen-lock transition.
use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::{AnyObject, ProtocolObject};
use objc2::MainThreadMarker;
use objc2_app_kit::{
    NSWorkspace, NSWorkspaceScreensDidSleepNotification,
    NSWorkspaceSessionDidResignActiveNotification,
};
use objc2_foundation::{
    NSNotification, NSNotificationCenter, NSObjectProtocol, NSOperationQueue, NSString,
};
use std::ptr::NonNull;
use std::sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc,
};
use tauri::Manager;

#[derive(Clone, Copy, Debug)]
pub enum WorkspaceNote {
    DisplaySleep,
    SessionInactive,
}
impl WorkspaceNote {
    pub const ALL: [Self; 2] = [Self::DisplaySleep, Self::SessionInactive];
    pub fn code(self) -> &'static str {
        match self {
            Self::DisplaySleep => "display-sleep",
            Self::SessionInactive => "session-inactive",
        }
    }
    fn name(self) -> &'static NSString {
        // Framework-owned constants, not arbitrary notification names.
        unsafe {
            match self {
                Self::DisplaySleep => NSWorkspaceScreensDidSleepNotification,
                Self::SessionInactive => NSWorkspaceSessionDidResignActiveNotification,
            }
        }
    }
}

#[derive(Clone, Default)]
pub struct WorkspaceCounters {
    display_sleep: Arc<AtomicU64>,
    session_inactive: Arc<AtomicU64>,
}
impl WorkspaceCounters {
    fn counter(&self, note: WorkspaceNote) -> &AtomicU64 {
        match note {
            WorkspaceNote::DisplaySleep => &self.display_sleep,
            WorkspaceNote::SessionInactive => &self.session_inactive,
        }
    }
    pub fn count(&self, note: WorkspaceNote) -> u64 {
        self.counter(note).load(Ordering::Acquire)
    }
}

/// Owned on the app's main thread for the whole event loop, independent of
/// window open/close cycles. No unsafe Send/Sync or leaked observer tokens.
pub struct WorkspaceObservers {
    center: Retained<NSNotificationCenter>,
    tokens: Vec<Retained<ProtocolObject<dyn NSObjectProtocol>>>,
    active: Arc<AtomicBool>,
    _main_thread: Option<MainThreadMarker>,
}
impl WorkspaceObservers {
    fn register(
        center: Retained<NSNotificationCenter>,
        object: Option<&AnyObject>,
        queue: Option<&NSOperationQueue>,
        main_thread: Option<MainThreadMarker>,
        callback: impl Fn(WorkspaceNote) + Clone + Send + Sync + 'static,
    ) -> Self {
        let active = Arc::new(AtomicBool::new(true));
        let mut tokens = Vec::with_capacity(2);
        for note in WorkspaceNote::ALL {
            let live = Arc::clone(&active);
            let callback = callback.clone();
            let block = RcBlock::new(move |_: NonNull<NSNotification>| {
                if live.load(Ordering::Acquire) {
                    callback(note);
                }
            });
            // The copied block captures only sendable Rust values. Production
            // delivery uses mainQueue; object and token come from this center.
            tokens.push(unsafe {
                center.addObserverForName_object_queue_usingBlock(
                    Some(note.name()),
                    object,
                    queue,
                    &block,
                )
            });
        }
        Self {
            center,
            tokens,
            active,
            _main_thread: main_thread,
        }
    }
}
impl Drop for WorkspaceObservers {
    fn drop(&mut self) {
        // Also fences callbacks that mainQueue already queued before removal.
        self.active.store(false, Ordering::Release);
        for token in &self.tokens {
            unsafe {
                self.center.removeObserver((**token).as_ref());
            }
        }
    }
}

pub fn install(app: &tauri::AppHandle) -> Result<WorkspaceObservers, String> {
    let marker = MainThreadMarker::new().ok_or("workspace observers require the main thread")?;
    let counters = WorkspaceCounters::default();
    if !app.manage(counters.clone()) {
        return Err("workspace observers already installed".into());
    }
    let workspace = NSWorkspace::sharedWorkspace();
    let app = app.clone();
    Ok(WorkspaceObservers::register(
        workspace.notificationCenter(),
        Some(&workspace),
        Some(&NSOperationQueue::mainQueue()),
        Some(marker),
        move |note| {
            // The queue contract is checked before touching any owned native window.
            if MainThreadMarker::new().is_none() {
                return;
            }
            for label in ["alden", "settings"] {
                if let Some(window) = app.get_webview_window(label) {
                    if window.hide().is_ok() && window.is_visible().ok() == Some(false) {
                        super::announce_visibility(&app, label, false);
                    }
                }
            }
            counters.counter(note).fetch_add(1, Ordering::Release);
            // Remain hidden until an ordinary explicit reopen. This never stops
            // voice, inference, tools, workers or queues, and never takes focus.
        },
    ))
}

/// Only the fixed isolated-audit path calls this on the main thread. This posts
/// to this process's NSWorkspace center, not the distributed/OS event stream.
pub fn post_process_local_audit_note(note: WorkspaceNote) -> Result<(), String> {
    MainThreadMarker::new().ok_or("audit notification requires the main thread")?;
    let workspace = NSWorkspace::sharedWorkspace();
    unsafe {
        workspace
            .notificationCenter()
            .postNotificationName_object(note.name(), Some(&workspace));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_center_observes_exact_notes_and_unregisters_on_drop() {
        // Private Foundation center: never touches OS session/display state.
        let center = NSNotificationCenter::new();
        let counters = WorkspaceCounters::default();
        let observed = counters.clone();
        let guard = WorkspaceObservers::register(center.clone(), None, None, None, move |note| {
            observed.counter(note).fetch_add(1, Ordering::Release);
        });
        assert_eq!(guard.tokens.len(), 2);
        unsafe {
            center.postNotificationName_object(&NSString::from_str("unrelated"), None);
        }
        for note in WorkspaceNote::ALL {
            unsafe {
                center.postNotificationName_object(note.name(), None);
            }
            assert_eq!(counters.count(note), 1);
        }
        drop(guard);
        for note in WorkspaceNote::ALL {
            unsafe {
                center.postNotificationName_object(note.name(), None);
            }
            assert_eq!(counters.count(note), 1);
        }
    }
}
