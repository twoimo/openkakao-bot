//! Link forwarding, and the **shared** image-acquisition ladder (parent
//! task 7).
//!
//! This module delivers two things. First, the [`ImageLadder`] — the ordered,
//! three-rung image-acquisition strategy shared by both the link forwarder
//! (R6.4 Threads images) and the Telegram relay (R7.5), so both features rest on
//! one image-count-parity guarantee (R12.6). Second, the [`LinkForwarder`] (task
//! 7.1): the composer-driven feature that collects a link's content and
//! forwards a redacted summary to the resolved KakaoTalk rooms (R6).
//!
//! Several safety properties of the forwarder hold structurally:
//!
//! * **All-or-nothing mention resolution (R6.2).** [`LinkForwarder::forward`]
//!   resolves *every* room mention first; if any one fails, the whole request
//!   is rejected and **zero** sends happen to **any** room.
//! * **Image-count mismatch is a partial failure that stays retryable (R6.6).**
//!   When the source and delivered image counts disagree, the (room, link)
//!   combination is **not** recorded in the [`DeliveryLedger`], so the request
//!   can be retried. Only a fully-delivered combination is marked (R6.14).
//! * **Once per (room, link) (R6.7).** The [`DeliveryLedger`] keys on the
//!   [`canonical_link_key`] (case- and trailing-slash-insensitive), so an
//!   already-delivered combination is refused with a plain-language "already
//!   sent" and zero new sends.
//! * **The journal stays redacted (R6.11).** Only the stage, result code,
//!   elapsed time, and the two image counts are written — never the collected
//!   body, the summary, the URL, a path, or an account identifier.
//!
//! The ladder embodies decision 2 of the design ("이미지 원본 확보 3단 경로를
//! 순서 있는 사다리로 추상화"):
//!
//! * **Order, and only-on-failure advance (R7.5).** Rungs are tried in order —
//!   original-save → cache-folder → screen-capture — and the next rung is tried
//!   **only** when the earlier one failed. As soon as a rung succeeds, the
//!   remaining rungs are not called at all.
//! * **Two-second bound per step (R7.5).** Success is "a single non-empty image
//!   within two seconds". The bound is measured on the injected [`Clock`], so a
//!   fake acquirer can simulate a slow rung by advancing the clock without any
//!   real sleep; a rung that answers late is [`StepAttempt::TimedOut`], a
//!   failure that falls through to the next rung.
//! * **Permission is coupled in (R9.10).** When the screen-recording permission
//!   is absent, the screen-capture rung is **not attempted** — it becomes
//!   [`StepAttempt::PermissionMissing`] rather than being called.
//! * **The outcome is data.** [`LadderOutcome`] records what each rung did and
//!   whether the acquired image came from screen capture
//!   ([`LadderOutcome::used_screen_capture`]), which is exactly what the relay
//!   needs to warn about lower quality and the permission requirement (R7.6).

use crate::ports::{AcquireError, Clock, ImageAcquisition, ImageAcquirer, ImageBlob, ImageRef};

/// The per-rung time bound: a rung must return a non-empty image within this
/// many milliseconds to count as a success (R7.5).
pub const STEP_TIMEOUT_MS: u64 = 2_000;

/// The number of rungs in the image ladder (R7.5).
pub const LADDER_RUNGS: usize = 3;

/// What one rung of the ladder did.
///
/// The variants distinguish the reasons a rung did not yield a usable image, so
/// the outcome carries enough detail to explain a miss without holding any raw
/// image bytes or paths.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepAttempt {
    /// The rung returned a non-empty image within the time bound.
    Acquired,
    /// The rung returned, but the image was empty (treated as a failure, R7.6).
    Empty,
    /// The rung did not answer within the two-second bound (R7.5).
    TimedOut,
    /// The rung needs an OS permission that is not granted, so it was **not**
    /// attempted (the screen-capture rung without screen recording, R9.10).
    PermissionMissing,
    /// The rung failed with a transport/availability error.
    Error(&'static str),
    /// The rung was not reached because an earlier rung already succeeded.
    Skipped,
}

/// The result of running the ladder for one image reference (R7.5).
///
/// `acquired` is `Some` exactly when one rung produced a non-empty image within
/// the bound; `attempts` records what each of the three rungs did, in rung
/// order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LadderOutcome {
    /// The acquired image, if any rung succeeded.
    pub acquired: Option<ImageBlob>,
    /// What each rung did, in order (original-save, cache-folder, screen-capture).
    pub attempts: [StepAttempt; LADDER_RUNGS],
    /// Which acquisition kind produced [`Self::acquired`], if any.
    acquired_via: Option<ImageAcquisition>,
}

impl LadderOutcome {
    /// Whether the acquired image came from the screen-capture rung, which the
    /// relay uses to warn about lower quality and the permission requirement
    /// (R7.6).
    pub fn used_screen_capture(&self) -> bool {
        matches!(self.acquired_via, Some(ImageAcquisition::ScreenCapture))
    }

    /// Whether an image was acquired at all.
    pub fn is_acquired(&self) -> bool {
        self.acquired.is_some()
    }

    /// The acquisition kind that produced the image, if any.
    pub fn acquired_via(&self) -> Option<ImageAcquisition> {
        self.acquired_via
    }
}

/// The ordered three-rung image-acquisition strategy (R7.5).
///
/// `steps` are tried in array order; the conventional order is original-save,
/// cache-folder, screen-capture, but the ladder only special-cases a rung whose
/// [`ImageAcquirer::kind`] is [`ImageAcquisition::ScreenCapture`] for the
/// permission gate — the ordering itself is whatever the caller wired.
pub struct ImageLadder<'a> {
    /// The three rungs, tried in order.
    pub steps: [&'a dyn ImageAcquirer; LADDER_RUNGS],
    /// The logical clock used to bound each rung to two seconds.
    pub clock: &'a dyn Clock,
}

