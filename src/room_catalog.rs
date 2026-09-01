use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::sync::{Arc, RwLock};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use thiserror::Error;

const CATALOG_NAME: &str = "menubar-room-catalog.json";
const MAX_CATALOG_BYTES: usize = 64 * 1024;
const MAX_CATALOG_ROOMS: usize = 32;

#[derive(Debug, Clone, Deserialize)]
struct CatalogFile {
    #[serde(default)]
    rooms: Vec<CatalogRoom>,
}

/// A room in the automation catalog (R6).
///
/// `enabled` is the per-room master switch ("동작"): when it is off, neither the
/// reply nor the GeekNews automation runs for that room even if the individual
/// `auto_reply`/`geeknews` toggles are on. `title` holds the *real* room title
/// so the top-right list can expose exactly the same string as the room's
/// actual title (R6.7).
///
/// The new fields carry `#[serde(default)]` so an on-disk catalog written
/// before this expansion still deserializes: a legacy room has no explicit
/// `enabled`, and we default it to `true` so previously-configured
/// `auto_reply`/`geeknews` rooms keep behaving as before. The `link_forward`
/// and `telegram_relay` toggles default to `false`, so a legacy room is treated
/// as not opted in to link forwarding or Telegram relaying — which is exactly
/// what the coverage verifier reads to judge those features `unsupported` in a
/// room that has not turned them on (R5.4).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogRoom {
    pub chat_id: i64,
    /// The real room title, shown verbatim in the top-right list (R6.7).
    #[serde(default)]
    pub title: String,
    /// Per-room master switch ("동작"). Defaults to `true` for backward
    /// compatibility with catalogs written before this field existed.
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub auto_reply: bool,
    #[serde(default)]
    pub geeknews: bool,
    /// Link-forwarding opt-in ("링크 전달"). Defaults to `false` for backward
    /// compatibility (R5.4, R6.1).
    #[serde(default)]
    pub link_forward: bool,
    /// Telegram-relay opt-in ("텔레그램 중계"). Defaults to `false` for backward
    /// compatibility (R5.4).
    #[serde(default)]
    pub telegram_relay: bool,
}

fn default_true() -> bool {
    true
}

pub fn load_catalog_rooms(state_root: &Path) -> Result<Vec<CatalogRoom>> {
    let path = state_root.join(CATALOG_NAME);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error).with_context(|| format!("inspect {}", path.display())),
    };
    if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
        anyhow::bail!("menubar room catalog is not a regular file");
    }
    let size = metadata.len() as usize;
    if size == 0 || size > MAX_CATALOG_BYTES {
        anyhow::bail!("menubar room catalog size is unsafe");
    }
    let raw = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    let parsed: CatalogFile =
        serde_json::from_slice(&raw).context("menubar room catalog JSON is invalid")?;
    if parsed.rooms.len() > MAX_CATALOG_ROOMS {
        anyhow::bail!("menubar room catalog has too many rooms");
    }
    let mut seen = std::collections::BTreeSet::new();
    let mut rooms = Vec::new();
    for room in parsed.rooms {
        if room.chat_id <= 0 || room.chat_id == i64::MAX {
            anyhow::bail!("menubar room catalog chat ID is invalid");
        }
        if !seen.insert(room.chat_id) {
            anyhow::bail!("menubar room catalog chat ID is duplicated");
        }
        rooms.push(room);
    }
    Ok(rooms)
}

pub fn catalog_auto_reply_chat_ids(state_root: &Path) -> Result<Vec<i64>> {
    Ok(load_catalog_rooms(state_root)?
        .into_iter()
        .filter(|room| room.auto_reply)
        .map(|room| room.chat_id)
        .collect())
}

pub fn catalog_geeknews_chat_ids(state_root: &Path) -> Result<Vec<i64>> {
    Ok(load_catalog_rooms(state_root)?
        .into_iter()
        .filter(|room| room.geeknews)
        .map(|room| room.chat_id)
        .collect())
}

pub fn merge_configured_and_catalog_selectors(
    configured: &[String],
    catalog_chat_ids: &[i64],
    chats: &[crate::local_db::LocalChat],
) -> Result<Vec<String>> {
    merge_configured_and_catalog_selectors_named(configured, catalog_chat_ids, chats, &[])
}

