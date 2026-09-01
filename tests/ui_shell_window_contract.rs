//! Window-contract + auto-refresh state-machine parity tests (Task 11.1).
//!
//! The menu-bar UI itself is SwiftUI/AppKit and cannot run under `cargo`, so the
//! behavior it must obey lives in the toolkit-agnostic core model
//! [`openkakao_cli::ui_shell`]. The live-ops spec grows the window set from five
//! to nine (기록·모델 설정·채팅방·자가 점검·대화 기억 + 대량 검증·기능 점검·자기개선·
//! 권한 설정), and every new window must reuse the exact same contract. These
//! tests exercise that model directly for **all nine** windows:
//!
//! 1. Re-opening a window focuses the existing one instead of building a second,
//!    for every one of the nine windows (focus-don't-recreate).
//! 2. The built menu has no refresh entry and lists exactly the nine
//!    window-opens plus quit — nothing more, nothing less.
//! 3. The auto-refresh cadence honors the 3-second change-event deadline and the
//!    60-second fallback poll.
//! 4. A failed open preserves the last shown screen and the prior windows, for
//!    every one of the nine windows, and yields a plain-language message.
//! 5. `AppWindow::all()` has exactly nine entries, each with a distinct,
//!    non-empty `menu_title` and a distinct, non-empty `key`.
//!
//! No Kakao database, network, or AX call is involved — the model is pure.
//!
//! Validates: Requirements R9.2, R9.12

use std::collections::BTreeSet;

use openkakao_cli::ui_shell::{
    build_menu, window_open_failure_message, AppWindow, AutoRefresh, MenuItemKind, OpenOutcome,
    UiError, WindowShell, EVENT_REFRESH_DEADLINE_MS, FALLBACK_REFRESH_INTERVAL_MS,
};

/// The exact set of windows the live-ops shell must expose (R9.2, R9.12).
const EXPECTED_WINDOW_COUNT: usize = 9;

/// R9.2/R9.12: `AppWindow::all()` is exactly the nine live-ops windows, and each
/// carries a distinct, non-empty menu title and a distinct, non-empty stable
/// key. This pins the window catalog the menu and onboarding both draw from.
#[test]
fn all_windows_have_distinct_nonempty_titles_and_keys() {
    let windows = AppWindow::all();
    assert_eq!(
        windows.len(),
        EXPECTED_WINDOW_COUNT,
        "the shell must expose exactly nine windows (R9.2/R9.12)"
    );

    // The nine windows are exactly the expected set, in the fixed menu order.
    assert_eq!(
        windows,
        [
            AppWindow::History,
            AppWindow::ModelSettings,
            AppWindow::ChatRooms,
            AppWindow::SelfCheck,
            AppWindow::Memory,
            AppWindow::Durability,
            AppWindow::Coverage,
            AppWindow::Improvement,
            AppWindow::Onboarding,
        ],
    );

    let mut titles: BTreeSet<&str> = BTreeSet::new();
    let mut keys: BTreeSet<&str> = BTreeSet::new();
    for w in windows {
        let title = w.menu_title();
        let key = w.key();
        assert!(!title.is_empty(), "{w:?} must have a non-empty menu title");
        assert!(!key.is_empty(), "{w:?} must have a non-empty key");
        assert!(titles.insert(title), "duplicate menu title for {w:?}: {title:?}");
        assert!(keys.insert(key), "duplicate key for {w:?}: {key:?}");
    }
    assert_eq!(titles.len(), EXPECTED_WINDOW_COUNT, "every title is distinct");
    assert_eq!(keys.len(), EXPECTED_WINDOW_COUNT, "every key is distinct");
}

/// R9.2/R9.12: clicking a window that is already open brings it to front and
/// never builds a second window, for every one of the nine feature windows.
#[test]
fn reopening_any_window_focuses_the_existing_one() {
    for w in AppWindow::all() {
        let mut shell = WindowShell::new();
        let mut builds = 0;

        let first = shell
            .open_with(w, || {
                builds += 1;
                Ok(())
            })
            .expect("first open succeeds");
        assert_eq!(first, OpenOutcome::Opened, "{w:?} first open");

        // Two more clicks — both must focus, never rebuild.
        for _ in 0..2 {
            let outcome = shell
                .open_with(w, || {
                    builds += 1;
                    Ok(())
                })
                .expect("reopen succeeds");
            assert_eq!(outcome, OpenOutcome::Focused, "{w:?} reopen focuses");
        }
        assert_eq!(builds, 1, "{w:?} must be built exactly once (R9.2)");
        assert_eq!(shell.open_count(), 1);
        assert_eq!(shell.front(), Some(w));
    }
}

/// R9.2: every one of the nine windows can be open at once, each opened exactly
/// once, and re-clicking any of them focuses without rebuilding.
#[test]
fn all_nine_windows_open_once_and_coexist() {
    let mut shell = WindowShell::new();
    let mut builds = 0;

    for w in AppWindow::all() {
        let outcome = shell
            .open_with(w, || {
                builds += 1;
                Ok(())
            })
            .expect("open succeeds");
        assert_eq!(outcome, OpenOutcome::Opened, "{w:?} opens fresh");
    }
    assert_eq!(builds, EXPECTED_WINDOW_COUNT, "each window built once");
    assert_eq!(shell.open_count(), EXPECTED_WINDOW_COUNT, "all nine coexist");

    // A second pass focuses every window without any further builds.
    for w in AppWindow::all() {
        let outcome = shell
            .open_with(w, || {
                builds += 1;
                Ok(())
            })
            .expect("reopen succeeds");
        assert_eq!(outcome, OpenOutcome::Focused, "{w:?} focuses");
        assert_eq!(shell.front(), Some(w), "{w:?} is frontmost after focus");
    }
    assert_eq!(builds, EXPECTED_WINDOW_COUNT, "no rebuilds on the second pass");
    assert_eq!(shell.open_count(), EXPECTED_WINDOW_COUNT);
}