impl ImageLadder<'_> {
    /// Try each rung in order, stopping at the first non-empty image acquired
    /// within the two-second bound (R7.5).
    ///
    /// `screen_recording_granted` gates the screen-capture rung: when it is
    /// false, any rung whose kind is [`ImageAcquisition::ScreenCapture`] is not
    /// attempted and is recorded as [`StepAttempt::PermissionMissing`] (R9.10).
    /// Rungs after a success are recorded as [`StepAttempt::Skipped`] and are
    /// never called.
    pub fn acquire(&self, r: &ImageRef, screen_recording_granted: bool) -> LadderOutcome {
        let mut attempts = [StepAttempt::Skipped; LADDER_RUNGS];

        for (idx, step) in self.steps.iter().enumerate() {
            // The screen-capture rung needs the screen-recording permission; if
            // it is missing, the rung is not attempted at all (R9.10).
            if step.kind() == ImageAcquisition::ScreenCapture && !screen_recording_granted {
                attempts[idx] = StepAttempt::PermissionMissing;
                continue;
            }

            // Measure the rung against the two-second bound on the injected
            // clock. A fake acquirer advances the clock to simulate work, so no
            // real time is spent (R7.5).
            let start = self.clock.now_ms();
            let result = step.acquire(r);
            let elapsed = self.clock.now_ms().saturating_sub(start);

            if elapsed > STEP_TIMEOUT_MS as i64 {
                // A late answer is a failure regardless of what it returned.
                attempts[idx] = StepAttempt::TimedOut;
                continue;
            }

            match result {
                Ok(blob) if !blob.bytes.is_empty() => {
                    attempts[idx] = StepAttempt::Acquired;
                    // Remaining rungs are not tried once we have an image.
                    return LadderOutcome {
                        acquired: Some(blob),
                        attempts,
                        acquired_via: Some(step.kind()),
                    };
                }
                // A returned-but-empty image is a failure (R7.6).
                Ok(_) | Err(AcquireError::Empty) => attempts[idx] = StepAttempt::Empty,
                Err(AcquireError::PermissionDenied) => {
                    attempts[idx] = StepAttempt::PermissionMissing
                }
                Err(AcquireError::NotAvailable) => attempts[idx] = StepAttempt::Error("not_available"),
                Err(AcquireError::Failed(_)) => attempts[idx] = StepAttempt::Error("failed"),
            }
        }

        LadderOutcome {
            acquired: None,
            attempts,
            acquired_via: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fakes::VirtualClock;
    use std::cell::Cell;

    /// A scripted rung: a kind, an outcome, and how long it takes on the shared
    /// virtual clock. It advances the clock by `takes_ms` on each call so the
    /// ladder's two-second bound is exercised without any real sleep.
    struct FakeAcquirer {
        kind: ImageAcquisition,
        clock: VirtualClock,
        takes_ms: i64,
        outcome: Outcome,
        calls: Cell<usize>,
    }

    #[derive(Clone)]
    enum Outcome {
        Bytes(Vec<u8>),
        Empty,
        Err(AcquireError),
    }

    impl FakeAcquirer {
        fn new(kind: ImageAcquisition, clock: VirtualClock, takes_ms: i64, outcome: Outcome) -> Self {
            Self {
                kind,
                clock,
                takes_ms,
                outcome,
                calls: Cell::new(0),
            }
        }

        fn ok(kind: ImageAcquisition, clock: VirtualClock) -> Self {
            Self::new(kind, clock, 100, Outcome::Bytes(vec![1, 2, 3]))
        }

        fn calls(&self) -> usize {
            self.calls.get()
        }
    }

    impl ImageAcquirer for FakeAcquirer {
        fn kind(&self) -> ImageAcquisition {
            self.kind
        }

        fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
            self.calls.set(self.calls.get() + 1);
            self.clock.advance_ms(self.takes_ms);
            match &self.outcome {
                Outcome::Bytes(b) => Ok(ImageBlob {
                    bytes: b.clone(),
                    mime: "image/png".into(),
                }),
                Outcome::Empty => Ok(ImageBlob {
                    bytes: Vec::new(),
                    mime: "image/png".into(),
                }),
                Outcome::Err(e) => Err(e.clone()),
            }
        }
    }

    fn img_ref() -> ImageRef {
        ImageRef {
            locator: "loc-1".into(),
        }
    }

    #[test]
    fn first_rung_success_skips_the_rest() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::ok(ImageAcquisition::OriginalSave, clock.clone());
        let s1 = FakeAcquirer::ok(ImageAcquisition::CacheFolder, clock.clone());
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert!(out.is_acquired());
        assert_eq!(out.attempts[0], StepAttempt::Acquired);
        assert_eq!(out.attempts[1], StepAttempt::Skipped);
        assert_eq!(out.attempts[2], StepAttempt::Skipped);
        assert!(!out.used_screen_capture());
        // The later rungs were never called.
        assert_eq!(s1.calls(), 0);
        assert_eq!(s2.calls(), 0);
    }

    #[test]
    fn falls_through_to_next_rung_only_on_failure() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            50,
            Outcome::Err(AcquireError::NotAvailable),
        );
        let s1 = FakeAcquirer::ok(ImageAcquisition::CacheFolder, clock.clone());
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert!(out.is_acquired());
        assert_eq!(out.attempts[0], StepAttempt::Error("not_available"));
        assert_eq!(out.attempts[1], StepAttempt::Acquired);
        assert_eq!(out.attempts[2], StepAttempt::Skipped);
        assert_eq!(s0.calls(), 1);
        assert_eq!(s1.calls(), 1);
        assert_eq!(s2.calls(), 0);
    }

    #[test]
    fn empty_image_is_a_failure_that_advances() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            10,
            Outcome::Empty,
        );
        let s1 = FakeAcquirer::ok(ImageAcquisition::CacheFolder, clock.clone());
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert_eq!(out.attempts[0], StepAttempt::Empty);
        assert_eq!(out.attempts[1], StepAttempt::Acquired);
    }

    #[test]
    fn slow_rung_times_out_and_advances() {
        let clock = VirtualClock::new(0);
        // The first rung answers after 2001ms — past the 2s bound.
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            (STEP_TIMEOUT_MS as i64) + 1,
            Outcome::Bytes(vec![9]),
        );
        let s1 = FakeAcquirer::ok(ImageAcquisition::CacheFolder, clock.clone());
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert_eq!(out.attempts[0], StepAttempt::TimedOut);
        assert_eq!(out.attempts[1], StepAttempt::Acquired);
    }

    #[test]
    fn exactly_two_seconds_still_counts_as_in_time() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            STEP_TIMEOUT_MS as i64,
            Outcome::Bytes(vec![9]),
        );
        let s1 = FakeAcquirer::ok(ImageAcquisition::CacheFolder, clock.clone());
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert_eq!(out.attempts[0], StepAttempt::Acquired);
    }

    #[test]
    fn screen_capture_not_attempted_without_permission() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            10,
            Outcome::Err(AcquireError::NotAvailable),
        );
        let s1 = FakeAcquirer::new(
            ImageAcquisition::CacheFolder,
            clock.clone(),
            10,
            Outcome::Err(AcquireError::NotAvailable),
        );
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), false);
        assert!(!out.is_acquired());
        assert_eq!(out.attempts[2], StepAttempt::PermissionMissing);
        // The screen-capture rung was never called (R9.10).
        assert_eq!(s2.calls(), 0);
    }

    #[test]
    fn screen_capture_used_when_earlier_rungs_fail_and_permission_present() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            10,
            Outcome::Err(AcquireError::NotAvailable),
        );
        let s1 = FakeAcquirer::new(
            ImageAcquisition::CacheFolder,
            clock.clone(),
            10,
            Outcome::Empty,
        );
        let s2 = FakeAcquirer::ok(ImageAcquisition::ScreenCapture, clock.clone());
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert!(out.is_acquired());
        assert_eq!(out.attempts[2], StepAttempt::Acquired);
        assert!(out.used_screen_capture());
        assert_eq!(out.acquired_via(), Some(ImageAcquisition::ScreenCapture));
    }

    #[test]
    fn all_three_rungs_fail_yields_no_image() {
        let clock = VirtualClock::new(0);
        let s0 = FakeAcquirer::new(
            ImageAcquisition::OriginalSave,
            clock.clone(),
            10,
            Outcome::Err(AcquireError::NotAvailable),
        );
        let s1 = FakeAcquirer::new(
            ImageAcquisition::CacheFolder,
            clock.clone(),
            10,
            Outcome::Err(AcquireError::Failed("boom".into())),
        );
        let s2 = FakeAcquirer::new(
            ImageAcquisition::ScreenCapture,
            clock.clone(),
            10,
            Outcome::Empty,
        );
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let out = ladder.acquire(&img_ref(), true);
        assert!(!out.is_acquired());
        assert_eq!(out.attempts[0], StepAttempt::Error("not_available"));
        assert_eq!(out.attempts[1], StepAttempt::Error("failed"));
        assert_eq!(out.attempts[2], StepAttempt::Empty);
        assert!(!out.used_screen_capture());
    }
}

// ===========================================================================
// Link forwarder (링크전달기) — task 7.1 (R6)
// ===========================================================================

use rusqlite::{params, Connection};
use thiserror::Error;

use crate::collector::{Collected, CollectFailure, RoutingCollector};
use crate::logging::{FlowKind, HistoryStore, PipelineEvent, Stage, StageStatus};
use crate::room_catalog::RoomCatalog;

/// The maximum links one forward request may carry (R6.16).
pub const MAX_LINKS: usize = 5;
/// The maximum room mentions one forward request may carry (R6.16).
pub const MAX_MENTIONS: usize = 10;
/// The hard upper bound on a forwarded summary, in characters (R6.3, R6.9).
pub const SUMMARY_MAX: usize = 700;
/// At or below this many characters the body is "short": the summary may keep
/// up to this many characters rather than halving (R6.9).
pub const SHORT_BODY_THRESHOLD: usize = 200;
/// The maximum images forwarded from one post; the rest are counted as dropped
/// (R6.4).
pub const MAX_IMAGES_PER_POST: usize = 10;
/// The per-link processing bound, in seconds (R6.12, R6.15).
pub const FORWARD_DEADLINE_SECS: u64 = 30;
/// How many delivery records are kept per room (R6.14).
pub const FORWARD_LEDGER_MAX_PER_ROOM: usize = 1_000;

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

/// A composer forward request: one or more links and one or more room mentions
/// (R6.1). Links are plain URL strings, matching the collector boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForwardRequest {
    /// The links to forward (1..=[`MAX_LINKS`]).
    pub links: Vec<String>,
    /// The room mentions to resolve (1..=[`MAX_MENTIONS`]).
    pub room_mentions: Vec<String>,
}

