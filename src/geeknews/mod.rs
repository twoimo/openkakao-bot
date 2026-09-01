//! GeekNews posting rule enforcement (R11.7, Correctness Property 6).
//!
//! GeekNews is posted from the official Atom feed (`https://news.hada.io/rss/news`)
//! at most three times per day, once inside each of three KST time slots, and
//! each slot posts only the TOP5 not-yet-seen items. The dedup cursor —
//! `seen`/`slots` — is the single source of truth for "already posted", so its
//! integrity decides whether an item can ever be re-sent.
//!
//! The invariant this module guarantees is ordering: the `seen`/`slots` state
//! is persisted **only after a send is confirmed**. [`run_geeknews_post`] calls
//! the [`Sender`] first and touches the [`CursorStore`] only once the send
//! returns `Ok`. If the send is not confirmed (any [`SendError`]), the store is
//! never written, so `seen`/`slots` are left exactly as they were and the same
//! items remain eligible for the next attempt. This is Correctness Property 6:
//! a failed send has zero effect on the dedup cursor.
//!
//! Every real product send still funnels through the safety gate: the
//! production [`Sender`] is the seam that applies AX local-send with the
//! allowlist/opt-in checks. This module is agnostic to how the send happens; it
//! only enforces that persistence follows a *confirmed* send.
//!
//! The formatting rule is preserved verbatim: a `GeekNews TOP5 · {when}` header,
//! then a blank line, then numbered items `1.`–`5.` with a blank line between
//! each item.

use std::collections::BTreeSet;

use chrono::{Duration, FixedOffset, NaiveDate, TimeZone};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

/// The official GeekNews Atom feed. Stored in the cursor for provenance parity
/// with the existing tool.
pub const FEED_URL: &str = "https://news.hada.io/rss/news";

/// TOP5: the maximum number of items posted in a single slot.
pub const MAX_ITEMS: usize = 5;

/// Longest a per-item summary may be before it is trimmed with an ellipsis.
const MAX_SUMMARY_CHARS: usize = 90;

/// Each slot stays open for 30 minutes after its (jittered) anchor.
const SLOT_WINDOW_SECS: i64 = 30 * 60;

/// KST is a fixed UTC+9 offset (Korea observes no DST), so the slot math is
/// deterministic without a timezone database.
const KST_OFFSET_SECS: i32 = 9 * 3600;

/// The three daily KST slots as `(name, hour, minute, jitter_minutes)`. The
/// anchor is `hour:minute` KST and the concrete open time is that anchor shifted
/// by a deterministic per-day jitter in `[-jitter, +jitter]` minutes.
const DAILY_SLOTS: [(&str, u32, u32, i64); 3] = [
    ("morning", 8, 40, 20),
    ("lunch", 12, 35, 15),
    ("evening", 19, 50, 25),
];

/// Keep at most this many recent seen IDs in the cursor.
const SEEN_CAP: usize = 200;

/// Keep at most this many recent posted-slot markers in the cursor.
const SLOTS_CAP: usize = 32;

/// One parsed feed entry. Only the fields needed to dedup and format a post are
/// retained.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedItem {
    /// The stable GeekNews topic id (`topic?id=<n>`). This is the dedup key.
    pub id: u32,
    /// The plain-text title.
    pub title: String,
    /// The canonical `https://news.hada.io/topic?id=<n>` URL.
    pub url: String,
    /// A short plain-text summary (may be empty).
    pub summary: String,
}

/// The persisted GeekNews dedup cursor: which items were already posted
/// (`seen_ids`) and which slots were already used (`posted_slots`). This is the
/// state R11.7 requires be written only after a confirmed send.
///
/// The JSON shape matches the existing `geeknews-rss-cursor.json` so a store can
/// round-trip either producer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GeekNewsCursor {
    /// The feed this cursor tracks.
    #[serde(default = "default_feed")]
    pub feed: String,
    /// The highest topic id observed in the feed.
    #[serde(default)]
    pub newest_id: u32,
    /// Topic ids already posted (kept sorted, capped at [`SEEN_CAP`]).
    #[serde(default)]
    pub seen_ids: Vec<u32>,
    /// `day:name` markers of slots already posted (capped at [`SLOTS_CAP`]).
    #[serde(default)]
    pub posted_slots: Vec<String>,
    /// Unix seconds of the last write.
    #[serde(default)]
    pub updated_at: i64,
}