/// R9.2: the menu has no refresh entry and lists exactly the nine window-opens
/// plus quit — no extra, missing, or disguised entries.
#[test]
fn menu_lists_exactly_nine_opens_and_quit_without_refresh() {
    let menu = build_menu();

    // No refresh entry, in any form.
    assert!(!menu.contains(&MenuItemKind::Refresh));

    // Exactly nine window-opens plus one quit — ten items total.
    assert_eq!(
        menu.len(),
        EXPECTED_WINDOW_COUNT + 1,
        "menu must be nine window-opens plus quit"
    );

    let mut opened: BTreeSet<AppWindow> = BTreeSet::new();
    let mut quits = 0;
    for item in &menu {
        match item {
            MenuItemKind::OpenWindow(w) => {
                assert!(opened.insert(*w), "duplicate open entry for {w:?}");
            }
            MenuItemKind::Quit => quits += 1,
            MenuItemKind::Refresh => panic!("refresh must not be present (R9.2)"),
        }
    }
    assert_eq!(quits, 1, "exactly one quit item");
    assert_eq!(
        opened.len(),
        EXPECTED_WINDOW_COUNT,
        "exactly one open entry per window"
    );
    // Every window from the catalog has a matching open entry.
    for w in AppWindow::all() {
        assert!(opened.contains(&w), "menu must open {w:?}");
    }
}

/// R9.2/R9.12: a change event makes a refresh due immediately and bounds it to
/// three seconds; with no events the fallback still fires within sixty seconds.
#[test]
fn auto_refresh_cadence_matches_requirements() {
    // Fallback path: no events, refresh must be due by 60s.
    let idle = AutoRefresh::new(0, ());
    assert!(!idle.should_refresh(59_999));
    assert!(idle.should_refresh(FALLBACK_REFRESH_INTERVAL_MS));
    assert!(FALLBACK_REFRESH_INTERVAL_MS <= 60_000);

    // Event path: a change makes refresh due at once, deadline <= 3s.
    let mut driven = AutoRefresh::new(0, ());
    driven.note_change(10_000);
    assert!(driven.should_refresh(10_000));
    assert_eq!(
        driven.must_refresh_by(),
        Some(10_000 + EVENT_REFRESH_DEADLINE_MS)
    );
    assert!(EVENT_REFRESH_DEADLINE_MS <= 3_000);
}

/// R9.12: when a refresh fails, the previously shown data stays on screen and a
/// failure indicator is raised; a later success clears it.
#[test]
fn failed_refresh_preserves_last_screen() {
    let mut ar = AutoRefresh::new(0, "이전 화면".to_string());
    ar.note_change(1_000);

    // The reload fails.
    ar.apply_failure(1_100);
    assert_eq!(ar.screen(), "이전 화면", "last screen preserved (R9.12)");
    assert!(ar.showing_failure(), "failure indicator shown (R9.12)");

    // The fallback clock still advances so another attempt is scheduled.
    assert_eq!(ar.next_fallback_at(), 1_100 + FALLBACK_REFRESH_INTERVAL_MS);

    // A later successful refresh swaps the screen and clears the flag.
    ar.note_change(5_000);
    ar.apply_success(5_050, "새 화면".to_string());
    assert_eq!(ar.screen(), "새 화면");
    assert!(!ar.showing_failure());
}

/// R9.2/R9.12: a failed window open leaves the previously open windows exactly
/// as they were and yields a plain-language message with a next action, for
/// every one of the nine windows.
#[test]
fn failed_open_keeps_prior_windows_and_explains_plainly() {
    for target in AppWindow::all() {
        let mut shell = WindowShell::new();

        // Open every *other* window first so there is real prior state to guard.
        let priors: Vec<AppWindow> = AppWindow::all()
            .into_iter()
            .filter(|w| *w != target)
            .collect();
        for w in &priors {
            shell.open_with(*w, || Ok(())).expect("prior window opens");
        }
        let last_front = shell.front();

        // Opening the target fails.
        let err = shell
            .open_with(target, || {
                Err(UiError::new(window_open_failure_message(target)))
            })
            .expect_err("target open fails");

        // The failed window is not open; every prior window is untouched.
        assert!(!shell.is_open(target), "{target:?} must not be open after failure");
        for w in &priors {
            assert!(shell.is_open(*w), "prior {w:?} preserved after {target:?} failure");
        }
        assert_eq!(shell.open_count(), priors.len(), "no prior window lost");
        assert_eq!(shell.front(), last_front, "frontmost window unchanged");

        // Plain language: names the window and gives a next action.
        assert!(
            err.message.contains(target.menu_title()),
            "message names {target:?}: {}",
            err.message
        );
        assert!(
            err.message.contains("다시 눌러"),
            "message gives a next action: {}",
            err.message
        );
        assert_eq!(shell.last_error(), Some(err.message.as_str()));
    }
}

/// R9.2/R9.12: the plain-language open-failure message is distinct and non-empty
/// for every window, so a beginner always sees which window failed.
#[test]
fn open_failure_message_is_specific_for_every_window() {
    let mut messages: BTreeSet<String> = BTreeSet::new();
    for w in AppWindow::all() {
        let msg = window_open_failure_message(w);
        assert!(msg.contains(w.menu_title()), "{w:?} message names the window");
        assert!(msg.contains("다시 눌러"), "{w:?} message gives a next action");
        assert!(messages.insert(msg), "{w:?} message must be distinct");
    }
    assert_eq!(messages.len(), EXPECTED_WINDOW_COUNT);
}
