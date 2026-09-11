//! Seed-based deterministic fakes and the verification harness world.
//!
//! Everything here implements a boundary trait from [`crate::ports`] (or one of
//! the existing seams in `experiment`/`geeknews`) with in-memory, deterministic
//! behavior. Assembling a world out of these fakes is the whole point: real
//! KakaoTalk sends stay at zero because the harness only ever wires fakes, the
//! [`ForbiddenNetwork`] refuses every outbound request, and the
//! [`BlockingRealSendPort`] tripwire counts any call that tried to reach the
//! real send path.
//!
//! Task 1 supplies the base fakes ([`FakeMessageSource`], [`FakeSendPort`],
//! [`FakeAxReadPort`], [`FakeProvider`], [`FakeFeedSource`]). Task 1.1 adds the
//! forbidden-network and blocked-real-send tripwires, the [`VirtualClock`] /
//! [`RecordingSleeper`] logical-time pair, and the [`FakePorts`] /
//! [`ScenarioShape`] world builder (R1.9, R1.12, R12.2, R12.17).

use std::cell::RefCell;
use std::sync::atomic::{AtomicI64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::dataset::{DatasetMessage, MessageKind};
use crate::experiment::{Answer, ModelId, Prompt, Provider, ProviderError};
use crate::geeknews::{FeedError, FeedSource};
use crate::ports::{
    AxError, AxReadPort, Clock, DetectMode, HttpRequest, HttpResponse, ImageBlob, MessageCursor,
    MessageSource, NetworkPort, OwnerCandidate, PortError, RelayCursor, RoomRef, SendAuthority,
    SendPort, SendReceipt, Sleeper, TelegramMessage,
};
use crate::safety::SendTicket;

// ===========================================================================
// Base fakes (task 1)
// ===========================================================================

/// A scripted room: its authority row plus the messages the source will yield.
#[derive(Debug, Clone)]
pub struct ScriptedRoom {
    /// The room reference.
    pub room: RoomRef,
    /// The database-authoritative authority for this room.
    pub authority: SendAuthority,
    /// The messages this room yields, ascending by `at`.
    pub messages: Vec<DatasetMessage>,
}

/// A deterministic [`MessageSource`] built from a seed and a scenario shape.
#[derive(Debug, Clone)]
pub struct FakeMessageSource {
    seed: u64,
    rooms: Vec<RoomRef>,
    script: Vec<ScriptedRoom>,
    owner_candidates: Vec<OwnerCandidate>,
}

impl FakeMessageSource {
    /// Build from explicit scripted rooms.
    pub fn new(seed: u64, script: Vec<ScriptedRoom>, owner_candidates: Vec<OwnerCandidate>) -> Self {
        let rooms = script.iter().map(|r| r.room.clone()).collect();
        Self {
            seed,
            rooms,
            script,
            owner_candidates,
        }
    }

    /// The seed this source was built from (determinism aid).
    pub fn seed(&self) -> u64 {
        self.seed
    }

    /// Always true — this is a fake.
    pub fn is_fake(&self) -> bool {
        true
    }

    fn room_script(&self, chat_id: i64) -> Option<&ScriptedRoom> {
        self.script.iter().find(|r| r.room.chat_id == chat_id)
    }
}

impl MessageSource for FakeMessageSource {
    fn rooms(&self) -> Result<Vec<RoomRef>, PortError> {
        Ok(self.rooms.clone())
    }

    fn messages_after(
        &self,
        after: Option<&MessageCursor>,
        limit: usize,
    ) -> Result<Vec<DatasetMessage>, PortError> {
        // Merge every room's messages in ascending (at, chat_id) order, then
        // keep only those strictly after the cursor.
        let mut all: Vec<DatasetMessage> = self
            .script
            .iter()
            .flat_map(|r| r.messages.iter().cloned())
            .collect();
        all.sort_by(|a, b| a.at.cmp(&b.at).then(a.chat_id.cmp(&b.chat_id)));
        let filtered = all
            .into_iter()
            .filter(|m| match after {
                Some(c) => m.at > c.at,
                None => true,
            })
            .take(limit)
            .collect();
        Ok(filtered)
    }

    fn authority(&self, chat_id: i64) -> Result<SendAuthority, PortError> {
        self.room_script(chat_id)
            .map(|r| r.authority.clone())
            .ok_or_else(|| PortError::NotFound(format!("chat {chat_id}")))
    }

    fn owner_candidates(&self, limit: usize) -> Result<Vec<OwnerCandidate>, PortError> {
        Ok(self.owner_candidates.iter().take(limit).cloned().collect())
    }
}

/// One recorded send made against a [`FakeSendPort`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FakeSend {
    /// A text send.
    Text {
        /// Target chat id.
        chat_id: i64,
        /// The body that would have been sent.
        body: String,
    },
    /// An image send.
    Image {
        /// Target chat id.
        chat_id: i64,
        /// Byte length of the image that would have been sent.
        bytes: usize,
    },
}