// ---------------------------------------------------------------------------
// Mention resolution (R6.1, R6.2)
// ---------------------------------------------------------------------------

/// A room a mention resolved to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedRoom {
    /// The database-authoritative chat id.
    pub chat_id: i64,
    /// The real room title the mention matched exactly.
    pub title: String,
}

/// The result of resolving one room mention (R6.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MentionResolution {
    /// Exactly one room title matched (case-sensitive, after trimming).
    Exact(ResolvedRoom),
    /// No room title matched. Carries up to ten candidate titles (R6.2).
    NotFound {
        /// Up to ten candidate room titles to guide the user.
        candidates: Vec<String>,
    },
    /// Two or more room titles matched, so the mention is ambiguous. Carries up
    /// to ten candidate titles (R6.2).
    Ambiguous {
        /// Up to ten candidate room titles to guide the user.
        candidates: Vec<String>,
    },
}

/// The maximum candidate titles surfaced when a mention fails to resolve (R6.2).
const MAX_CANDIDATES: usize = 10;

/// Resolve one room mention against the catalog (R6.1).
///
/// The mention is trimmed of leading/trailing whitespace and then compared for
/// a **case-sensitive**, whole-string equality against each room's real title.
/// Exactly one match resolves; zero or two-or-more matches fail, carrying up to
/// ten candidate titles (R6.2).
pub fn resolve_mention(mention: &str, catalog: &dyn RoomCatalog) -> MentionResolution {
    let target = mention.trim();
    let titles = catalog.titles();
    let matches: Vec<ResolvedRoom> = titles
        .iter()
        .filter(|(_, title)| title == target)
        .map(|(chat_id, title)| ResolvedRoom {
            chat_id: *chat_id,
            title: title.clone(),
        })
        .collect();

    match matches.len() {
        1 => MentionResolution::Exact(matches.into_iter().next().expect("one match")),
        0 => MentionResolution::NotFound {
            candidates: candidate_titles(&titles),
        },
        _ => MentionResolution::Ambiguous {
            candidates: candidate_titles(&titles),
        },
    }
}

/// Up to [`MAX_CANDIDATES`] non-empty room titles, for a resolution-failure
/// hint (R6.2).
fn candidate_titles(titles: &[(i64, String)]) -> Vec<String> {
    titles
        .iter()
        .filter(|(_, t)| !t.trim().is_empty())
        .take(MAX_CANDIDATES)
        .map(|(_, t)| t.clone())
        .collect()
}

// ---------------------------------------------------------------------------
// Summarize (R6.3, R6.9)
// ---------------------------------------------------------------------------

/// Summarize a body for forwarding (R6.9).
///
/// The result is always at most [`SUMMARY_MAX`] (700) characters. When the body
/// is longer than [`SHORT_BODY_THRESHOLD`] (200) characters, the result is also
/// at most half the body's character count, so a third party's post is never
/// copied verbatim. A short body (≤ 200 characters) is kept as-is, up to 200
/// characters. Truncation is on character boundaries, so multi-byte text (e.g.
/// Korean) is never split mid-character.
pub fn summarize(body: &str) -> String {
    let total = body.chars().count();
    let cap = if total <= SHORT_BODY_THRESHOLD {
        // A short body may be kept whole, capped at 200.
        total.min(SHORT_BODY_THRESHOLD)
    } else {
        // A longer body is at most half its length, and never over 700.
        (total / 2).min(SUMMARY_MAX)
    };
    body.chars().take(cap).collect()
}

// ---------------------------------------------------------------------------
// Canonical link key (R6.7)
// ---------------------------------------------------------------------------

/// The canonical key that decides whether two links are "the same" for
/// once-per-room delivery (R6.7): case-insensitive, ignoring any trailing
/// slashes.
pub fn canonical_link_key(url: &str) -> String {
    let trimmed = url.trim().to_lowercase();
    trimmed.trim_end_matches('/').to_string()
}

// ---------------------------------------------------------------------------
// PII masking (R6.13)
// ---------------------------------------------------------------------------

/// How many items of each personal-information kind were masked (R6.13).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PiiTally {
    /// Masked phone numbers.
    pub phone: usize,
    /// Masked addresses.
    pub address: usize,
    /// Masked account numbers.
    pub account: usize,
}

impl PiiTally {
    /// The total number of masked items across all kinds.
    pub fn total(&self) -> usize {
        self.phone + self.address + self.account
    }
}

/// The placeholder a masked phone number is replaced with (holds no digits).
const PHONE_MASK: &str = "[전화번호]";
/// The placeholder a masked account number is replaced with.
const ACCOUNT_MASK: &str = "[계좌번호]";
/// The placeholder a masked address is replaced with.
const ADDRESS_MASK: &str = "[주소]";

/// Mask phone numbers, addresses, and account numbers in `text`, returning the
/// masked text and the per-kind counts (R6.13).
///
/// Addresses are masked first (a token-level pass), so the digits inside an
/// address are gone before the phone/account digit scan runs and cannot be
/// double-counted. The masked value never appears in the result: each match is
/// replaced by a digit-free placeholder.
pub fn mask_pii(text: &str) -> (String, PiiTally) {
    let mut tally = PiiTally::default();
    // Phone and account numbers are masked first — they are the most specific
    // (a 9..=16 digit run) — so their digits are replaced by digit-free
    // placeholders and cannot be swallowed by the address token scan. An
    // address's short building number (a few digits) is never a phone/account
    // number, so it survives for the address pass.
    let after_digits = mask_phone_and_account(text, &mut tally);
    let masked = mask_addresses(&after_digits, &mut tally);
    (masked, tally)
}

/// Whether a token is an address unit word (ends with a Korean administrative
/// or road/way suffix).
fn is_area_token(token: &str) -> bool {
    matches!(
        token.chars().last(),
        Some('시' | '도' | '군' | '구' | '동' | '읍' | '면' | '리')
    ) && token.chars().count() >= 2
}

/// Whether a token is a road/way token (ends with 로 or 길), the strongest
/// address signal.
///
/// Road names are proper nouns of at least three syllables (테헤란로, 세종대로),
/// so the three-syllable floor excludes the common particles that also end in
/// 로/길 (e.g. "으로", "길") and would otherwise cause false positives.
fn is_road_token(token: &str) -> bool {
    matches!(token.chars().last(), Some('로' | '길')) && token.chars().count() >= 3
}

/// Whether a token is (mostly) a building/lot number: begins with a digit.
fn is_number_token(token: &str) -> bool {
    token.chars().next().is_some_and(|c| c.is_ascii_digit())
}

/// Mask address spans over whitespace tokens (R6.13).
///
/// A span qualifies as an address when it contains a road token (로/길) with a
/// following number, or at least two area tokens with a number — which masks a
/// standard Korean address while leaving ordinary phrases (e.g. "운동 3번")
/// untouched.
fn mask_addresses(text: &str, tally: &mut PiiTally) -> String {
    let tokens: Vec<&str> = text.split(' ').collect();
    let mut out: Vec<String> = Vec::with_capacity(tokens.len());
    let mut i = 0;
    while i < tokens.len() {
        // Grow a candidate span of consecutive address-ish tokens.
        let mut j = i;
        let mut area = 0usize;
        let mut road = false;
        let mut number = false;
        while j < tokens.len() {
            let t = tokens[j];
            if is_road_token(t) {
                road = true;
            } else if is_area_token(t) {
                area += 1;
            } else if is_number_token(t) {
                number = true;
            } else {
                break;
            }
            j += 1;
        }
        let qualifies = number && (road || area >= 2);
        if qualifies && j > i {
            out.push(ADDRESS_MASK.to_string());
            tally.address += 1;
            i = j;
        } else {
            out.push(tokens[i].to_string());
            i += 1;
        }
    }
    out.join(" ")
}

