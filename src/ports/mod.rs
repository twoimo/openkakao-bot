//! External boundary traits ("adapter shims") for the live-ops features.
//!
//! Every place where new code touches the outside world — the local KakaoTalk
//! database, the AX local-send path, the Telegram desktop window, the network,
//! image acquisition, and the macOS keychain — is expressed as a trait here.
//! New code depends only on these traits and cannot tell a real adapter apart
//! from a fake one, which is what lets the verification harness assemble a
//! fully fake, network-forbidden world (see [`crate::fakes`]).
//!
//! This module is the deliverable of task 1 ("어댑터 시임"). Task 2 threaded the
//! [`crate::safety::SendTicket`] through [`SendPort`]: every send method now
//! requires a ticket, which only `src/safety` can mint, so a send that skips
//! the gate fails to compile (R1.1, R1.11, R12.2).

use thiserror::Error;

use crate::dataset::DatasetMessage;
use crate::safety::SendTicket;

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A failure crossing one of the outward ports (`MessageSource`, `SendPort`,
/// `NetworkPort`).
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum PortError {
    /// The verification harness forbids all outbound network traffic. The
    /// forbidden-network adapter returns this instead of ever transmitting
    /// (R1.12, R12.9).
    #[error("이 경로에서는 외부 네트워크 요청이 금지되어 있어요")]
    NetworkForbidden,
    /// A call reached the blocked real-send path and was refused without
    /// sending anything (R1.2).
    #[error("실제 전송 경로는 검증 중에 차단되어 있어요")]
    RealSendBlocked,
    /// The requested item was not found.
    #[error("대상을 찾지 못했어요: {0}")]
    NotFound(String),
    /// The backing store or transport failed.
    #[error("경계 어댑터 오류: {0}")]
    Backend(String),
}

/// A failure reading the Telegram desktop window through Accessibility.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum AxError {
    /// The Telegram window could not be located.
    #[error("텔레그램 창을 찾지 못했어요")]
    WindowNotFound,
    /// Reading the window failed.
    #[error("텔레그램 창을 읽지 못했어요: {0}")]
    ReadFailed(String),
    /// The Accessibility permission is not granted (R9.10).
    #[error("손쉬운 사용 권한이 없어요")]
    PermissionDenied,
}

/// A failure acquiring a single image (one rung of the image ladder, R7.5).
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum AcquireError {
    /// This rung cannot serve the reference (try the next rung).
    #[error("이 방법으로는 이미지를 확보할 수 없어요")]
    NotAvailable,
    /// The rung is missing its OS permission (e.g. screen recording, R11.12).
    #[error("이미지 확보에 필요한 권한이 없어요")]
    PermissionDenied,
    /// The rung produced an empty payload (treated as failure, R7.6).
    #[error("확보한 이미지가 비어 있어요")]
    Empty,
    /// Some other acquisition failure.
    #[error("이미지를 확보하지 못했어요: {0}")]
    Failed(String),
}

/// A failure reading a secret from the keychain.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum SecretError {
    /// No secret is stored under the requested key.
    #[error("요청한 비밀값이 없어요")]
    NotFound,
    /// The keychain refused access.
    #[error("비밀값 접근이 거부되었어요")]
    AccessDenied,
    /// The keychain backend failed.
    #[error("비밀값 보관소 오류: {0}")]
    Backend(String),
}

// ---------------------------------------------------------------------------
// Value types
// ---------------------------------------------------------------------------

/// A reference to a chat room drawn from the local database.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoomRef {
    /// Database-authoritative chat id.
    pub chat_id: i64,
    /// Display title. May be empty for unnamed group rooms.
    pub title: String,
}

/// An opaque, monotonically ordered position in a room's message stream.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub struct MessageCursor {
    /// Opaque send timestamp of the last consumed message.
    pub at: i64,
    /// Tie-breaking log id for messages sharing an `at`.
    pub log_id: i64,
}

/// Database-authoritative identity/target/state for one chat. This is the only
/// source used to assemble a `SafetyConfig`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendAuthority {
    /// The chat id this authority describes.
    pub chat_id: i64,
    /// The chat type code (0 = direct, etc.).
    pub chat_type: i64,
    /// True when this is the "memo" chat (나와의 채팅), the only memo-grade target.
    pub is_memo: bool,
    /// The database-authoritative owner display name, if known (R10.2).
    pub owner_display_name: Option<String>,
}

/// A candidate owner display name surfaced from the database (R10.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnerCandidate {
    /// The display name of the candidate.
    pub display_name: String,
    /// How many messages this candidate authored (used to rank candidates).
    pub message_count: i64,
}