/// A deterministic, in-memory [`SendPort`] that records calls instead of
/// sending. Optionally fails every `fail_every`-th call to exercise failure
/// paths.
#[derive(Debug, Default)]
pub struct FakeSendPort {
    calls: RefCell<Vec<FakeSend>>,
    fail_every: Option<usize>,
    clock_ms: AtomicI64,
}

impl FakeSendPort {
    /// A send port that always confirms.
    pub fn new() -> Self {
        Self {
            calls: RefCell::new(Vec::new()),
            fail_every: None,
            clock_ms: AtomicI64::new(0),
        }
    }

    /// A send port that fails every `n`-th call (`n >= 1`).
    pub fn failing_every(n: usize) -> Self {
        Self {
            calls: RefCell::new(Vec::new()),
            fail_every: (n >= 1).then_some(n),
            clock_ms: AtomicI64::new(0),
        }
    }

    /// How many confirmed sends were recorded.
    pub fn confirmed_count(&self) -> usize {
        self.calls.borrow().len()
    }

    /// A snapshot of every recorded send.
    pub fn calls(&self) -> Vec<FakeSend> {
        self.calls.borrow().clone()
    }

    fn next_receipt(&self, chat_id: i64) -> SendReceipt {
        let at = self.clock_ms.fetch_add(1, Ordering::SeqCst) + 1;
        SendReceipt {
            chat_id,
            sent_at_ms: at,
        }
    }

    fn should_fail(&self) -> bool {
        match self.fail_every {
            // Count the current (not-yet-recorded) attempt.
            Some(n) => (self.calls.borrow().len() + 1).is_multiple_of(n),
            None => false,
        }
    }
}

impl SendPort for FakeSendPort {
    fn send_text(&self, ticket: &SendTicket, body: &str) -> Result<SendReceipt, PortError> {
        if self.should_fail() {
            return Err(PortError::Backend("scripted send failure".into()));
        }
        let chat_id = ticket.chat_id();
        self.calls.borrow_mut().push(FakeSend::Text {
            chat_id,
            body: body.to_string(),
        });
        Ok(self.next_receipt(chat_id))
    }

    fn send_image(&self, ticket: &SendTicket, image: &ImageBlob) -> Result<SendReceipt, PortError> {
        if self.should_fail() {
            return Err(PortError::Backend("scripted send failure".into()));
        }
        let chat_id = ticket.chat_id();
        self.calls.borrow_mut().push(FakeSend::Image {
            chat_id,
            bytes: image.bytes.len(),
        });
        Ok(self.next_receipt(chat_id))
    }

    fn is_fake(&self) -> bool {
        true
    }
}

/// A deterministic [`AxReadPort`] that serves canned pages of Telegram messages.
#[derive(Debug, Clone, Default)]
pub struct FakeAxReadPort {
    messages: Vec<TelegramMessage>,
    mode: Option<DetectMode>,
}

impl FakeAxReadPort {
    /// Build from a flat, unsorted list of messages.
    pub fn new(mut messages: Vec<TelegramMessage>) -> Self {
        messages.sort_by(|a, b| a.at.cmp(&b.at).then(a.display_order.cmp(&b.display_order)));
        Self {
            messages,
            mode: None,
        }
    }