/// Mask phone and account numbers by scanning maximal `[0-9-]` runs (R6.13).
///
/// A run whose stripped digits number 9..=11 and begin with `0` is a phone
/// number; any other run of 10..=16 digits is an account number. Shorter or
/// stray digit runs (years, small counts) are left alone.
fn mask_phone_and_account(text: &str, tally: &mut PiiTally) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_ascii_digit() {
            // Consume a maximal digit/hyphen run.
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_digit() || chars[i] == '-') {
                i += 1;
            }
            let run: String = chars[start..i].iter().collect();
            let run = run.trim_end_matches('-');
            let digits: String = run.chars().filter(|c| c.is_ascii_digit()).collect();
            let digit_count = digits.len();
            if (9..=11).contains(&digit_count) && digits.starts_with('0') {
                out.push_str(PHONE_MASK);
                tally.phone += 1;
            } else if (10..=16).contains(&digit_count) {
                out.push_str(ACCOUNT_MASK);
                tally.account += 1;
            } else {
                out.push_str(run);
            }
            // Preserve any trailing hyphens that were trimmed off the run.
            let trailing = chars[start..i].len() - run.chars().count();
            for _ in 0..trailing {
                out.push('-');
            }
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Forward message (R6.3, R6.9)
// ---------------------------------------------------------------------------

/// The title field of a forward message (R6.3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TitleField {
    /// The original title was determined.
    Known(String),
    /// The original title could not be determined (R6.3).
    Unknown,
}

/// The forward message built for one link (R6.3, R6.9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForwardMessage {
    /// The original title, or [`TitleField::Unknown`] when it is unavailable.
    pub title: TitleField,
    /// The summary (≤ 700 chars, ≤ 50% of the body; see [`summarize`]).
    pub summary: String,
    /// The source link.
    pub source: String,
    /// The author, when the post is a third party's (R6.9).
    pub author: Option<String>,
    /// The acquired images to attach, in post order, at most
    /// [`MAX_IMAGES_PER_POST`].
    pub images: Vec<ImageBlob>,
    /// How many images were dropped for exceeding the per-post cap (R6.4).
    pub dropped_images: usize,
}

impl ForwardMessage {
    /// Render the message text a room receives: the title (or an "unknown"
    /// marker), the author when present, the summary, and the source link
    /// (R6.3, R6.9).
    pub fn render(&self) -> String {
        let mut out = String::new();
        match &self.title {
            TitleField::Known(title) => out.push_str(title),
            TitleField::Unknown => out.push_str("(제목 확인 불가)"),
        }
        out.push('\n');
        if let Some(author) = &self.author {
            out.push_str("작성자: ");
            out.push_str(author);
            out.push('\n');
        }
        out.push_str(&self.summary);
        out.push('\n');
        out.push_str("출처: ");
        out.push_str(&self.source);
        out
    }
}

// ---------------------------------------------------------------------------
// Image tally (R6.5, R6.6)
// ---------------------------------------------------------------------------

/// The source vs delivered image counts for one (room, link) forward (R6.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImageTally {
    /// The number of images in the source post (including any over the cap).
    pub source: usize,
    /// The number of images successfully delivered.
    pub delivered: usize,
}

impl ImageTally {
    /// Whether every source image was delivered (R6.5).
    pub fn is_complete(&self) -> bool {
        self.source == self.delivered
    }

    /// How many images are missing (source minus delivered) (R6.6).
    pub fn missing(&self) -> usize {
        self.source.saturating_sub(self.delivered)
    }
}

// ---------------------------------------------------------------------------
// Delivery ledger (R6.7, R6.14)
// ---------------------------------------------------------------------------

/// A failure from the delivery ledger.
#[derive(Debug, Error)]
pub enum LedgerError {
    /// The backing store failed.
    #[error("전달 완료 기록 저장소 오류: {0}")]
    Backend(String),
}

/// Records which (room, link) combinations have been confirmed as delivered, so
/// each link is forwarded to each room at most once (R6.7, R6.14).
pub trait DeliveryLedger {
    /// Whether `link_key` has already been delivered to `chat_id`.
    fn is_delivered(&self, chat_id: i64, link_key: &str) -> bool;

    /// Record a confirmed delivery. Only a fully-delivered combination is ever
    /// marked (R6.14); an image-count mismatch never calls this, so it stays
    /// retryable (R6.6).
    fn mark(&self, chat_id: i64, link_key: &str, at: i64) -> Result<(), LedgerError>;
}

/// A [`DeliveryLedger`] backed by SQLite over the `forward_ledger` table. Keeps
/// only the most-recent [`FORWARD_LEDGER_MAX_PER_ROOM`] records per room (R6.14).
pub struct SqliteDeliveryLedger {
    conn: Connection,
}

impl SqliteDeliveryLedger {
    /// Wrap a connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, LedgerError> {
        ensure_forward_ledger_schema(&conn)?;
        Ok(Self { conn })
    }

    /// Open (or create) a ledger at `path`.
    pub fn open(path: &std::path::Path) -> Result<Self, LedgerError> {
        let conn = Connection::open(path).map_err(ledger_err)?;
        Self::new(conn)
    }

    /// Open an in-memory ledger. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, LedgerError> {
        let conn = Connection::open_in_memory().map_err(ledger_err)?;
        Self::new(conn)
    }
}

fn ledger_err(e: rusqlite::Error) -> LedgerError {
    LedgerError::Backend(e.to_string())
}

/// Create the `forward_ledger` table if absent (R6 schema).
pub fn ensure_forward_ledger_schema(conn: &Connection) -> Result<(), LedgerError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS forward_ledger(
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            link_key TEXT NOT NULL,
            at INTEGER NOT NULL,
            UNIQUE(chat_id, link_key)
        );
        CREATE INDEX IF NOT EXISTS idx_forward_ledger_room
            ON forward_ledger(chat_id, at DESC, id DESC);",
    )
    .map_err(ledger_err)
}

impl DeliveryLedger for SqliteDeliveryLedger {
    fn is_delivered(&self, chat_id: i64, link_key: &str) -> bool {
        self.conn
            .query_row(
                "SELECT 1 FROM forward_ledger WHERE chat_id = ?1 AND link_key = ?2",
                params![chat_id, link_key],
                |_| Ok(()),
            )
            .is_ok()
    }

    fn mark(&self, chat_id: i64, link_key: &str, at: i64) -> Result<(), LedgerError> {
        self.conn
            .execute(
                "INSERT INTO forward_ledger(chat_id, link_key, at)
                 VALUES (?1, ?2, ?3)
                 ON CONFLICT(chat_id, link_key) DO UPDATE SET at = excluded.at",
                params![chat_id, link_key, at],
            )
            .map_err(ledger_err)?;
        // Retention: keep only the most-recent records for this room (R6.14).
        self.conn
            .execute(
                "DELETE FROM forward_ledger
                 WHERE chat_id = ?1 AND id NOT IN (
                     SELECT id FROM forward_ledger
                     WHERE chat_id = ?1
                     ORDER BY at DESC, id DESC
                     LIMIT ?2
                 )",
                params![chat_id, FORWARD_LEDGER_MAX_PER_ROOM as i64],
            )
            .map_err(ledger_err)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Collaborator seams
// ---------------------------------------------------------------------------

/// The link-collection seam (R11). In production this is the two-path
/// [`RoutingCollector`]; tests inject a scripted collector so no real network
/// is touched.
pub trait LinkCollector {
    /// Collect the content of one link.
    fn collect(&self, url: &str) -> Result<Collected, CollectFailure>;
}

impl LinkCollector for RoutingCollector<'_> {
    fn collect(&self, url: &str) -> Result<Collected, CollectFailure> {
        RoutingCollector::collect(self, url)
    }
}

/// The outcome of sending one forward message to one room (R6.10).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RoomSendOutcome {
    /// The text was sent and this many images were delivered.
    Sent {
        /// How many attached images were confirmed as delivered.
        images_delivered: usize,
    },
    /// The safety gate fenced this room; carries a plain-language reason (R6.10).
    Fenced {
        /// Why the send was fenced.
        reason: String,
    },
    /// The send failed at some stage.
    Failed {
        /// A short, stable failure code.
        code: &'static str,
    },
}

/// The room-send seam (R6.10). In production this wraps
/// [`SendGuard`](crate::safety::SendGuard) authorization plus the
/// [`SendPort`](crate::ports::SendPort); a fenced room reports
/// [`RoomSendOutcome::Fenced`] and performs no send. Tests inject a scripted
/// sender so no real KakaoTalk send happens.
pub trait RoomSender {
    /// Authorize and send `text` with `images` to `chat_id`.
    fn send_forward(&self, chat_id: i64, text: &str, images: &[ImageBlob]) -> RoomSendOutcome;
}