pub fn merge_configured_and_catalog_selectors_named(
    configured: &[String],
    catalog_chat_ids: &[i64],
    chats: &[crate::local_db::LocalChat],
    group_titles: &[(i64, String)],
) -> Result<Vec<String>> {
    let titles = group_titles
        .iter()
        .filter(|(id, title)| *id > 0 && !title.trim().is_empty())
        .map(|(id, title)| (*id, title.trim().to_string()))
        .collect::<BTreeMap<_, _>>();
    let mut selectors = Vec::new();
    let mut seen = std::collections::BTreeSet::new();
    for value in configured {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            continue;
        }
        let parsed = crate::local_db::parse_chat_selectors(&[trimmed.to_string()])?;
        let resolved = crate::local_db::resolve_chat_selectors(chats, &parsed)?;
        for chat in resolved {
            if seen.insert(chat.chat_id) {
                selectors.push(binding_selector_named(&chat, titles.get(&chat.chat_id)));
            }
        }
    }
    let by_id = chats
        .iter()
        .filter(|chat| chat.chat_id > 0)
        .map(|chat| (chat.chat_id, chat))
        .collect::<BTreeMap<_, _>>();
    for chat_id in catalog_chat_ids {
        if !seen.insert(*chat_id) {
            continue;
        }
        let chat = by_id
            .get(chat_id)
            .with_context(|| format!("menubar catalog chat ID {chat_id} was not found"))?;
        selectors.push(binding_selector_named(chat, titles.get(chat_id)));
    }
    if selectors.len() > MAX_CATALOG_ROOMS {
        anyhow::bail!("too many unique chat targets (maximum {MAX_CATALOG_ROOMS})");
    }
    Ok(selectors)
}

fn binding_selector(chat: &crate::local_db::LocalChat) -> String {
    binding_selector_named(chat, None)
}

fn binding_selector_named(chat: &crate::local_db::LocalChat, group_title: Option<&String>) -> String {
    let name = if !chat.chat_name.trim().is_empty() {
        chat.chat_name.trim()
    } else if let Some(title) = group_title.filter(|title| !title.trim().is_empty()) {
        title.trim()
    } else if !chat.display_name.trim().is_empty() {
        chat.display_name.trim()
    } else {
        ""
    };
    if name.is_empty() {
        format!("id:{}", chat.chat_id)
    } else {
        format!("bind:{}:{name}", chat.chat_id)
    }
}

// ---------------------------------------------------------------------------
// Room catalog management (Task 8, R6.2–R6.8)
// ---------------------------------------------------------------------------

/// Which per-room switch a [`RoomCatalog::set_toggle`] call targets.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Toggle {
    /// The per-room master switch ("동작").
    Enabled,
    /// Automatic replies ("답변").
    AutoReply,
    /// GeekNews posting ("긱뉴스").
    GeekNews,
    /// Link forwarding ("링크 전달").
    LinkForward,
    /// Telegram relaying ("텔레그램 중계").
    TelegramRelay,
}

/// Why a catalog mutation was rejected. Each variant carries a plain-language
/// Korean message so the chat-room window can surface it directly (R6.3, R6.4).
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum RoomError {
    /// The room is already in the automation list (R6.3).
    #[error("이미 자동화 목록에 있는 채팅방이에요. 목록은 그대로 두었어요.")]
    Duplicate(i64),
    /// The room does not exist or cannot be accessed (R6.4).
    #[error("그 채팅방을 찾을 수 없거나 열 수 없어요. 채팅방이 있는지 확인한 뒤 다시 추가해 주세요.")]
    NotAccessible(i64),
    /// The room is not in the automation list, so it cannot be removed/toggled.
    #[error("자동화 목록에 없는 채팅방이에요. 먼저 채팅방을 추가해 주세요.")]
    NotInCatalog(i64),
    /// The catalog is already at the maximum number of rooms.
    #[error("채팅방을 너무 많이 추가했어요 (최대 {0}개). 하나를 지운 뒤 다시 추가해 주세요.")]
    TooMany(usize),
}