    /// Override the reported detect mode (defaults to `Notification`).
    pub fn with_mode(mut self, mode: DetectMode) -> Self {
        self.mode = Some(mode);
        self
    }
}

impl AxReadPort for FakeAxReadPort {
    fn detect_mode(&self) -> DetectMode {
        self.mode.unwrap_or(DetectMode::Notification)
    }

    fn messages_after(
        &self,
        cursor: &RelayCursor,
        limit: usize,
    ) -> Result<Vec<TelegramMessage>, AxError> {
        let out = self
            .messages
            .iter()
            .filter(|m| (m.at, m.display_order) > (cursor.at, cursor.display_order))
            .take(limit)
            .cloned()
            .collect();
        Ok(out)
    }

    fn is_fake(&self) -> bool {
        true
    }
}

/// What a [`FakeProvider`] does when asked to generate an answer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptedOutcome {
    /// Return this reply text.
    Reply(String),
    /// Fail generation.
    GenerateFail,
    /// Time out.
    Timeout,
}

/// A deterministic [`Provider`] — no network, no LLM.
#[derive(Debug, Clone)]
pub struct FakeProvider {
    id: String,
    models: Vec<ModelId>,
    latency_ms: u64,
    outcome: ScriptedOutcome,
}

impl FakeProvider {
    /// Build a provider that serves `models` and answers with `outcome`.
    pub fn new(
        id: impl Into<String>,
        models: Vec<ModelId>,
        latency_ms: u64,
        outcome: ScriptedOutcome,
    ) -> Self {
        Self {
            id: id.into(),
            models,
            latency_ms,
            outcome,
        }
    }
}

impl Provider for FakeProvider {
    fn id(&self) -> &str {
        &self.id
    }

    fn list_models(&self) -> Result<Vec<ModelId>, ProviderError> {
        Ok(self.models.clone())
    }

    fn generate(&self, _model: &ModelId, prompt: &Prompt) -> Result<Answer, ProviderError> {
        match &self.outcome {
            ScriptedOutcome::Reply(text) => Ok(Answer {
                // Deterministically fold the question id in so different prompts
                // yield different-but-stable answers.
                text: format!("{text} [{}]", prompt.question_id),
                latency_ms: self.latency_ms,
            }),
            ScriptedOutcome::GenerateFail => {
                Err(ProviderError::GenerateFailed("scripted failure".into()))
            }
            ScriptedOutcome::Timeout => Err(ProviderError::Timeout),
        }
    }
}

/// A deterministic [`FeedSource`] that returns canned Atom XML with no network.
#[derive(Debug, Clone, Default)]
pub struct FakeFeedSource {
    xml: String,
}

impl FakeFeedSource {
    /// Build from raw Atom XML (empty string = "no items").
    pub fn new(xml: impl Into<String>) -> Self {
        Self { xml: xml.into() }
    }
}

impl FeedSource for FakeFeedSource {
    fn fetch_xml(&self) -> Result<String, FeedError> {
        Ok(self.xml.clone())
    }
}

// ===========================================================================
// Task 1.1 — forbidden network, blocked real send, virtual time
// ===========================================================================

/// The forbidden-network harness: it never transmits, counts every attempt, and
/// returns [`PortError::NetworkForbidden`]. During bulk verification, dataset
/// refresh, and self-improvement, outbound egress must stay at zero
/// (R1.12, R12.9). Uses an atomic counter so it is `Sync` and can be shared
/// across the harness worker pool.
#[derive(Debug, Default)]
pub struct ForbiddenNetwork {
    attempts: AtomicUsize,
}

impl ForbiddenNetwork {
    /// A fresh forbidden network with zero attempts.
    pub fn new() -> Self {
        Self::default()
    }
}

impl NetworkPort for ForbiddenNetwork {
    fn request(&self, _req: HttpRequest) -> Result<HttpResponse, PortError> {
        // Count the attempt but never send.
        self.attempts.fetch_add(1, Ordering::SeqCst);
        Err(PortError::NetworkForbidden)
    }