// ---------------------------------------------------------------------------
// Forward outcome
// ---------------------------------------------------------------------------

/// Why a mention failed to resolve (R6.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MentionFailReason {
    /// No room title matched.
    NotFound,
    /// Two or more room titles matched.
    Ambiguous,
}

/// One mention that failed to resolve, with its candidate hint (R6.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MentionFailure {
    /// The original mention text.
    pub mention: String,
    /// Why it failed.
    pub reason: MentionFailReason,
    /// Up to ten candidate room titles.
    pub candidates: Vec<String>,
}

/// Why a whole forward request was rejected before any send (R6.2, R6.16).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ForwardRejection {
    /// The link or mention count was outside its allowed range (R6.16).
    CountOutOfRange {
        /// The number of links supplied.
        links: usize,
        /// The number of mentions supplied.
        mentions: usize,
    },
    /// One or more mentions failed to resolve; all-or-nothing, so **zero** sends
    /// happen to any room (R6.2).
    MentionResolution(Vec<MentionFailure>),
}

/// The per-room delivery result (R6.5, R6.6, R6.7, R6.10).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeliveryResult {
    /// This (room, link) was already delivered; zero new sends (R6.7).
    AlreadyDelivered,
    /// The gate fenced this room; no send (R6.10).
    Fenced {
        /// The plain-language fence reason.
        reason: String,
    },
    /// Sent, with every source image delivered — marked in the ledger (R6.5).
    Sent {
        /// The image tally (complete).
        images: ImageTally,
    },
    /// A partial failure: the image counts disagree, so the combination is
    /// **not** marked and can be retried (R6.6).
    PartialFailure {
        /// The image tally (incomplete).
        images: ImageTally,
        /// Per-issue plain-language reasons for the miss (R6.6).
        miss_reasons: Vec<String>,
    },
    /// The send failed at some stage.
    Failed {
        /// A short, stable failure code.
        code: &'static str,
    },
}

/// One room's delivery outcome for a link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoomDelivery {
    /// The target chat id.
    pub chat_id: i64,
    /// The room's display title.
    pub room_title: String,
    /// The delivery result.
    pub result: DeliveryResult,
}

/// The outcome of forwarding one link to all resolved rooms.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LinkOutcome {
    /// Collection failed; zero sends for this link (R6.8).
    CollectFailed(CollectFailure),
    /// The per-link deadline elapsed before sending; zero sends (R6.15).
    DeadlineExceeded,
    /// The link was delivered (see each room's result).
    Delivered {
        /// The per-room results.
        rooms: Vec<RoomDelivery>,
        /// The masked personal-information counts (R6.13).
        pii: PiiTally,
        /// The number of images in the source post.
        source_images: usize,
        /// The number of images dropped for exceeding the per-post cap (R6.4).
        dropped_images: usize,
    },
}

/// One link's result within a forward request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LinkResult {
    /// The link.
    pub link: String,
    /// Its outcome.
    pub outcome: LinkOutcome,
}

/// The overall outcome of a forward request (R6).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ForwardOutcome {
    /// The request was rejected before any send (R6.2, R6.16).
    Rejected(ForwardRejection),
    /// The request ran; carries each link's result.
    Ran {
        /// Per-link results, in request order.
        links: Vec<LinkResult>,
    },
}

// ---------------------------------------------------------------------------
// LinkForwarder
// ---------------------------------------------------------------------------

/// The link forwarder (R6). Resolves room mentions, collects link content,
/// builds a redacted summary, forwards it to each gate-passing room, and records
/// only fully-delivered combinations.
pub struct LinkForwarder<'a> {
    /// The link-collection seam (Aside/fallback in production).
    pub collector: &'a dyn LinkCollector,
    /// The room-send seam (guard + send port in production).
    pub sender: &'a dyn RoomSender,
    /// The once-per-(room, link) ledger.
    pub ledger: &'a dyn DeliveryLedger,
    /// The room catalog used to resolve mentions by real title.
    pub catalog: &'a dyn RoomCatalog,
    /// The shared image-acquisition ladder for post images (R6.4).
    pub ladder: &'a ImageLadder<'a>,
    /// The redacted pipeline journal (R6.11).
    pub journal: &'a dyn HistoryStore,
    /// The logical clock bounding per-link processing.
    pub clock: &'a dyn Clock,
    /// Whether the screen-recording permission is granted (gates the ladder's
    /// screen-capture rung, R9.10).
    pub screen_recording_granted: bool,
}