/// A directory of which rooms exist and are accessible, plus their real titles.
///
/// `add` consults this to reject an absent/inaccessible room (R6.4) and to
/// capture the real title so the top-right list shows exactly the same string
/// as the room's actual title (R6.7). In production this wraps the local
/// KakaoTalk database chats; tests inject an in-memory fake so no real Kakao or
/// network call is ever made.
pub trait RoomDirectory {
    /// The real title of an accessible room, or `None` when the room does not
    /// exist or cannot be accessed.
    fn title_of(&self, chat_id: i64) -> Option<String>;
}

/// An in-memory [`RoomDirectory`] backed by a fixed `chat_id -> title` map.
#[derive(Debug, Clone, Default)]
pub struct MapRoomDirectory {
    titles: BTreeMap<i64, String>,
}

impl MapRoomDirectory {
    /// Build a directory from `(chat_id, title)` pairs. Non-positive ids and
    /// blank titles are dropped so they can never be "accessible".
    pub fn new(entries: impl IntoIterator<Item = (i64, String)>) -> Self {
        let titles = entries
            .into_iter()
            .filter(|(id, title)| *id > 0 && !title.trim().is_empty())
            .map(|(id, title)| (id, title.trim().to_string()))
            .collect();
        Self { titles }
    }

    /// Build a directory from local database chats, deriving each room's title
    /// with the same rules the AX binding selector uses (chat name, then the
    /// provided group title, then the display name). This keeps the catalog's
    /// stored title aligned with the real room title (R6.7).
    pub fn from_local_chats(
        chats: &[crate::local_db::LocalChat],
        group_titles: &[(i64, String)],
    ) -> Self {
        let titles: BTreeMap<i64, String> = group_titles
            .iter()
            .filter(|(id, title)| *id > 0 && !title.trim().is_empty())
            .map(|(id, title)| (*id, title.trim().to_string()))
            .collect();
        let mut map = BTreeMap::new();
        for chat in chats {
            if chat.chat_id <= 0 {
                continue;
            }
            let title = if !chat.chat_name.trim().is_empty() {
                chat.chat_name.trim().to_string()
            } else if let Some(group) = titles.get(&chat.chat_id) {
                group.clone()
            } else if !chat.display_name.trim().is_empty() {
                chat.display_name.trim().to_string()
            } else {
                continue;
            };
            map.insert(chat.chat_id, title);
        }
        Self { titles: map }
    }
}

impl RoomDirectory for MapRoomDirectory {
    fn title_of(&self, chat_id: i64) -> Option<String> {
        self.titles.get(&chat_id).cloned()
    }
}

/// An owned, point-in-time snapshot of a room's automation state, pinned at the
/// **start** of processing a message.
///
/// The runtime pins one of these by calling [`RoomCatalog::pin`] when it begins
/// handling a message. Because the snapshot is an owned value, a later
/// [`RoomCatalog::set_toggle`] can never mutate a snapshot that was already
/// handed out: an in-flight message keeps the toggle values it started with,
/// and only a message whose processing *starts after* the change observes the
/// new values (R6.6, Correctness Property 5, latter half).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoomAutomation {
    pub chat_id: i64,
    /// Whether the room is still in the catalog. A removed room reports
    /// `present == false`, so all of its automation is inactive (R6.5).
    pub present: bool,
    pub enabled: bool,
    pub auto_reply: bool,
    pub geeknews: bool,
    /// The catalog version this snapshot was pinned at.
    pub version: u64,
}

impl RoomAutomation {
    /// True when automatic replies should run for this pinned snapshot: the room
    /// must be present, its master switch on, and the reply toggle on. Whether a
    /// real send actually happens is still decided by the safety gate
    /// ([`crate::safety::SafetyGate`]); this only reflects the room's opt-in
    /// (R6.8).
    pub fn auto_reply_active(&self) -> bool {
        self.present && self.enabled && self.auto_reply
    }

    /// True when GeekNews posting should run for this pinned snapshot. As with
    /// [`Self::auto_reply_active`], the safety gate still governs any real send.
    pub fn geeknews_active(&self) -> bool {
        self.present && self.enabled && self.geeknews
    }
}

/// Manage the automation catalog: list rooms, add/remove them, and flip their
/// per-room toggles (R6.2–R6.7).
///
/// Removal takes effect immediately — the room leaves [`RoomCatalog::list`] and
/// any [`RoomCatalog::pin`] after it reports `present == false` (R6.5). A toggle
/// only affects message processing that *starts after* the change: a snapshot
/// already pinned by an in-flight message is unchanged (R6.6).
pub trait RoomCatalog {
    /// All rooms currently in the catalog, ordered by `chat_id`.
    fn list(&self) -> Vec<CatalogRoom>;