    fn egress_count(&self) -> usize {
        self.attempts.load(Ordering::SeqCst)
    }
}

/// The blocked-real-send tripwire: any call that reaches the real send path is
/// refused without sending and only bumps the `blocked` counter (R1.2). Every
/// property test asserts this counter stays at zero — the observation layer of
/// the three-layer "zero real sends" defense.
#[derive(Debug, Default)]
pub struct BlockingRealSendPort {
    blocked: AtomicUsize,
}

impl BlockingRealSendPort {
    /// A fresh tripwire with a zero counter.
    pub fn new() -> Self {
        Self::default()
    }

    /// How many calls tried to reach the real send path.
    pub fn blocked_count(&self) -> usize {
        self.blocked.load(Ordering::SeqCst)
    }
}

impl SendPort for BlockingRealSendPort {
    fn send_text(&self, _ticket: &SendTicket, _body: &str) -> Result<SendReceipt, PortError> {
        self.blocked.fetch_add(1, Ordering::SeqCst);
        Err(PortError::RealSendBlocked)
    }

    fn send_image(&self, _ticket: &SendTicket, _image: &ImageBlob) -> Result<SendReceipt, PortError> {
        self.blocked.fetch_add(1, Ordering::SeqCst);
        Err(PortError::RealSendBlocked)
    }

    fn is_fake(&self) -> bool {
        // It stands in for the *real* send path; it is not a fake output.
        false
    }
}

/// A logical clock the harness advances by hand. Cloning shares the same
/// timeline so a [`RecordingSleeper`] can advance the clock a caller reads.
#[derive(Debug, Clone, Default)]
pub struct VirtualClock {
    now_ms: Arc<AtomicI64>,
}

impl VirtualClock {
    /// A clock starting at `start_ms`.
    pub fn new(start_ms: i64) -> Self {
        Self {
            now_ms: Arc::new(AtomicI64::new(start_ms)),
        }
    }

    /// Advance logical time by `ms` milliseconds.
    pub fn advance_ms(&self, ms: i64) {
        self.now_ms.fetch_add(ms, Ordering::SeqCst);
    }

    /// Set logical time to an absolute value.
    pub fn set_ms(&self, ms: i64) {
        self.now_ms.store(ms, Ordering::SeqCst);
    }
}

impl Clock for VirtualClock {
    fn now_ms(&self) -> i64 {
        self.now_ms.load(Ordering::SeqCst)
    }
}

/// A [`Sleeper`] that never actually sleeps: it records each requested duration
/// and advances the shared [`VirtualClock`] instead, so interval/jitter
/// invariants are checked on a virtual timeline without spending wall-clock
/// time.
#[derive(Debug)]
pub struct RecordingSleeper {
    clock: VirtualClock,
    slept: Mutex<Vec<u64>>,
}

impl RecordingSleeper {
    /// Build a sleeper bound to `clock`.
    pub fn new(clock: VirtualClock) -> Self {
        Self {
            clock,
            slept: Mutex::new(Vec::new()),
        }
    }

    /// Every recorded sleep duration, in call order.
    pub fn records(&self) -> Vec<u64> {
        self.slept.lock().expect("sleeper lock").clone()
    }

    /// Total logical time slept.
    pub fn total_slept_ms(&self) -> u128 {
        self.slept
            .lock()
            .expect("sleeper lock")
            .iter()
            .map(|&ms| ms as u128)
            .sum()
    }
}

impl Sleeper for RecordingSleeper {
    fn sleep_ms(&self, ms: u64) {
        self.slept.lock().expect("sleeper lock").push(ms);
        self.clock.advance_ms(ms as i64);
    }
}

// ===========================================================================
// Task 1.1 — scenario shape and the assembled fake world
// ===========================================================================