impl LinkForwarder<'_> {
    /// Forward every link to every resolved room (R6).
    ///
    /// The request is first checked against the count limits (R6.16), then
    /// **all** mentions are resolved; if any one fails, the whole request is
    /// rejected with zero sends (R6.2). Only then is each link collected and
    /// forwarded.
    pub fn forward(&self, req: &ForwardRequest) -> ForwardOutcome {
        // 1. Count limits — reject with zero sends (R6.16).
        if req.links.is_empty()
            || req.links.len() > MAX_LINKS
            || req.room_mentions.is_empty()
            || req.room_mentions.len() > MAX_MENTIONS
        {
            return ForwardOutcome::Rejected(ForwardRejection::CountOutOfRange {
                links: req.links.len(),
                mentions: req.room_mentions.len(),
            });
        }

        // 2. Resolve ALL mentions first (all-or-nothing, R6.2).
        let mut resolved: Vec<ResolvedRoom> = Vec::with_capacity(req.room_mentions.len());
        let mut failures: Vec<MentionFailure> = Vec::new();
        for mention in &req.room_mentions {
            match resolve_mention(mention, self.catalog) {
                MentionResolution::Exact(room) => resolved.push(room),
                MentionResolution::NotFound { candidates } => failures.push(MentionFailure {
                    mention: mention.clone(),
                    reason: MentionFailReason::NotFound,
                    candidates,
                }),
                MentionResolution::Ambiguous { candidates } => failures.push(MentionFailure {
                    mention: mention.clone(),
                    reason: MentionFailReason::Ambiguous,
                    candidates,
                }),
            }
        }
        if !failures.is_empty() {
            // Zero sends to any room (R6.2).
            return ForwardOutcome::Rejected(ForwardRejection::MentionResolution(failures));
        }

        // 3. Forward each link.
        let links = req
            .links
            .iter()
            .map(|link| self.forward_one_link(link, &resolved))
            .collect();
        ForwardOutcome::Ran { links }
    }

    /// Collect, build, and forward one link to every resolved room.
    fn forward_one_link(&self, link: &str, resolved: &[ResolvedRoom]) -> LinkResult {
        let start = self.clock.now_ms();
        let deadline = start + (FORWARD_DEADLINE_SECS * 1_000) as i64;
        let link_key = canonical_link_key(link);

        // Collect. A private/deleted/unreachable link stops here with zero
        // sends (R6.8); the collector already deleted its temp data (R11.8).
        let collected = match self.collector.collect(link) {
            Ok(collected) => collected,
            Err(failure) => {
                self.journal_event(
                    &link_key,
                    Stage::Retrieve,
                    StageStatus::Failed,
                    "collect_failed",
                    start,
                    None,
                    None,
                );
                return LinkResult {
                    link: link.to_string(),
                    outcome: LinkOutcome::CollectFailed(failure),
                };
            }
        };

        // The per-link deadline is checked before any send, so an over-time
        // link performs zero sends (R6.12, R6.15).
        if self.clock.now_ms() >= deadline {
            self.journal_event(
                &link_key,
                Stage::Retrieve,
                StageStatus::Failed,
                "deadline_exceeded",
                start,
                None,
                None,
            );
            return LinkResult {
                link: link.to_string(),
                outcome: LinkOutcome::DeadlineExceeded,
            };
        }

        // Mask PII, then summarize the masked body so no masked value survives
        // into the summary (R6.13).
        let (masked_body, pii) = mask_pii(&collected.body);
        let summary = summarize(&masked_body);

        // Acquire the post's images through the shared ladder (R6.4), capped at
        // the per-post maximum; the rest are dropped.
        let source_images = collected.images.len();
        let included = source_images.min(MAX_IMAGES_PER_POST);
        let (blobs, acquire_misses) = self.acquire_images(&collected.images[..included]);
        let dropped_images = source_images.saturating_sub(MAX_IMAGES_PER_POST);

        let message = ForwardMessage {
            title: match &collected.title {
                Some(title) => TitleField::Known(title.clone()),
                None => TitleField::Unknown,
            },
            summary,
            source: link.to_string(),
            author: collected.author.clone(),
            images: blobs,
            dropped_images,
        };

        let rooms = resolved
            .iter()
            .map(|room| {
                self.deliver_to_room(
                    room,
                    &link_key,
                    &message,
                    source_images,
                    dropped_images,
                    &acquire_misses,
                    start,
                )
            })
            .collect();

        LinkResult {
            link: link.to_string(),
            outcome: LinkOutcome::Delivered {
                rooms,
                pii,
                source_images,
                dropped_images,
            },
        }
    }

    /// Deliver one built message to one room, applying the once-per-room and
    /// image-parity rules (R6.5, R6.6, R6.7, R6.10, R6.14).
    #[allow(clippy::too_many_arguments)]
    fn deliver_to_room(
        &self,
        room: &ResolvedRoom,
        link_key: &str,
        message: &ForwardMessage,
        source_images: usize,
        dropped_images: usize,
        acquire_misses: &[String],
        start: i64,
    ) -> RoomDelivery {
        // Already forwarded — zero new sends (R6.7).
        if self.ledger.is_delivered(room.chat_id, link_key) {
            self.journal_event(
                link_key,
                Stage::PreSend,
                StageStatus::Success,
                "forward_already",
                start,
                Some(source_images as u32),
                Some(0),
            );
            return RoomDelivery {
                chat_id: room.chat_id,
                room_title: room.title.clone(),
                result: DeliveryResult::AlreadyDelivered,
            };
        }

        let result = match self
            .sender
            .send_forward(room.chat_id, &message.render(), &message.images)
        {
            RoomSendOutcome::Fenced { reason } => {
                self.journal_event(
                    link_key,
                    Stage::Authorize,
                    StageStatus::Failed,
                    "forward_fenced",
                    start,
                    Some(source_images as u32),
                    Some(0),
                );
                DeliveryResult::Fenced { reason }
            }
            RoomSendOutcome::Failed { code } => {
                self.journal_event(
                    link_key,
                    Stage::Commit,
                    StageStatus::Failed,
                    "forward_failed",
                    start,
                    Some(source_images as u32),
                    Some(0),
                );
                DeliveryResult::Failed { code }
            }
            RoomSendOutcome::Sent { images_delivered } => {
                let images = ImageTally {
                    source: source_images,
                    delivered: images_delivered,
                };
                if images.is_complete() {
                    // Only a fully-delivered combination is marked (R6.14).
                    let _ = self.ledger.mark(room.chat_id, link_key, self.clock.now_ms());
                    self.journal_event(
                        link_key,
                        Stage::Commit,
                        StageStatus::Success,
                        "forward_sent",
                        start,
                        Some(source_images as u32),
                        Some(images_delivered as u32),
                    );
                    DeliveryResult::Sent { images }
                } else {
                    // Image-count mismatch is a partial failure: do NOT mark, so
                    // the combination stays retryable (R6.6).
                    self.journal_event(
                        link_key,
                        Stage::Commit,
                        StageStatus::Failed,
                        "forward_partial",
                        start,
                        Some(source_images as u32),
                        Some(images_delivered as u32),
                    );
                    DeliveryResult::PartialFailure {
                        miss_reasons: miss_reasons(
                            source_images,
                            images_delivered,
                            dropped_images,
                            acquire_misses,
                        ),
                        images,
                    }
                }
            }
        };

        RoomDelivery {
            chat_id: room.chat_id,
            room_title: room.title.clone(),
            result,
        }
    }

    /// Acquire post images through the shared ladder, returning the acquired
    /// blobs (in order) and a plain-language reason for each miss (R6.4, R7.5).
    fn acquire_images(&self, refs: &[ImageRef]) -> (Vec<ImageBlob>, Vec<String>) {
        let mut blobs = Vec::new();
        let mut misses = Vec::new();
        for r in refs {
            let outcome = self.ladder.acquire(r, self.screen_recording_granted);
            match outcome.acquired {
                Some(blob) => blobs.push(blob),
                None => misses.push(acquire_miss_reason(&outcome)),
            }
        }
        (blobs, misses)
    }

    /// Append a redacted journal record carrying only the stage, result code,
    /// elapsed time, and the two image counts (R6.11).
    #[allow(
        clippy::too_many_arguments,
        reason = "one positional argument per recorded field keeps the call sites readable"
    )]
    fn journal_event(
        &self,
        link_key: &str,
        stage: Stage,
        status: StageStatus,
        result_code: &str,
        start: i64,
        images_source: Option<u32>,
        images_delivered: Option<u32>,
    ) {
        let now = self.clock.now_ms();
        // `trace_id` is hashed by the journal's redaction step before storage,
        // so passing the link key here never persists the raw URL (R6.11).
        let event = PipelineEvent {
            trace_id: format!("forward:{link_key}"),
            flow: FlowKind::LinkForward,
            stage,
            status,
            result_code: result_code.to_string(),
            duration_ms: now.saturating_sub(start).max(0) as u64,
            at: now,
        };
        let _ = self
            .journal
            .append_with_images(event, images_source, images_delivered);
    }
}

/// Build the per-issue miss reasons for a partial failure (R6.6).
fn miss_reasons(
    source: usize,
    delivered: usize,
    dropped: usize,
    acquire_misses: &[String],
) -> Vec<String> {
    let mut reasons = Vec::new();
    if dropped > 0 {
        reasons.push(format!(
            "이미지 {dropped}장은 한 게시물 최대 {MAX_IMAGES_PER_POST}장 제한을 넘어 전달하지 못했어요."
        ));
    }
    for reason in acquire_misses {
        reasons.push(reason.clone());
    }
    let missing = source.saturating_sub(delivered);
    let accounted = dropped + acquire_misses.len();
    if missing > accounted {
        reasons.push(format!(
            "이미지 {}장은 전송에 실패했어요. 다시 보내 볼까요?",
            missing - accounted
        ));
    }
    if reasons.is_empty() && missing > 0 {
        reasons.push(format!("이미지 {missing}장을 전달하지 못했어요."));
    }
    reasons
}

/// A short plain-language reason an image could not be acquired, from the
/// ladder's per-rung attempts (R6.6).
fn acquire_miss_reason(outcome: &LadderOutcome) -> String {
    if outcome.attempts.contains(&StepAttempt::PermissionMissing) {
        "화면 기록 권한이 없어 일부 이미지를 가져오지 못했어요.".to_string()
    } else if outcome.attempts.iter().all(|a| *a == StepAttempt::TimedOut) {
        "이미지 확보가 시간 안에 끝나지 않았어요.".to_string()
    } else {
        "원본 이미지를 가져오지 못했어요.".to_string()
    }
}

#[cfg(test)]
mod forward_tests {
    use super::*;
    use crate::fakes::VirtualClock;
    use crate::logging::SqliteHistoryStore;
    use crate::room_catalog::{CatalogRoom, InMemoryRoomCatalog, MapRoomDirectory};
    use std::cell::RefCell;
    use std::sync::Arc;

    // ---- resolve_mention ----

    fn catalog(rooms: &[(i64, &str)]) -> InMemoryRoomCatalog {
        let dir = Arc::new(MapRoomDirectory::new(
            rooms.iter().map(|(id, t)| (*id, (*t).to_string())),
        ));
        let catalog_rooms: Vec<CatalogRoom> = rooms
            .iter()
            .map(|(id, t)| CatalogRoom {
                chat_id: *id,
                title: (*t).to_string(),
                enabled: true,
                auto_reply: false,
                geeknews: false,
                link_forward: true,
                telegram_relay: false,
            })
            .collect();
        InMemoryRoomCatalog::from_rooms(dir, catalog_rooms)
    }

    #[test]
    fn resolve_mention_exact_after_trim() {
        let cat = catalog(&[(1, "스터디"), (2, "업무방")]);
        assert_eq!(
            resolve_mention("  스터디 ", &cat),
            MentionResolution::Exact(ResolvedRoom {
                chat_id: 1,
                title: "스터디".into()
            })
        );
    }