/// The receipt returned by a confirmed send. Only commit/success receipts are
/// counted toward sample progress (R2.1).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendReceipt {
    /// The chat the send was confirmed to.
    pub chat_id: i64,
    /// Logical timestamp of the confirmation.
    pub sent_at_ms: i64,
}

/// An in-memory image payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageBlob {
    /// The raw image bytes.
    pub bytes: Vec<u8>,
    /// The MIME type (e.g. `image/png`).
    pub mime: String,
}

/// A reference to an image that still needs to be acquired.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageRef {
    /// An opaque locator for the source image (never a secret).
    pub locator: String,
}

/// One outbound HTTP request crossing the network boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpRequest {
    /// HTTP method.
    pub method: String,
    /// Fully-qualified request URL.
    pub url: String,
    /// Request body bytes.
    pub body: Vec<u8>,
}

/// One HTTP response.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpResponse {
    /// HTTP status code.
    pub status: u16,
    /// Response body bytes.
    pub body: Vec<u8>,
}

/// How the Telegram AX reader detects new messages (R7.13).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DetectMode {
    /// Subscribe to the notification stream.
    Notification,
    /// Poll the window on an interval.
    Polling {
        /// Poll interval in milliseconds.
        interval_ms: u64,
    },
}

/// A position in the Telegram relay stream, ordered by `(at, display_order)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
pub struct RelayCursor {
    /// Opaque timestamp of the last relayed message.
    pub at: i64,
    /// Tie-breaking display order for same-`at` messages (R7.1).
    pub display_order: i64,
}

/// One link inside a Telegram message (R7.3, R7.4).
///
/// Link extraction prefers the real URL over the on-screen display text; when
/// the real address cannot be recovered, only the display text is available and
/// the relay flags it as unconfirmed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LinkRef {
    /// The real URL was extracted (preferred, R7.3).
    Resolved(String),
    /// Only the on-screen display text could be read; the real address is
    /// unconfirmed (R7.4).
    DisplayOnly(String),
}

/// An attachment kind that is excluded from relaying (R7.19). Text, links, and
/// images relay; everything else is dropped and its kind/count is surfaced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExcludedAttachment {
    /// A video attachment.
    Video,
    /// A file attachment.
    File,
    /// A voice message.
    Voice,
    /// A sticker.
    Sticker,
}

/// One message read out of the Telegram desktop window.
///
/// The relay reads text, links, and images out of a message and excludes
/// everything else (R7.19). Links arrive as [`LinkRef`] so a display-only link
/// can be distinguished from a resolved URL (R7.3, R7.4); images arrive as
/// [`ImageRef`] locators to be acquired through the shared image ladder (R7.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TelegramMessage {
    /// A stable per-message id (never stored verbatim downstream).
    pub pid: String,
    /// Opaque send timestamp.
    pub at: i64,
    /// Order among messages sharing `at`, preserving same-instant order (R7.1).
    pub display_order: i64,
    /// The message text.
    pub text: String,
    /// The links in the message, in original appearance order (R7.3).
    pub links: Vec<LinkRef>,
    /// The image references in the message, in original appearance order (R7.5).
    pub images: Vec<ImageRef>,
    /// Non-relayable attachments present on the message (R7.19).
    pub excluded: Vec<ExcludedAttachment>,
}

/// Which rung of the image-acquisition ladder an [`ImageAcquirer`] implements
/// (R7.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ImageAcquisition {
    /// Save the original attachment.
    OriginalSave,
    /// Read from the local cache folder.
    CacheFolder,
    /// Capture the screen (needs the screen-recording permission).
    ScreenCapture,
}

/// A key naming a secret in the keychain. Values are only ever returned as
/// [`Secret`]; the key itself carries no secret material.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SecretKey {
    /// The Telegram bot/API token.
    TelegramBotToken,
    /// The relay reader session token.
    RelaySession,
    /// The collector session token.
    CollectorSession,
}

// ---------------------------------------------------------------------------
// Secret
// ---------------------------------------------------------------------------

/// A secret value that never shows its contents (R11.6).
///
/// The inner string is zeroized on drop. [`Secret`] deliberately implements
/// **no** `Display` and **no** `Serialize`, and its `Debug` prints only
/// `Secret(***)`, so a secret cannot leak into logs, configuration, JSON, or
/// the screen by accident. The only way to read the value is the explicit
/// [`Secret::expose_secret`].
pub struct Secret(zeroize::Zeroizing<String>);

impl Secret {
    /// Wrap a raw secret string.
    pub fn new(value: impl Into<String>) -> Self {
        Secret(zeroize::Zeroizing::new(value.into()))
    }