fn default_feed() -> String {
    FEED_URL.to_string()
}

impl Default for GeekNewsCursor {
    fn default() -> Self {
        Self {
            feed: default_feed(),
            newest_id: 0,
            seen_ids: Vec::new(),
            posted_slots: Vec::new(),
            updated_at: 0,
        }
    }
}

impl GeekNewsCursor {
    /// True when `id` has already been posted.
    pub fn has_seen(&self, id: u32) -> bool {
        self.seen_ids.binary_search(&id).is_ok()
    }

    /// True when the slot `marker` (`day:name`) has already been posted.
    pub fn has_posted_slot(&self, marker: &str) -> bool {
        self.posted_slots.iter().any(|m| m == marker)
    }

    /// Return a copy with `ids` recorded as seen, `newest` folded into
    /// `newest_id`, the slot `marker` recorded as posted, and `updated_at` set.
    /// Seen ids stay sorted/deduped and both lists are capped. This is a pure
    /// transform: it never touches any store.
    fn committed(
        &self,
        ids: &[u32],
        newest: u32,
        marker: &str,
        now: i64,
    ) -> GeekNewsCursor {
        let mut seen: BTreeSet<u32> = self.seen_ids.iter().copied().collect();
        seen.extend(ids.iter().copied());
        // Keep only the most recent SEEN_CAP ids (largest ids are newest).
        let mut seen_ids: Vec<u32> = seen.into_iter().collect();
        if seen_ids.len() > SEEN_CAP {
            seen_ids = seen_ids.split_off(seen_ids.len() - SEEN_CAP);
        }

        let mut posted_slots = self.posted_slots.clone();
        if !posted_slots.iter().any(|m| m == marker) {
            posted_slots.push(marker.to_string());
        }
        if posted_slots.len() > SLOTS_CAP {
            let start = posted_slots.len() - SLOTS_CAP;
            posted_slots = posted_slots.split_off(start);
        }

        let observed_max = ids.iter().copied().max().unwrap_or(0);
        GeekNewsCursor {
            feed: FEED_URL.to_string(),
            newest_id: self.newest_id.max(newest).max(observed_max),
            seen_ids,
            posted_slots,
            updated_at: now,
        }
    }
}

/// The Atom feed source. The production implementation performs the pinned HTTPS
/// fetch; tests inject a fake that returns canned XML with no network.
pub trait FeedSource {
    /// Fetch the raw Atom XML. An empty string is treated as "no items".
    fn fetch_xml(&self) -> Result<String, FeedError>;
}

/// The confirmed-send seam. The production implementation funnels through the
/// safety gate and AX local-send; tests inject a fake. A returned `Ok` means the
/// send was **confirmed** — only then may the cursor be persisted.
pub trait Sender {
    /// Send `message` to `chat_id`. `Ok(())` means the send was confirmed.
    fn send(&self, chat_id: i64, message: &str) -> Result<(), SendError>;
}

/// The dedup-cursor store (`seen`/`slots`). The production implementation is a
/// file; tests use an in-memory fake.
pub trait CursorStore {
    /// Load the current cursor. A missing cursor is the [`GeekNewsCursor::default`].
    fn load(&self) -> Result<GeekNewsCursor, StoreError>;
    /// Persist the cursor. Called **only after** a confirmed send.
    fn store(&self, cursor: &GeekNewsCursor) -> Result<(), StoreError>;
}

/// A feed-fetch failure.
#[derive(Debug, Error)]
#[error("긱뉴스 피드를 가져오지 못했어요: {0}")]
pub struct FeedError(pub String);