    #[test]
    fn resolve_mention_is_case_sensitive() {
        let cat = catalog(&[(1, "Study"), (2, "업무방")]);
        // A different case does not match (case-sensitive, R6.1).
        match resolve_mention("study", &cat) {
            MentionResolution::NotFound { candidates } => {
                assert!(candidates.contains(&"Study".to_string()));
            }
            other => panic!("expected NotFound, got {other:?}"),
        }
    }

    #[test]
    fn resolve_mention_ambiguous_when_two_titles_match() {
        let cat = catalog(&[(1, "스터디"), (2, "스터디")]);
        assert!(matches!(
            resolve_mention("스터디", &cat),
            MentionResolution::Ambiguous { .. }
        ));
    }

    #[test]
    fn resolve_mention_not_found_offers_candidates() {
        let cat = catalog(&[(1, "스터디"), (2, "업무방")]);
        match resolve_mention("없는방", &cat) {
            MentionResolution::NotFound { candidates } => {
                assert_eq!(candidates.len(), 2);
            }
            other => panic!("expected NotFound, got {other:?}"),
        }
    }

    // ---- summarize ----

    #[test]
    fn summarize_keeps_short_body_whole() {
        let body = "짧은 본문입니다";
        assert_eq!(summarize(body), body);
    }

    #[test]
    fn summarize_short_body_at_threshold_boundary() {
        let body: String = "가".repeat(SHORT_BODY_THRESHOLD);
        let out = summarize(&body);
        assert_eq!(out.chars().count(), SHORT_BODY_THRESHOLD);
    }

    #[test]
    fn summarize_long_body_is_half_and_under_cap() {
        let body: String = "가".repeat(1_000);
        let out = summarize(&body);
        let n = out.chars().count();
        assert!(n <= SUMMARY_MAX);
        assert!(n <= 1_000 / 2);
        assert_eq!(n, 500);
    }

    #[test]
    fn summarize_never_exceeds_seven_hundred() {
        let body: String = "a".repeat(10_000);
        let out = summarize(&body);
        assert_eq!(out.chars().count(), SUMMARY_MAX);
    }

    #[test]
    fn summarize_truncates_on_char_boundary() {
        // 201 multi-byte chars → cap 100; must not panic on a byte split.
        let body: String = "한".repeat(201);
        let out = summarize(&body);
        assert_eq!(out.chars().count(), 100);
    }

    // ---- canonical_link_key ----

    #[test]
    fn canonical_link_key_ignores_case_and_trailing_slash() {
        assert_eq!(
            canonical_link_key("https://Example.com/Post/"),
            canonical_link_key("https://example.com/post")
        );
        assert_eq!(
            canonical_link_key("https://x.com/a///"),
            "https://x.com/a"
        );
    }

    // ---- mask_pii ----

    #[test]
    fn mask_pii_masks_mobile_phone() {
        let (out, tally) = mask_pii("연락처는 010-1234-5678 입니다");
        assert!(!out.contains("010-1234-5678"));
        assert!(out.contains(PHONE_MASK));
        assert_eq!(tally.phone, 1);
    }

    #[test]
    fn mask_pii_masks_phone_without_hyphens() {
        let (out, tally) = mask_pii("전화 01012345678 로 주세요");
        assert!(!out.contains("01012345678"));
        assert_eq!(tally.phone, 1);
    }

    #[test]
    fn mask_pii_masks_account_number() {
        let (out, tally) = mask_pii("계좌 110-234-567890 으로 입금");
        assert!(!out.contains("110-234-567890"));
        assert!(out.contains(ACCOUNT_MASK));
        assert_eq!(tally.account, 1);
    }

    #[test]
    fn mask_pii_masks_address() {
        let (out, tally) = mask_pii("주소는 서울특별시 강남구 테헤란로 123 입니다");
        assert!(out.contains(ADDRESS_MASK));
        assert!(!out.contains("테헤란로 123"));
        assert_eq!(tally.address, 1);
    }

    #[test]
    fn mask_pii_leaves_ordinary_phrase_untouched() {
        // "운동 3번" has one area-ish token and a number but no road and only
        // one area token, so it is not masked.
        let (out, tally) = mask_pii("운동 3번 했어요");
        assert_eq!(out, "운동 3번 했어요");
        assert_eq!(tally.total(), 0);
    }

    #[test]
    fn mask_pii_masks_multiple_kinds() {
        let (out, tally) =
            mask_pii("서울특별시 강남구 테헤란로 5 010-1111-2222 123-456-7890");
        assert_eq!(tally.address, 1);
        assert_eq!(tally.phone, 1);
        assert_eq!(tally.account, 1);
        assert!(!out.contains("010-1111-2222"));
        assert!(!out.contains("123-456-7890"));
    }

    // ---- ImageTally ----

    #[test]
    fn image_tally_complete_and_missing() {
        assert!(ImageTally {
            source: 3,
            delivered: 3
        }
        .is_complete());
        let t = ImageTally {
            source: 5,
            delivered: 3,
        };
        assert!(!t.is_complete());
        assert_eq!(t.missing(), 2);
    }

    // ---- DeliveryLedger ----

    #[test]
    fn ledger_marks_and_reports_delivered() {
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let key = canonical_link_key("https://x.com/a/");
        assert!(!ledger.is_delivered(1, &key));
        ledger.mark(1, &key, 100).unwrap();
        assert!(ledger.is_delivered(1, &key));
        // A different room is independent.
        assert!(!ledger.is_delivered(2, &key));
    }

    #[test]
    fn ledger_retains_most_recent_thousand_per_room() {
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        for i in 0..(FORWARD_LEDGER_MAX_PER_ROOM as i64 + 5) {
            ledger.mark(1, &format!("link-{i}"), i).unwrap();
        }
        let count: i64 = ledger
            .conn
            .query_row(
                "SELECT COUNT(*) FROM forward_ledger WHERE chat_id = 1",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(count, FORWARD_LEDGER_MAX_PER_ROOM as i64);
        // The oldest links were evicted; the newest is retained.
        assert!(ledger.is_delivered(1, "link-1004"));
        assert!(!ledger.is_delivered(1, "link-0"));
    }

    // ---- LinkForwarder ----

    /// A collector that returns a scripted result and counts its calls.
    struct ScriptedCollector {
        result: Result<Collected, CollectFailure>,
        calls: RefCell<usize>,
    }

    impl ScriptedCollector {
        fn ok(collected: Collected) -> Self {
            Self {
                result: Ok(collected),
                calls: RefCell::new(0),
            }
        }

        fn fail(failure: CollectFailure) -> Self {
            Self {
                result: Err(failure),
                calls: RefCell::new(0),
            }
        }
    }

    impl LinkCollector for ScriptedCollector {
        fn collect(&self, _url: &str) -> Result<Collected, CollectFailure> {
            *self.calls.borrow_mut() += 1;
            self.result.clone()
        }
    }

    /// A room sender that returns a fixed outcome and counts its sends.
    struct ScriptedSender {
        outcome: RoomSendOutcome,
        sends: RefCell<usize>,
    }

    impl ScriptedSender {
        fn new(outcome: RoomSendOutcome) -> Self {
            Self {
                outcome,
                sends: RefCell::new(0),
            }
        }

        fn send_count(&self) -> usize {
            *self.sends.borrow()
        }
    }

    impl RoomSender for ScriptedSender {
        fn send_forward(
            &self,
            _chat_id: i64,
            _text: &str,
            _images: &[ImageBlob],
        ) -> RoomSendOutcome {
            *self.sends.borrow_mut() += 1;
            self.outcome.clone()
        }
    }

    /// An acquirer that always yields a non-empty image.
    struct OkAcquirer {
        clock: VirtualClock,
    }
    impl ImageAcquirer for OkAcquirer {
        fn kind(&self) -> ImageAcquisition {
            ImageAcquisition::OriginalSave
        }
        fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
            self.clock.advance_ms(10);
            Ok(ImageBlob {
                bytes: vec![1, 2, 3],
                mime: "image/png".into(),
            })
        }
    }