    /// Add `chat_id` to the catalog. Rejects a duplicate (R6.3) and an
    /// absent/inaccessible room (R6.4); on success the room's real title is
    /// captured from the directory. A newly added room starts with the master
    /// switch on and both feature toggles off, so nothing sends until the
    /// operator turns a feature on.
    fn add(&self, chat_id: i64) -> Result<(), RoomError>;

    /// Remove `chat_id`. Its automation stops immediately (R6.5). Rejects a
    /// room that is not in the catalog.
    fn remove(&self, chat_id: i64) -> Result<(), RoomError>;

    /// Flip one toggle. The change is stored now but only applies from the next
    /// pinned message-processing snapshot (R6.6). Rejects a room that is not in
    /// the catalog.
    fn set_toggle(&self, chat_id: i64, kind: Toggle, on: bool) -> Result<(), RoomError>;

    /// The real title of a room in the catalog, or `None` if it is not present.
    /// This is the lookup the top-right list uses so it shows exactly the same
    /// string as the room's actual title (R6.7).
    fn title_of(&self, chat_id: i64) -> Option<String>;

    /// `(chat_id, title)` for every room, ordered by `chat_id` — the exact
    /// strings the top-right list renders (R6.7).
    fn titles(&self) -> Vec<(i64, String)>;

    /// Pin the current automation state for `chat_id` at the start of processing
    /// a message. The returned value is owned and stays fixed for the caller
    /// even if a toggle lands afterwards (R6.6). A room not in the catalog pins
    /// as `present == false`.
    fn pin(&self, chat_id: i64) -> RoomAutomation;
}

/// One room's mutable toggle state inside the catalog.
#[derive(Debug, Clone, PartialEq, Eq)]
struct RoomEntry {
    title: String,
    enabled: bool,
    auto_reply: bool,
    geeknews: bool,
    link_forward: bool,
    telegram_relay: bool,
}

/// The atomically-swapped catalog state: the set of rooms plus a monotonic
/// version bumped on every successful mutation.
#[derive(Debug, Clone, Default)]
struct CatalogState {
    rooms: BTreeMap<i64, RoomEntry>,
    version: u64,
}

/// An in-memory [`RoomCatalog`].
///
/// The state lives behind an `RwLock<Arc<CatalogState>>`, mirroring
/// [`crate::model_config::InMemoryModelConfigStore`]: [`Self::pin`] clones the
/// `Arc`-shared value cheaply and reads the room's toggles into an owned
/// [`RoomAutomation`], while a mutation replaces the pointer under the write
/// lock. A mutation therefore never disturbs a snapshot that was already pinned,
/// which is what makes "toggle applies from the next message" hold (R6.6).
pub struct InMemoryRoomCatalog {
    inner: RwLock<Arc<CatalogState>>,
    directory: Arc<dyn RoomDirectory + Send + Sync>,
    max_rooms: usize,
}

impl std::fmt::Debug for InMemoryRoomCatalog {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("InMemoryRoomCatalog")
            .field("state", &self.read())
            .field("max_rooms", &self.max_rooms)
            .finish_non_exhaustive()
    }
}

impl InMemoryRoomCatalog {
    /// Build an empty catalog backed by `directory` for add-time verification.
    pub fn new(directory: Arc<dyn RoomDirectory + Send + Sync>) -> Self {
        Self {
            inner: RwLock::new(Arc::new(CatalogState::default())),
            directory,
            max_rooms: MAX_CATALOG_ROOMS,
        }
    }