/// Bounds on a generated scenario. Every field is clamped to its ceiling so a
/// generator cannot produce a world larger than the harness supports.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ScenarioShape {
    /// Number of rooms (≤ [`ScenarioShape::MAX_ROOMS`]).
    pub rooms: usize,
    /// Messages per room (≤ [`ScenarioShape::MAX_MESSAGES_PER_ROOM`]).
    pub messages_per_room: usize,
    /// Images per message (≤ [`ScenarioShape::MAX_IMAGES_PER_MESSAGE`]).
    pub images_per_message: usize,
    /// Safety-config combinations to cover (≤ [`ScenarioShape::MAX_SAFETY_COMBO`]).
    pub safety_combo: usize,
}

impl ScenarioShape {
    /// Ceiling on rooms.
    pub const MAX_ROOMS: usize = 32;
    /// Ceiling on messages per room.
    pub const MAX_MESSAGES_PER_ROOM: usize = 500;
    /// Ceiling on images per message.
    pub const MAX_IMAGES_PER_MESSAGE: usize = 20;
    /// Ceiling on safety-config combinations.
    pub const MAX_SAFETY_COMBO: usize = 64;

    /// Build a shape, clamping each field to its ceiling.
    pub fn new(
        rooms: usize,
        messages_per_room: usize,
        images_per_message: usize,
        safety_combo: usize,
    ) -> Self {
        Self {
            rooms: rooms.min(Self::MAX_ROOMS),
            messages_per_room: messages_per_room.min(Self::MAX_MESSAGES_PER_ROOM),
            images_per_message: images_per_message.min(Self::MAX_IMAGES_PER_MESSAGE),
            safety_combo: safety_combo.min(Self::MAX_SAFETY_COMBO),
        }
    }
}

impl Default for ScenarioShape {
    fn default() -> Self {
        Self::new(4, 20, 2, 8)
    }
}

/// A fully-assembled fake world: every boundary is a fake, the network is
/// forbidden, time is virtual, and all filesystem state lives under a sandbox
/// [`tempfile::TempDir`]. This is what the verification harness wires so real
/// KakaoTalk sends stay at zero (R1.9, R12.2, R12.17).
#[derive(Debug)]
pub struct FakePorts {
    /// The fake local-database read boundary.
    pub messages: FakeMessageSource,
    /// The fake send boundary used by the harness.
    pub send: FakeSendPort,
    /// The real-send tripwire; its counter must stay at zero.
    pub blocked_real_send: BlockingRealSendPort,
    /// The fake Telegram window read boundary.
    pub ax_read: FakeAxReadPort,
    /// The fake LLM providers.
    pub providers: Vec<FakeProvider>,
    /// The fake GeekNews feed.
    pub feed: FakeFeedSource,
    /// The forbidden network; its egress count must stay at zero.
    pub net: ForbiddenNetwork,
    /// The virtual clock.
    pub clock: VirtualClock,
    /// The non-sleeping, time-advancing sleeper.
    pub sleeper: RecordingSleeper,
    /// The sandbox root; every path the harness writes lives under here.
    pub sandbox: tempfile::TempDir,
}