    /// An acquirer that never yields an image (used to fill unused rungs).
    struct NeverAcquirer {
        kind: ImageAcquisition,
    }
    impl ImageAcquirer for NeverAcquirer {
        fn kind(&self) -> ImageAcquisition {
            self.kind
        }
        fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
            Err(AcquireError::NotAvailable)
        }
    }

    fn collected(images: usize) -> Collected {
        Collected {
            title: Some("좋은 글".into()),
            body: "본문 내용입니다. ".repeat(30),
            images: (0..images)
                .map(|i| ImageRef {
                    locator: format!("img-{i}"),
                })
                .collect(),
            author: Some("작성자".into()),
            path: crate::collector::CollectorPath::Fallback,
            switched: None,
        }
    }

    struct Harness {
        ladder_clock: VirtualClock,
        ok: OkAcquirer,
        n1: NeverAcquirer,
        n2: NeverAcquirer,
    }

    impl Harness {
        fn new() -> Self {
            let ladder_clock = VirtualClock::new(0);
            Self {
                ok: OkAcquirer {
                    clock: ladder_clock.clone(),
                },
                n1: NeverAcquirer {
                    kind: ImageAcquisition::CacheFolder,
                },
                n2: NeverAcquirer {
                    kind: ImageAcquisition::ScreenCapture,
                },
                ladder_clock,
            }
        }

        fn ladder(&self) -> ImageLadder<'_> {
            ImageLadder {
                steps: [&self.ok, &self.n1, &self.n2],
                clock: &self.ladder_clock,
            }
        }
    }

    #[test]
    fn forward_rejects_out_of_range_counts() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(0));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        // Zero links.
        assert!(matches!(
            fwd.forward(&ForwardRequest {
                links: vec![],
                room_mentions: vec!["스터디".into()],
            }),
            ForwardOutcome::Rejected(ForwardRejection::CountOutOfRange { .. })
        ));
        // Too many links.
        assert!(matches!(
            fwd.forward(&ForwardRequest {
                links: (0..=MAX_LINKS).map(|i| format!("https://x/{i}")).collect(),
                room_mentions: vec!["스터디".into()],
            }),
            ForwardOutcome::Rejected(ForwardRejection::CountOutOfRange { .. })
        ));
        // No sends happened.
        assert_eq!(sender.send_count(), 0);
    }

    #[test]
    fn forward_all_or_nothing_on_mention_failure() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(0));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        // One good mention, one bad — the whole request is rejected with zero
        // sends (R6.2).
        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into(), "없는방".into()],
        });
        match outcome {
            ForwardOutcome::Rejected(ForwardRejection::MentionResolution(failures)) => {
                assert_eq!(failures.len(), 1);
                assert_eq!(failures[0].mention, "없는방");
            }
            other => panic!("expected mention rejection, got {other:?}"),
        }
        assert_eq!(sender.send_count(), 0);
        assert_eq!(*collector.calls.borrow(), 0);
    }

    #[test]
    fn forward_happy_path_sends_and_marks_ledger() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(2));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 2 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => {
                assert_eq!(links.len(), 1);
                match &links[0].outcome {
                    LinkOutcome::Delivered { rooms, .. } => {
                        assert_eq!(rooms.len(), 1);
                        assert!(matches!(
                            rooms[0].result,
                            DeliveryResult::Sent {
                                images: ImageTally {
                                    source: 2,
                                    delivered: 2
                                }
                            }
                        ));
                    }
                    other => panic!("expected delivered, got {other:?}"),
                }
            }
            other => panic!("expected ran, got {other:?}"),
        }
        // The combination is now recorded.
        assert!(ledger.is_delivered(1, &canonical_link_key("https://x.com/a")));
    }

    #[test]
    fn forward_image_mismatch_is_partial_and_not_marked() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(2));
        // Only one of two images delivered → mismatch.
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 1 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => match &links[0].outcome {
                LinkOutcome::Delivered { rooms, .. } => match &rooms[0].result {
                    DeliveryResult::PartialFailure {
                        images,
                        miss_reasons,
                    } => {
                        assert_eq!(images.missing(), 1);
                        assert!(!miss_reasons.is_empty());
                    }
                    other => panic!("expected partial failure, got {other:?}"),
                },
                other => panic!("expected delivered, got {other:?}"),
            },
            other => panic!("expected ran, got {other:?}"),
        }
        // A partial failure is retryable: nothing recorded (R6.6).
        assert!(!ledger.is_delivered(1, &canonical_link_key("https://x.com/a")));
    }

    #[test]
    fn forward_already_delivered_sends_nothing() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(0));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        ledger
            .mark(1, &canonical_link_key("https://x.com/a"), 1)
            .unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a/".into()], // trailing slash still matches
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => match &links[0].outcome {
                LinkOutcome::Delivered { rooms, .. } => {
                    assert_eq!(rooms[0].result, DeliveryResult::AlreadyDelivered);
                }
                other => panic!("expected delivered, got {other:?}"),
            },
            other => panic!("expected ran, got {other:?}"),
        }
        assert_eq!(sender.send_count(), 0);
    }

    #[test]
    fn forward_fenced_room_is_not_marked() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(0));
        let sender = ScriptedSender::new(RoomSendOutcome::Fenced {
            reason: "안전 설정이 꺼져 있어요".into(),
        });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => match &links[0].outcome {
                LinkOutcome::Delivered { rooms, .. } => {
                    assert!(matches!(rooms[0].result, DeliveryResult::Fenced { .. }));
                }
                other => panic!("expected delivered, got {other:?}"),
            },
            other => panic!("expected ran, got {other:?}"),
        }
        assert!(!ledger.is_delivered(1, &canonical_link_key("https://x.com/a")));
    }

    #[test]
    fn forward_collect_failure_sends_nothing() {
        let cat = catalog(&[(1, "스터디")]);
        let collector =
            ScriptedCollector::fail(CollectFailure::Terminal(crate::collector::CollectError::Private));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 0 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => {
                assert!(matches!(links[0].outcome, LinkOutcome::CollectFailed(_)));
            }
            other => panic!("expected ran, got {other:?}"),
        }
        assert_eq!(sender.send_count(), 0);
    }

    #[test]
    fn forward_over_ten_images_counts_dropped() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(12));
        // Deliver the 10 included images.
        let sender = ScriptedSender::new(RoomSendOutcome::Sent {
            images_delivered: MAX_IMAGES_PER_POST,
        });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        let outcome = fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/a".into()],
            room_mentions: vec!["스터디".into()],
        });
        match outcome {
            ForwardOutcome::Ran { links } => match &links[0].outcome {
                LinkOutcome::Delivered {
                    dropped_images,
                    source_images,
                    rooms,
                    ..
                } => {
                    assert_eq!(*source_images, 12);
                    assert_eq!(*dropped_images, 2);
                    // 12 source vs 10 delivered → partial failure, not marked.
                    assert!(matches!(
                        rooms[0].result,
                        DeliveryResult::PartialFailure { .. }
                    ));
                }
                other => panic!("expected delivered, got {other:?}"),
            },
            other => panic!("expected ran, got {other:?}"),
        }
    }

    #[test]
    fn forward_journals_only_redacted_records() {
        let cat = catalog(&[(1, "스터디")]);
        let collector = ScriptedCollector::ok(collected(1));
        let sender = ScriptedSender::new(RoomSendOutcome::Sent { images_delivered: 1 });
        let ledger = SqliteDeliveryLedger::open_in_memory().unwrap();
        let journal = SqliteHistoryStore::open_in_memory().unwrap();
        let clock = VirtualClock::new(0);
        let harness = Harness::new();
        let ladder = harness.ladder();
        let fwd = LinkForwarder {
            collector: &collector,
            sender: &sender,
            ledger: &ledger,
            catalog: &cat,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };

        fwd.forward(&ForwardRequest {
            links: vec!["https://x.com/secret-path".into()],
            room_mentions: vec!["스터디".into()],
        });

        let recent = journal.recent(10).unwrap();
        assert!(!recent.is_empty());
        for ev in recent {
            assert_eq!(ev.flow, FlowKind::LinkForward);
            // The URL never appears verbatim (trace id is a provenance hash).
            assert!(!ev.trace_id.contains("secret-path"));
            assert!(!ev.result_code.contains("secret-path"));
        }
    }
}