    /// Build a catalog seeded from previously-loaded [`CatalogRoom`]s (e.g. from
    /// [`load_catalog_rooms`]). Rooms beyond [`MAX_CATALOG_ROOMS`] and
    /// duplicate ids are dropped; the seeded state starts at version `1`.
    pub fn from_rooms(
        directory: Arc<dyn RoomDirectory + Send + Sync>,
        rooms: impl IntoIterator<Item = CatalogRoom>,
    ) -> Self {
        let mut state = CatalogState {
            rooms: BTreeMap::new(),
            version: 1,
        };
        for room in rooms {
            if room.chat_id <= 0 || state.rooms.len() >= MAX_CATALOG_ROOMS {
                continue;
            }
            let title = if room.title.trim().is_empty() {
                directory.title_of(room.chat_id).unwrap_or_default()
            } else {
                room.title.trim().to_string()
            };
            state.rooms.entry(room.chat_id).or_insert(RoomEntry {
                title,
                enabled: room.enabled,
                auto_reply: room.auto_reply,
                geeknews: room.geeknews,
                link_forward: room.link_forward,
                telegram_relay: room.telegram_relay,
            });
        }
        Self {
            inner: RwLock::new(Arc::new(state)),
            directory,
            max_rooms: MAX_CATALOG_ROOMS,
        }
    }

    fn read(&self) -> Arc<CatalogState> {
        Arc::clone(&self.inner.read().expect("room catalog lock poisoned on read"))
    }

    /// Apply `mutate` to a fresh copy of the state under the write lock, then
    /// publish it with the next version. Returning `Err` from `mutate` leaves
    /// the stored state untouched (R6.3, R6.4).
    fn mutate<F>(&self, mutate: F) -> Result<(), RoomError>
    where
        F: FnOnce(&mut CatalogState) -> Result<(), RoomError>,
    {
        let mut guard = self
            .inner
            .write()
            .expect("room catalog lock poisoned on write");
        let mut next = (**guard).clone();
        mutate(&mut next)?;
        next.version = guard.version.saturating_add(1);
        *guard = Arc::new(next);
        Ok(())
    }
}

impl RoomCatalog for InMemoryRoomCatalog {
    fn list(&self) -> Vec<CatalogRoom> {
        self.read()
            .rooms
            .iter()
            .map(|(chat_id, entry)| CatalogRoom {
                chat_id: *chat_id,
                title: entry.title.clone(),
                enabled: entry.enabled,
                auto_reply: entry.auto_reply,
                geeknews: entry.geeknews,
                link_forward: entry.link_forward,
                telegram_relay: entry.telegram_relay,
            })
            .collect()
    }

    fn add(&self, chat_id: i64) -> Result<(), RoomError> {
        // Resolve the real title before taking the write lock so an
        // absent/inaccessible room is rejected without touching the catalog.
        let title = self
            .directory
            .title_of(chat_id)
            .filter(|title| !title.trim().is_empty())
            .ok_or(RoomError::NotAccessible(chat_id))?;
        let max_rooms = self.max_rooms;
        self.mutate(move |state| {
            if state.rooms.contains_key(&chat_id) {
                return Err(RoomError::Duplicate(chat_id));
            }
            if state.rooms.len() >= max_rooms {
                return Err(RoomError::TooMany(max_rooms));
            }
            state.rooms.insert(
                chat_id,
                RoomEntry {
                    title: title.trim().to_string(),
                    enabled: true,
                    auto_reply: false,
                    geeknews: false,
                    link_forward: false,
                    telegram_relay: false,
                },
            );
            Ok(())
        })
    }

    fn remove(&self, chat_id: i64) -> Result<(), RoomError> {
        self.mutate(move |state| {
            if state.rooms.remove(&chat_id).is_none() {
                return Err(RoomError::NotInCatalog(chat_id));
            }
            Ok(())
        })
    }

    fn set_toggle(&self, chat_id: i64, kind: Toggle, on: bool) -> Result<(), RoomError> {
        self.mutate(move |state| {
            let entry = state
                .rooms
                .get_mut(&chat_id)
                .ok_or(RoomError::NotInCatalog(chat_id))?;
            match kind {
                Toggle::Enabled => entry.enabled = on,
                Toggle::AutoReply => entry.auto_reply = on,
                Toggle::GeekNews => entry.geeknews = on,
                Toggle::LinkForward => entry.link_forward = on,
                Toggle::TelegramRelay => entry.telegram_relay = on,
            }
            Ok(())
        })
    }

    fn title_of(&self, chat_id: i64) -> Option<String> {
        self.read()
            .rooms
            .get(&chat_id)
            .map(|entry| entry.title.clone())
    }