/// A send failure. Any of these means the send was **not** confirmed, so the
/// cursor must be left unchanged (R11.7).
#[derive(Debug, Error)]
#[error("긱뉴스를 보내지 못했어요: {0}")]
pub struct SendError(pub String);

/// A cursor-store failure.
#[derive(Debug, Error)]
#[error("긱뉴스 기록을 저장하지 못했어요: {0}")]
pub struct StoreError(pub String);

/// Why a post attempt did not send, or that it did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PostOutcome {
    /// A post was sent and the cursor was persisted afterwards.
    Posted {
        /// The `day:name` slot marker that was posted.
        slot: String,
        /// The topic ids that were posted.
        ids: Vec<u32>,
    },
    /// The current time is not inside an open slot (outside every window, or the
    /// slot for this time was already posted).
    NoOpenSlot,
    /// The feed returned no usable entries.
    EmptyFeed,
    /// Every feed item has already been seen; nothing fresh to post.
    NoFreshItems,
}

/// Errors that abort a post attempt.
#[derive(Debug, Error)]
pub enum GeekNewsError {
    /// The feed could not be fetched.
    #[error(transparent)]
    Feed(#[from] FeedError),
    /// The send was not confirmed. The cursor was left unchanged.
    #[error(transparent)]
    Send(#[from] SendError),
    /// The cursor could not be loaded or stored.
    #[error(transparent)]
    Store(#[from] StoreError),
}

/// The KST offset used for every slot computation.
fn kst() -> FixedOffset {
    FixedOffset::east_opt(KST_OFFSET_SECS).expect("KST offset is valid")
}

/// One concrete slot window on a concrete day.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlotWindow {
    /// The `day:name` marker (e.g. `2026-08-20:evening`).
    pub marker: String,
    /// The slot name (`morning`/`lunch`/`evening`).
    pub name: String,
    /// Unix seconds the window opens.
    pub start: i64,
    /// Unix seconds the window closes (exclusive).
    pub end: i64,
}

/// Compute a slot's `[start, end)` in unix seconds for a given KST `day`.
///
/// The jitter is a deterministic function of `day` and `name`: the first two
/// bytes of `sha256("<day>:<name>")` pick an offset in `[-jitter, +jitter]`
/// minutes from the anchor. This matches the existing tool exactly so an already
/// deployed cursor keeps aligning.
fn slot_window(day: NaiveDate, name: &str, hour: u32, minute: u32, jitter: i64) -> (i64, i64) {
    let seed = format!("{}:{}", day.format("%Y-%m-%d"), name);
    let digest = Sha256::digest(seed.as_bytes());
    let raw = u16::from_be_bytes([digest[0], digest[1]]) as i64;
    let span = jitter * 2 + 1;
    let offset = raw.rem_euclid(span) - jitter;

    let naive = day
        .and_hms_opt(hour, minute, 0)
        .expect("slot anchor time is valid");
    let anchor = kst()
        .from_local_datetime(&naive)
        .single()
        .expect("KST has no ambiguous local times")
        + Duration::minutes(offset);
    let start = anchor.timestamp();
    (start, start + SLOT_WINDOW_SECS)
}

/// Return the slot open at `now` (unix seconds) as `(day, name)`, or `None` when
/// `now` is outside every window for its KST day.
pub fn slot_at(now: i64) -> Option<(String, String)> {
    let local = kst().timestamp_opt(now, 0).single()?;
    let day = local.date_naive();
    for (name, hour, minute, jitter) in DAILY_SLOTS {
        let (start, end) = slot_window(day, name, hour, minute, jitter);
        if start <= now && now < end {
            return Some((day.format("%Y-%m-%d").to_string(), name.to_string()));
        }
    }
    None
}

/// Every slot window for the KST day containing `now`. Ordered by start time.
/// Useful for callers (and tests) that need a concrete in-window instant.
pub fn day_slot_windows(now: i64) -> Vec<SlotWindow> {
    let Some(local) = kst().timestamp_opt(now, 0).single() else {
        return Vec::new();
    };
    let day = local.date_naive();
    let day_text = day.format("%Y-%m-%d").to_string();
    let mut windows: Vec<SlotWindow> = DAILY_SLOTS
        .into_iter()
        .map(|(name, hour, minute, jitter)| {
            let (start, end) = slot_window(day, name, hour, minute, jitter);
            SlotWindow {
                marker: format!("{day_text}:{name}"),
                name: name.to_string(),
                start,
                end,
            }
        })
        .collect();
    windows.sort_by_key(|w| w.start);
    windows
}

/// Strip HTML tags and unescape the handful of entities GeekNews emits, then
/// collapse whitespace. Deterministic and dependency-free.
fn html_to_plain(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut in_tag = false;
    for ch in value.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(ch),
            _ => {}
        }
    }
    let unescaped = out
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&nbsp;", " ");
    unescaped.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Extract the inner text of the first `<tag ...>...</tag>` in `block`,
/// unwrapping a `<![CDATA[ ... ]]>` payload if present.
fn extract_tag(block: &str, tag: &str) -> Option<String> {
    let open_marker = format!("<{tag}");
    let open_pos = block.find(&open_marker)?;
    // Find the end of the opening tag ('>').
    let after_open = block[open_pos..].find('>')? + open_pos + 1;
    let close_marker = format!("</{tag}>");
    let close_pos = block[after_open..].find(&close_marker)? + after_open;
    let mut inner = &block[after_open..close_pos];
    inner = inner.trim();
    if let Some(rest) = inner.strip_prefix("<![CDATA[") {
        inner = rest.strip_suffix("]]>").unwrap_or(rest);
    }
    Some(inner.to_string())
}

/// Extract the `href` attribute value of the first `<link ... href=...>` in
/// `block`, accepting either single or double quotes.
fn extract_link_href(block: &str) -> Option<String> {
    let link_pos = block.find("<link")?;
    let tail = &block[link_pos..];
    let href_pos = tail.find("href")?;
    let after = &tail[href_pos..];
    // Skip to the first quote after `href`.
    let quote_rel = after.find(['\'', '"'])?;
    let quote = after.as_bytes()[quote_rel] as char;
    let value_start = href_pos + quote_rel + 1;
    let value_end = tail[value_start..].find(quote)? + value_start;
    Some(tail[value_start..value_end].trim().to_string())
}

/// Parse the GeekNews Atom feed into [`FeedItem`]s. Only `<entry>` blocks whose
/// link is a canonical `topic?id=<n>` URL with a non-empty title are kept,
/// preserving feed order.
pub fn parse_geeknews_entries(feed_xml: &str) -> Vec<FeedItem> {
    let mut items = Vec::new();
    let mut rest = feed_xml;
    while let Some(open) = rest.find("<entry>") {
        let after_open = open + "<entry>".len();
        let Some(close_rel) = rest[after_open..].find("</entry>") else {
            break;
        };
        let block = &rest[after_open..after_open + close_rel];
        rest = &rest[after_open + close_rel + "</entry>".len()..];

        let url = extract_link_href(block).unwrap_or_default();
        let Some(id) = parse_topic_id(&url) else {
            continue;
        };
        let title = extract_tag(block, "title")
            .map(|raw| html_to_plain(&raw))
            .unwrap_or_default();
        if title.is_empty() {
            continue;
        }
        let mut summary = extract_tag(block, "content")
            .map(|raw| html_to_plain(&raw))
            .unwrap_or_default();
        if summary.chars().count() > MAX_SUMMARY_CHARS {
            let trimmed: String = summary.chars().take(MAX_SUMMARY_CHARS).collect();
            summary = format!("{}…", trimmed.trim_end());
        }
        items.push(FeedItem {
            id,
            title,
            url,
            summary,
        });
    }
    items
}

/// Parse `<n>` from `https://news.hada.io/topic?id=<n>`, rejecting leading zeros
/// and over-long ids (mirrors the canonical topic-URL rule).
fn parse_topic_id(url: &str) -> Option<u32> {
    let digits = url.strip_prefix("https://news.hada.io/topic?id=")?;
    if digits.is_empty()
        || digits.len() > 10
        || digits.starts_with('0')
        || !digits.bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    digits.parse::<u32>().ok()
}

/// Build the `GeekNews TOP5 · {when}` post body from `items`.
///
/// The format is preserved verbatim: the header, a blank line, then numbered
/// items `1.`–`5.` separated by blank lines. An item with an `https://` URL is
/// rendered as `title url`; otherwise just the title. Returns an empty string
/// when nothing formattable remains.
pub fn format_top5(items: &[FeedItem], now: i64) -> String {
    let Some(local) = kst().timestamp_opt(now, 0).single() else {
        return String::new();
    };
    let when = local.format("%Y-%m-%d %H:%M KST").to_string();

    let parts: Vec<String> = items
        .iter()
        .take(MAX_ITEMS)
        .filter_map(|item| {
            let title = item.title.trim();
            if title.is_empty() {
                return None;
            }
            let url = item.url.trim();
            if url.starts_with("https://") {
                Some(format!("{title} {url}"))
            } else {
                Some(title.to_string())
            }
        })
        .collect();
    if parts.is_empty() {
        return String::new();
    }
    let numbered: Vec<String> = parts
        .iter()
        .enumerate()
        .map(|(index, part)| format!("{}. {part}", index + 1))
        .collect();
    format!("GeekNews TOP5 · {when}\n\n{}", numbered.join("\n\n"))
}

/// Run one GeekNews post attempt, enforcing R11.7 / Correctness Property 6.
///
/// Ordering is the whole point:
/// 1. Refuse unless `now` is inside an open, not-yet-posted slot.
/// 2. Fetch and parse the feed; pick up to [`MAX_ITEMS`] fresh (unseen) items.
/// 3. **Send first.** On any [`SendError`] the function returns the error
///    *without touching the store*, so `seen`/`slots` stay unchanged.
/// 4. Only after the send is confirmed, persist the updated cursor (record the
///    posted ids and slot marker).
///
/// A store failure after a confirmed send is surfaced as an error, but by then
/// the send already happened; the cursor is the only thing that could not be
/// updated. A send failure, by contrast, leaves the cursor pristine.
pub fn run_geeknews_post(
    chat_id: i64,
    now: i64,
    feed: &dyn FeedSource,
    sender: &dyn Sender,
    store: &dyn CursorStore,
) -> Result<PostOutcome, GeekNewsError> {
    let cursor = store.load()?;

    // 1. Slot gate: must be inside an open slot that has not been posted.
    let Some((day, name)) = slot_at(now) else {
        return Ok(PostOutcome::NoOpenSlot);
    };
    let marker = format!("{day}:{name}");
    if cursor.has_posted_slot(&marker) {
        return Ok(PostOutcome::NoOpenSlot);
    }

    // 2. Fetch + parse + pick fresh TOP5.
    let xml = feed.fetch_xml()?;
    let items = parse_geeknews_entries(&xml);
    if items.is_empty() {
        return Ok(PostOutcome::EmptyFeed);
    }
    let newest = items.iter().map(|item| item.id).max().unwrap_or(0);
    let fresh: Vec<FeedItem> = items
        .into_iter()
        .filter(|item| !cursor.has_seen(item.id))
        .take(MAX_ITEMS)
        .collect();
    if fresh.is_empty() {
        return Ok(PostOutcome::NoFreshItems);
    }
    let message = format_top5(&fresh, now);
    if message.is_empty() {
        return Ok(PostOutcome::NoFreshItems);
    }
    let ids: Vec<u32> = fresh.iter().map(|item| item.id).collect();

    // 3. Send first. A failure returns here and never reaches the store, so the
    //    dedup cursor is left exactly as it was (R11.7 / Correctness Property 6).
    sender.send(chat_id, &message)?;

    // 4. Persist seen/slots only now that the send is confirmed.
    let updated = cursor.committed(&ids, newest, &marker, now);
    store.store(&updated)?;

    Ok(PostOutcome::Posted { slot: marker, ids })
}

#[cfg(test)]
mod tests;