impl FakePorts {
    /// Deterministically assemble a fake world from `seed` and `shape`.
    ///
    /// The same `(seed, shape)` always produces the same rooms, messages, and
    /// owner candidates, so verification is reproducible.
    pub fn from_seed(seed: u64, shape: ScenarioShape) -> Self {
        let mut rng = StdRng::seed_from_u64(seed);

        let mut script = Vec::with_capacity(shape.rooms);
        let mut owner_candidates = Vec::new();
        let owner_name = format!("owner-{}", seed % 1000);

        let mut at_counter: i64 = 1;
        for room_idx in 0..shape.rooms {
            let chat_id = (room_idx as i64) + 1;
            // Some rooms are unnamed to exercise label fallback downstream.
            let title = if rng.gen_bool(0.2) {
                String::new()
            } else {
                format!("room-{room_idx}")
            };
            let is_memo = room_idx == 0; // the memo chat (나와의 채팅)

            let mut messages = Vec::with_capacity(shape.messages_per_room);
            for msg_idx in 0..shape.messages_per_room {
                let is_owner = rng.gen_bool(0.3);
                let sender = if is_owner {
                    owner_name.clone()
                } else {
                    format!("member-{}", rng.gen_range(0..8))
                };
                let kind = if rng.gen_bool(0.15) {
                    MessageKind::Image
                } else {
                    MessageKind::Text
                };
                let text = match kind {
                    MessageKind::Text => format!("msg {room_idx}:{msg_idx}"),
                    _ => String::new(),
                };
                messages.push(DatasetMessage {
                    chat_id,
                    at: at_counter,
                    sender,
                    is_owner,
                    kind,
                    text,
                    provenance: format!("fake:{chat_id}:{msg_idx}"),
                });
                at_counter += 1;
            }

            script.push(ScriptedRoom {
                room: RoomRef {
                    chat_id,
                    title: title.clone(),
                },
                authority: SendAuthority {
                    chat_id,
                    chat_type: if is_memo { 0 } else { 1 },
                    is_memo,
                    owner_display_name: Some(owner_name.clone()),
                },
                messages,
            });
        }

        owner_candidates.push(OwnerCandidate {
            display_name: owner_name,
            message_count: (shape.rooms * shape.messages_per_room) as i64,
        });

        // A handful of deterministic providers whose count follows the safety
        // combo dial (at least one, capped so tests stay small).
        let provider_count = (shape.safety_combo % 4) + 1;
        let providers = (0..provider_count)
            .map(|i| {
                FakeProvider::new(
                    format!("fake-provider-{i}"),
                    vec![ModelId::new(format!("fake-model-{i}"))],
                    (10 + i as u64) % 50,
                    ScriptedOutcome::Reply(format!("reply-{i}")),
                )
            })
            .collect();

        // Deterministic Telegram page for the relay read boundary.
        let ax_messages = (0..shape.messages_per_room.min(8))
            .map(|i| TelegramMessage {
                pid: format!("tg-{seed}-{i}"),
                at: (i as i64) + 1,
                display_order: 0,
                text: format!("telegram {i}"),
                links: Vec::new(),
                images: Vec::new(),
                excluded: Vec::new(),
            })
            .collect();

        let clock = VirtualClock::new(0);

        FakePorts {
            messages: FakeMessageSource::new(seed, script, owner_candidates),
            send: FakeSendPort::new(),
            blocked_real_send: BlockingRealSendPort::new(),
            ax_read: FakeAxReadPort::new(ax_messages),
            providers,
            feed: FakeFeedSource::new(String::new()),
            net: ForbiddenNetwork::new(),
            clock: clock.clone(),
            sleeper: RecordingSleeper::new(clock),
            sandbox: tempfile::TempDir::new().expect("create sandbox tempdir"),
        }
    }