    fn titles(&self) -> Vec<(i64, String)> {
        self.read()
            .rooms
            .iter()
            .map(|(chat_id, entry)| (*chat_id, entry.title.clone()))
            .collect()
    }

    fn pin(&self, chat_id: i64) -> RoomAutomation {
        let state = self.read();
        match state.rooms.get(&chat_id) {
            Some(entry) => RoomAutomation {
                chat_id,
                present: true,
                enabled: entry.enabled,
                auto_reply: entry.auto_reply,
                geeknews: entry.geeknews,
                version: state.version,
            },
            None => RoomAutomation {
                chat_id,
                present: false,
                enabled: false,
                auto_reply: false,
                geeknews: false,
                version: state.version,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::local_db::LocalChat;
    use std::fs;
    use tempfile::tempdir;

    fn chat(id: i64, name: &str) -> LocalChat {
        LocalChat {
            chat_id: id,
            chat_type: 1,
            chat_name: name.to_string(),
            database_chat_name: None,
            active_members_count: 4,
            last_log_id: 1,
            last_updated_at: 0,
            unread_count: 0,
            display_name: name.to_string(),
        }
    }

    #[test]
    fn merge_keeps_configured_order_and_appends_catalog_auto_reply() {
        let dir = tempdir().expect("temp");
        fs::write(
            dir.path().join(CATALOG_NAME),
            r#"{"schema_version":1,"rooms":[{"chat_id":99,"auto_reply":true,"geeknews":true},{"chat_id":42,"auto_reply":false,"geeknews":true}]}"#,
        )
        .expect("write catalog");
        let chats = vec![chat(42, "부자멘토멘티"), chat(99, "kakao-test")];
        let catalog_ids = catalog_auto_reply_chat_ids(dir.path()).expect("catalog ids");
        assert_eq!(catalog_ids, vec![99]);
        let merged = merge_configured_and_catalog_selectors(
            &["bind:42:부자멘토멘티".into()],
            &catalog_ids,
            &chats,
        )
        .expect("merge");
        assert_eq!(
            merged,
            vec![
                "bind:42:부자멘토멘티".to_string(),
                "bind:99:kakao-test".to_string()
            ]
        );

        let untitled = chat(77, "");
        let named = merge_configured_and_catalog_selectors_named(
            &["bind:42:부자멘토멘티".into()],
            &[77],
            &[chat(42, "부자멘토멘티"), untitled],
            &[(77, "kakao-test".into())],
        )
        .expect("named merge");
        assert_eq!(
            named,
            vec![
                "bind:42:부자멘토멘티".to_string(),
                "bind:77:kakao-test".to_string()
            ]
        );
    }

    fn directory() -> Arc<dyn RoomDirectory + Send + Sync> {
        Arc::new(MapRoomDirectory::new([
            (42, "부자멘토멘티".to_string()),
            (99, "kakao-test".to_string()),
            (7, "개발자 모임".to_string()),
        ]))
    }

    #[test]
    fn add_captures_real_title_and_defaults_to_master_on_features_off() {
        let catalog = InMemoryRoomCatalog::new(directory());
        catalog.add(42).expect("add accessible room");
        let rooms = catalog.list();
        assert_eq!(rooms.len(), 1);
        assert_eq!(rooms[0].chat_id, 42);
        // Top-right list shows exactly the real room title (R6.7).
        assert_eq!(rooms[0].title, "부자멘토멘티");
        assert!(rooms[0].enabled);
        assert!(!rooms[0].auto_reply);
        assert!(!rooms[0].geeknews);
        assert_eq!(catalog.title_of(42).as_deref(), Some("부자멘토멘티"));
        assert_eq!(catalog.titles(), vec![(42, "부자멘토멘티".to_string())]);
    }

    #[test]
    fn duplicate_add_is_rejected_and_list_unchanged() {
        let catalog = InMemoryRoomCatalog::new(directory());
        catalog.add(42).expect("first add");
        let before = catalog.list();
        assert_eq!(catalog.add(42), Err(RoomError::Duplicate(42)));
        // The existing list is preserved (R6.3).
        assert_eq!(catalog.list(), before);
    }

    #[test]
    fn adding_absent_room_fails_and_list_unchanged() {
        let catalog = InMemoryRoomCatalog::new(directory());
        assert_eq!(catalog.add(123456), Err(RoomError::NotAccessible(123456)));
        // Nothing was added (R6.4).
        assert!(catalog.list().is_empty());
    }

    #[test]
    fn remove_stops_automation_immediately() {
        let catalog = InMemoryRoomCatalog::new(directory());
        catalog.add(42).expect("add");
        catalog
            .set_toggle(42, Toggle::AutoReply, true)
            .expect("enable reply");
        assert!(catalog.pin(42).auto_reply_active());
        catalog.remove(42).expect("remove");
        // Immediately gone from the list and inactive (R6.5).
        assert!(catalog.list().is_empty());
        assert_eq!(catalog.title_of(42), None);
        let pinned = catalog.pin(42);
        assert!(!pinned.present);
        assert!(!pinned.auto_reply_active());
    }

    #[test]
    fn removing_absent_room_is_rejected() {
        let catalog = InMemoryRoomCatalog::new(directory());
        assert_eq!(catalog.remove(42), Err(RoomError::NotInCatalog(42)));
    }

    #[test]
    fn toggle_on_absent_room_is_rejected() {
        let catalog = InMemoryRoomCatalog::new(directory());
        assert_eq!(
            catalog.set_toggle(42, Toggle::Enabled, true),
            Err(RoomError::NotInCatalog(42))
        );
    }

    #[test]
    fn master_switch_gates_features() {
        let catalog = InMemoryRoomCatalog::new(directory());
        catalog.add(42).expect("add");
        catalog
            .set_toggle(42, Toggle::AutoReply, true)
            .expect("reply on");
        catalog
            .set_toggle(42, Toggle::GeekNews, true)
            .expect("geeknews on");
        assert!(catalog.pin(42).auto_reply_active());
        assert!(catalog.pin(42).geeknews_active());
        // Turning off the master switch disables both features (R6.6 semantics).
        catalog
            .set_toggle(42, Toggle::Enabled, false)
            .expect("master off");
        assert!(!catalog.pin(42).auto_reply_active());
        assert!(!catalog.pin(42).geeknews_active());
    }

    #[test]
    fn toggle_applies_from_next_pin_not_in_flight() {
        let catalog = InMemoryRoomCatalog::new(directory());
        catalog.add(42).expect("add");
        // A message begins processing: pin the snapshot.
        let in_flight = catalog.pin(42);
        assert!(!in_flight.auto_reply);
        // The operator flips the reply toggle while that message is in flight.
        catalog
            .set_toggle(42, Toggle::AutoReply, true)
            .expect("reply on");
        // The in-flight snapshot is unchanged (R6.6).
        assert!(!in_flight.auto_reply);
        assert!(!in_flight.auto_reply_active());
        // A message that starts after the change sees the new value.
        assert!(catalog.pin(42).auto_reply_active());
    }

    #[test]
    fn from_rooms_seeds_and_backfills_titles() {
        let seed = vec![
            CatalogRoom {
                chat_id: 42,
                title: String::new(),
                enabled: true,
                auto_reply: true,
                geeknews: false,
                link_forward: false,
                telegram_relay: false,
            },
            CatalogRoom {
                chat_id: 99,
                title: "override".to_string(),
                enabled: false,
                auto_reply: false,
                geeknews: true,
                link_forward: false,
                telegram_relay: false,
            },
        ];
        let catalog = InMemoryRoomCatalog::from_rooms(directory(), seed);
        // Blank title backfilled from the directory (R6.7); explicit title kept.
        assert_eq!(catalog.title_of(42).as_deref(), Some("부자멘토멘티"));
        assert_eq!(catalog.title_of(99).as_deref(), Some("override"));
        let pinned = catalog.pin(42);
        assert!(pinned.auto_reply_active());
        assert!(!catalog.pin(99).enabled);
    }

    #[test]
    fn directory_from_local_chats_uses_real_titles() {
        let chats = vec![chat(42, "부자멘토멘티"), chat(77, "")];
        let dir = MapRoomDirectory::from_local_chats(&chats, &[(77, "kakao-test".into())]);
        assert_eq!(dir.title_of(42).as_deref(), Some("부자멘토멘티"));
        assert_eq!(dir.title_of(77).as_deref(), Some("kakao-test"));
        assert_eq!(dir.title_of(1), None);
    }
}