    /// Explicitly read the secret. Named loudly so every read site is obvious
    /// at a glance; there is no implicit conversion path.
    pub fn expose_secret(&self) -> &str {
        self.0.as_str()
    }
}

impl std::fmt::Debug for Secret {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Secret(***)")
    }
}
// No Display / Serialize impls — a secret cannot be rendered or serialized.

// ---------------------------------------------------------------------------
// Boundary traits
// ---------------------------------------------------------------------------

/// Logical time. Production uses the system clock; verification uses
/// [`crate::fakes::VirtualClock`].
pub trait Clock {
    /// The current logical time in milliseconds.
    fn now_ms(&self) -> i64;
}

/// A cooperative delay. The verification harness never actually sleeps — it
/// only advances the [`VirtualClock`](crate::fakes::VirtualClock).
pub trait Sleeper {
    /// Wait for `ms` milliseconds of logical time.
    fn sleep_ms(&self, ms: u64);
}

/// The local KakaoTalk database read boundary (SQLCipher).
pub trait MessageSource {
    /// Every room visible in the local database.
    fn rooms(&self) -> Result<Vec<RoomRef>, PortError>;

    /// Messages strictly after `after`, in ascending time order, capped at
    /// `limit`. `None` means "from the beginning".
    fn messages_after(
        &self,
        after: Option<&MessageCursor>,
        limit: usize,
    ) -> Result<Vec<DatasetMessage>, PortError>;

    /// The database-authoritative identity/target/state for `chat_id`. The only
    /// source used to assemble a `SafetyConfig`.
    fn authority(&self, chat_id: i64) -> Result<SendAuthority, PortError>;

    /// Up to `limit` candidate owner display names (R10.2).
    fn owner_candidates(&self, limit: usize) -> Result<Vec<OwnerCandidate>, PortError>;
}

/// The AX local-send boundary — the only place a real send can leave the
/// device.
///
/// Every method requires a [`SendTicket`], which is mintable only inside
/// `src/safety` via [`SendGuard::authorize`](crate::safety::SendGuard::authorize).
/// A send path that has not passed the gate cannot obtain a ticket, so gate
/// bypass fails to compile. The ticket carries its own target, so these methods
/// take no separate `chat_id`.
pub trait SendPort {
    /// Send a text body to the ticket's chat. `Ok` means the send was
    /// confirmed.
    fn send_text(&self, ticket: &SendTicket, body: &str) -> Result<SendReceipt, PortError>;

    /// Send an image to the ticket's chat. `Ok` means the send was confirmed.
    fn send_image(&self, ticket: &SendTicket, image: &ImageBlob) -> Result<SendReceipt, PortError>;

    /// Whether this is a fake implementation. The durability harness preflight
    /// checks this before starting (R1.11).
    fn is_fake(&self) -> bool;
}

/// The Telegram desktop window read boundary (macOS Accessibility).
pub trait AxReadPort {
    /// How new messages are detected.
    fn detect_mode(&self) -> DetectMode;

    /// Messages after `cursor`, ascending, capped at `limit`.
    fn messages_after(
        &self,
        cursor: &RelayCursor,
        limit: usize,
    ) -> Result<Vec<TelegramMessage>, AxError>;

    /// Whether this is a fake implementation (R1.11).
    fn is_fake(&self) -> bool;
}

/// The boundary crossed by every request leaving the device. Counts attempts.
pub trait NetworkPort {
    /// Perform an outbound request.
    fn request(&self, req: HttpRequest) -> Result<HttpResponse, PortError>;

    /// How many outbound requests have been attempted so far.
    fn egress_count(&self) -> usize;
}

/// One rung of the image-acquisition ladder (R7.5).
pub trait ImageAcquirer {
    /// Which rung this is.
    fn kind(&self) -> ImageAcquisition;

    /// Attempt to acquire the image referenced by `r`.
    fn acquire(&self, r: &ImageRef) -> Result<ImageBlob, AcquireError>;
}

/// The secret store (macOS keychain). Values only ever leave as [`Secret`].
pub trait SecretStore {
    /// Read the secret stored under `key`.
    fn get(&self, key: SecretKey) -> Result<Secret, SecretError>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn secret_debug_is_masked_and_no_leak() {
        let s = Secret::new("super-secret-token");
        // Debug must never reveal the value.
        assert_eq!(format!("{s:?}"), "Secret(***)");
        assert!(!format!("{s:?}").contains("super-secret-token"));
        // The value is only reachable through the explicit accessor.
        assert_eq!(s.expose_secret(), "super-secret-token");
    }

    #[test]
    fn port_error_network_forbidden_has_message() {
        let msg = PortError::NetworkForbidden.to_string();
        assert!(!msg.is_empty());
    }
}