    /// Verify the world is isolated: every read/send port is a fake, no real
    /// send or network egress has happened, and the sandbox lives under the
    /// system temp directory. Returns `Err` with a human-readable reason on the
    /// first breach found (R1.9, R12.17).
    pub fn assert_isolated(&self) -> Result<(), String> {
        if !self.send.is_fake() {
            return Err("send port is not a fake".into());
        }
        if !self.ax_read.is_fake() {
            return Err("ax read port is not a fake".into());
        }
        if !self.messages.is_fake() {
            return Err("message source is not a fake".into());
        }
        if self.blocked_real_send.blocked_count() != 0 {
            return Err(format!(
                "real send path was hit {} time(s)",
                self.blocked_real_send.blocked_count()
            ));
        }
        if self.net.egress_count() != 0 {
            return Err(format!(
                "network egress happened {} time(s)",
                self.net.egress_count()
            ));
        }

        // The sandbox must sit under the system temp directory. Canonicalize
        // both sides so /var vs /private/var symlinks on macOS don't trip us.
        let sandbox = self
            .sandbox
            .path()
            .canonicalize()
            .map_err(|e| format!("sandbox path not resolvable: {e}"))?;
        let tmp = std::env::temp_dir()
            .canonicalize()
            .map_err(|e| format!("temp dir not resolvable: {e}"))?;
        if !sandbox.starts_with(&tmp) {
            return Err(format!(
                "sandbox {} is not under the temp dir {}",
                sandbox.display(),
                tmp.display()
            ));
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ports::ImageBlob;
    use crate::safety::{SendGrade, SendTicket};

    #[test]
    fn forbidden_network_never_sends_and_counts_attempts() {
        let net = ForbiddenNetwork::new();
        assert_eq!(net.egress_count(), 0);
        for i in 1..=3 {
            let res = net.request(HttpRequest {
                method: "GET".into(),
                url: "https://example.com".into(),
                body: Vec::new(),
            });
            assert_eq!(res, Err(PortError::NetworkForbidden));
            assert_eq!(net.egress_count(), i);
        }
    }

    #[test]
    fn blocking_real_send_counts_and_refuses() {
        let port = BlockingRealSendPort::new();
        assert_eq!(port.blocked_count(), 0);
        assert!(!port.is_fake());
        let ticket = SendTicket::for_test(1, SendGrade::Fake);
        assert_eq!(
            port.send_text(&ticket, "hi"),
            Err(PortError::RealSendBlocked)
        );
        assert_eq!(
            port.send_image(
                &ticket,
                &ImageBlob {
                    bytes: vec![1, 2, 3],
                    mime: "image/png".into()
                }
            ),
            Err(PortError::RealSendBlocked)
        );
        assert_eq!(port.blocked_count(), 2);
    }

    #[test]
    fn virtual_clock_and_recording_sleeper_advance_logical_time() {
        let clock = VirtualClock::new(1_000);
        let sleeper = RecordingSleeper::new(clock.clone());
        assert_eq!(clock.now_ms(), 1_000);

        sleeper.sleep_ms(250);
        sleeper.sleep_ms(750);

        // The sleeper never blocks; it only advances the shared clock.
        assert_eq!(clock.now_ms(), 2_000);
        assert_eq!(sleeper.records(), vec![250, 750]);
        assert_eq!(sleeper.total_slept_ms(), 1_000);
    }

    #[test]
    fn scenario_shape_clamps_to_ceilings() {
        let shape = ScenarioShape::new(1_000, 10_000, 100, 500);
        assert_eq!(shape.rooms, ScenarioShape::MAX_ROOMS);
        assert_eq!(shape.messages_per_room, ScenarioShape::MAX_MESSAGES_PER_ROOM);
        assert_eq!(
            shape.images_per_message,
            ScenarioShape::MAX_IMAGES_PER_MESSAGE
        );
        assert_eq!(shape.safety_combo, ScenarioShape::MAX_SAFETY_COMBO);
    }

    #[test]
    fn from_seed_is_deterministic_and_isolated() {
        let shape = ScenarioShape::new(3, 10, 2, 8);
        let a = FakePorts::from_seed(42, shape);
        let b = FakePorts::from_seed(42, shape);

        // Same seed + shape ⇒ identical rooms and messages.
        assert_eq!(a.messages.rooms().unwrap(), b.messages.rooms().unwrap());
        assert_eq!(
            a.messages.messages_after(None, 10_000).unwrap(),
            b.messages.messages_after(None, 10_000).unwrap()
        );

        // A freshly-built world is isolated.
        a.assert_isolated().expect("world must be isolated");
    }

    #[test]
    fn assert_isolated_flags_a_real_send() {
        let ports = FakePorts::from_seed(7, ScenarioShape::default());
        // Simulate a call reaching the real send path.
        let ticket = SendTicket::for_test(1, SendGrade::Fake);
        let _ = ports.blocked_real_send.send_text(&ticket, "leak");
        assert!(ports.assert_isolated().is_err());
    }

    #[test]
    fn fake_message_source_respects_cursor() {
        let ports = FakePorts::from_seed(9, ScenarioShape::new(2, 5, 0, 4));
        let all = ports.messages.messages_after(None, 10_000).unwrap();
        assert!(!all.is_empty());
        let mid = all[all.len() / 2].at;
        let after = ports
            .messages
            .messages_after(Some(&MessageCursor { at: mid, log_id: 0 }), 10_000)
            .unwrap();
        assert!(after.iter().all(|m| m.at > mid));
    }
}
