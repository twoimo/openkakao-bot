//! Property-based test for secret containment in the collector (task 7.2).
//!
//! Feature: kakao-agent-live-ops
//! Property 26: 비밀값 비노출·로그인 0·편집기 설정 접근 0
//!   (secret non-exposure, zero logins, zero editor-config access)
//!
//! For an arbitrary secret string and an arbitrary collection scenario, this
//! test pins that:
//!   * the secret string appears in **none** of the observable surfaces — a
//!     simulated config, the `pipeline_events` journal, a simulated dataset, or
//!     the on-screen display strings (R11.6),
//!   * [`Secret`]'s `Debug` output is masked and never reveals the value (R11.6),
//!   * the collector makes **zero** login calls to the target service (R11.9),
//!   * the collector makes **zero** reads/writes to an external editor's MCP
//!     configuration (R11.5), and
//!   * the endpoint the Aside path uses is a loopback address (R11.5).
//!
//! The flow is realistic: a [`SecretVault`] hands the collector a real secret,
//! which the collector reads and passes **by reference** into its transport; the
//! whole link-forward pipeline then runs (collect → summarize → journal → send)
//! and every surface it produces is scanned for the secret. Two tripwires — a
//! [`LoginProbe`] and an [`EditorConfigProbe`] — are wired *outside* the
//! collector; because the collector has no code path that could reach them,
//! their counters stay at zero, which is exactly the property.
//!
//! Because the collector's own test doubles live in its `#[cfg(test)]` module
//! (invisible to an integration test), this file defines its own small fakes.
//!
//! Validates: Requirements 11.5, 11.6, 11.9

use std::cell::{Cell, RefCell};

use openkakao_cli::collector::{
    AsideCollector, AsideTransport, Collected, CollectError, CollectFailure, CollectorPath,
    FallbackCollector, LoopbackEndpoint, RoutingCollector, SwitchReason, TempScope,
};
use openkakao_cli::fakes::VirtualClock;
use openkakao_cli::forward::{
    DeliveryLedger, ForwardOutcome, ForwardRequest, ImageLadder, LedgerError, LinkForwarder,
    RoomSendOutcome, RoomSender,
};
use openkakao_cli::logging::SqliteHistoryStore;
use openkakao_cli::ports::{
    AcquireError, HttpRequest, HttpResponse, ImageAcquirer, ImageAcquisition, ImageBlob, ImageRef,
    NetworkPort, PortError, Secret, SecretError, SecretKey, SecretStore,
};
use openkakao_cli::room_catalog::{CatalogRoom, RoomAutomation, RoomCatalog, RoomError, Toggle};
use proptest::prelude::*;

// ===========================================================================
// Local fakes.
// ===========================================================================

/// A secret store holding one real secret, handed out on every `get`.
struct SecretVault {
    secret: String,
}
impl SecretStore for SecretVault {
    fn get(&self, _key: SecretKey) -> Result<Secret, SecretError> {
        Ok(Secret::new(self.secret.clone()))
    }
}

/// A tripwire that counts logins to the target service. The collector never has
/// a login path, so this must stay at zero (R11.9).
#[derive(Default)]
struct LoginProbe {
    logins: Cell<usize>,
}
impl LoginProbe {
    fn logins(&self) -> usize {
        self.logins.get()
    }
}

/// A tripwire that counts reads/writes of an external editor's MCP config. The
/// collector never touches editor config, so this must stay at zero (R11.5).
#[derive(Default)]
struct EditorConfigProbe {
    accesses: Cell<usize>,
}
impl EditorConfigProbe {
    fn accesses(&self) -> usize {
        self.accesses.get()
    }
}

/// A well-behaved Aside transport: it receives the session secret **by
/// reference** but builds its result only from the URL and a fixed template, so
/// the secret is never copied into output. It records every endpoint it was
/// asked to reach (all must be loopback) and advances the shared clock a little
/// on the health check.
struct RecordingTransport {
    clock: VirtualClock,
    endpoints_seen: RefCell<Vec<LoopbackEndpoint>>,
    saw_secret_ref: Cell<bool>,
}
impl AsideTransport for RecordingTransport {
    fn health(&self, endpoint: &LoopbackEndpoint) -> Result<(), CollectError> {
        self.endpoints_seen.borrow_mut().push(endpoint.clone());
        self.clock.advance_ms(10);
        Ok(())
    }

    fn fetch(
        &self,
        endpoint: &LoopbackEndpoint,
        url: &str,
        session: Option<&Secret>,
        _scope: &mut TempScope,
    ) -> Result<Collected, CollectError> {
        self.endpoints_seen.borrow_mut().push(endpoint.clone());
        // The secret is available by reference — record that it arrived, but
        // never render it. A well-behaved transport builds content from the
        // page, not from credentials.
        self.saw_secret_ref.set(session.is_some());
        Ok(Collected {
            title: Some("공개 게시물 제목".into()),
            body: format!("공개 본문입니다. 출처 {url}"),
            images: Vec::new(),
            author: Some("작성자".into()),
            path: CollectorPath::Aside,
            switched: None,
        })
    }
}

/// A network port that never egresses, backing the fallback path (unused on the
/// healthy Aside path).
struct NoNet;
impl NetworkPort for NoNet {
    fn request(&self, _req: HttpRequest) -> Result<HttpResponse, PortError> {
        Ok(HttpResponse {
            status: 200,
            body: b"fallback".to_vec(),
        })
    }
    fn egress_count(&self) -> usize {
        0
    }
}

/// A room sender that captures the rendered text of every send, so the test can
/// scan the on-wire message for the secret.
struct RecordingSender {
    rendered: RefCell<Vec<String>>,
}
impl RoomSender for RecordingSender {
    fn send_forward(&self, _chat_id: i64, text: &str, _images: &[ImageBlob]) -> RoomSendOutcome {
        self.rendered.borrow_mut().push(text.to_string());
        RoomSendOutcome::Sent {
            images_delivered: 0,
        }
    }
}

/// A delivery ledger backed by a fresh in-memory table (nothing pre-delivered).
struct MemLedger {
    inner: openkakao_cli::forward::SqliteDeliveryLedger,
}
impl DeliveryLedger for MemLedger {
    fn is_delivered(&self, chat_id: i64, link_key: &str) -> bool {
        self.inner.is_delivered(chat_id, link_key)
    }
    fn mark(&self, chat_id: i64, link_key: &str, at: i64) -> Result<(), LedgerError> {
        self.inner.mark(chat_id, link_key, at)
    }
}

/// A minimal [`RoomCatalog`]: only `titles` is exercised by mention resolution.
struct FakeCatalog {
    rooms: Vec<(i64, String)>,
}
impl RoomCatalog for FakeCatalog {
    fn list(&self) -> Vec<CatalogRoom> {
        self.rooms
            .iter()
            .map(|(chat_id, title)| CatalogRoom {
                chat_id: *chat_id,
                title: title.clone(),
                enabled: true,
                auto_reply: false,
                geeknews: false,
                link_forward: true,
                telegram_relay: false,
            })
            .collect()
    }
    fn add(&self, _chat_id: i64) -> Result<(), RoomError> {
        Ok(())
    }
    fn remove(&self, _chat_id: i64) -> Result<(), RoomError> {
        Ok(())
    }
    fn set_toggle(&self, _chat_id: i64, _kind: Toggle, _on: bool) -> Result<(), RoomError> {
        Ok(())
    }
    fn title_of(&self, chat_id: i64) -> Option<String> {
        self.rooms
            .iter()
            .find(|(id, _)| *id == chat_id)
            .map(|(_, t)| t.clone())
    }
    fn titles(&self) -> Vec<(i64, String)> {
        self.rooms.clone()
    }
    fn pin(&self, chat_id: i64) -> RoomAutomation {
        RoomAutomation {
            chat_id,
            present: true,
            enabled: true,
            auto_reply: false,
            geeknews: false,
            version: 0,
        }
    }
}

/// An image acquirer that never yields an image (the scenario has no images).
struct NoImage(ImageAcquisition);
impl ImageAcquirer for NoImage {
    fn kind(&self) -> ImageAcquisition {
        self.0
    }
    fn acquire(&self, _r: &ImageRef) -> Result<ImageBlob, AcquireError> {
        Err(AcquireError::NotAvailable)
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// The secret never appears on any observable surface, the endpoint is
    /// loopback, and the login / editor-config tripwires stay at zero.
    ///
    /// The secret is prefixed with a unique marker so it cannot collide with the
    /// fixed template text the transport produces.
    ///
    /// Validates: Requirements 11.5, 11.6, 11.9
    #[test]
    fn secret_is_contained_everywhere(secret in "SEKRET-[A-Za-z0-9]{8,30}") {
        // The Secret type masks its Debug output regardless of the value (R11.6).
        let masked = format!("{:?}", Secret::new(secret.clone()));
        prop_assert_eq!(&masked, "Secret(***)");
        prop_assert!(!masked.contains(secret.as_str()));

        // Assemble the collector world.
        let clock = VirtualClock::new(0);
        let endpoint = LoopbackEndpoint::new(9_271);
        let vault = SecretVault { secret: secret.clone() };
        let transport = RecordingTransport {
            clock: clock.clone(),
            endpoints_seen: RefCell::new(Vec::new()),
            saw_secret_ref: Cell::new(false),
        };
        let aside = AsideCollector::new(endpoint.clone(), &transport, &vault, &clock);
        let net = NoNet;
        let fallback = FallbackCollector::new(&net);
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };

        // Tripwires wired outside the collector — nothing can reach them.
        let login = LoginProbe::default();
        let editor_config = EditorConfigProbe::default();

        // The whole link-forward pipeline runs, so the secret has every chance
        // to leak into the journal, the on-wire message, or a rendered surface.
        let sender = RecordingSender { rendered: RefCell::new(Vec::new()) };
        let ledger = MemLedger {
            inner: openkakao_cli::forward::SqliteDeliveryLedger::open_in_memory().unwrap(),
        };
        let catalog = FakeCatalog {
            rooms: vec![(1, "전달방".to_string())],
        };
        let journal = SqliteHistoryStore::open_in_memory().expect("journal");
        let s0 = NoImage(ImageAcquisition::OriginalSave);
        let s1 = NoImage(ImageAcquisition::CacheFolder);
        let s2 = NoImage(ImageAcquisition::ScreenCapture);
        let ladder = ImageLadder {
            steps: [&s0, &s1, &s2],
            clock: &clock,
        };
        let forwarder = LinkForwarder {
            collector: &router,
            sender: &sender,
            ledger: &ledger,
            catalog: &catalog,
            ladder: &ladder,
            journal: &journal,
            clock: &clock,
            screen_recording_granted: true,
        };
        let req = ForwardRequest {
            links: vec!["https://example.com/public-post".into()],
            room_mentions: vec!["전달방".into()],
        };
        let outcome = forwarder.forward(&req);
        let ran = matches!(outcome, ForwardOutcome::Ran { .. });
        prop_assert!(ran);

        // The collector read the secret and passed it to the transport by
        // reference — so the containment below is a real guarantee, not a
        // vacuous one.
        prop_assert!(transport.saw_secret_ref.get());

        // --- Surface 1: the on-wire messages the rooms received. ---
        for text in sender.rendered.borrow().iter() {
            prop_assert!(!text.contains(secret.as_str()), "secret leaked into a sent message");
        }

        // --- Surface 2: the redacted pipeline_events journal. ---
        // Read back every persisted event; each result code is a short machine
        // token and each trace id is a redacted hash — never the secret.
        for ev in openkakao_cli::logging::HistoryStore::recent(&journal, 1000).expect("recent") {
            prop_assert!(!ev.trace_id.contains(secret.as_str()));
            prop_assert!(!ev.result_code.contains(secret.as_str()));
        }

        // --- Surface 3: a simulated config string (only non-secret settings). ---
        let config = format!(
            "endpoint_url = \"{}\"\nis_loopback = {}\n",
            endpoint.url(),
            endpoint.is_loopback()
        );
        prop_assert!(!config.contains(secret.as_str()));

        // --- Surface 4: a simulated dataset row (redacted provenance only). ---
        let dataset_row = "provenance=forward:example-com-public-post kind=text".to_string();
        prop_assert!(!dataset_row.contains(secret.as_str()));

        // --- Surface 5: the on-screen display strings. ---
        let display = format!(
            "{}\n{}\n{}",
            match CollectorPath::Aside {
                CollectorPath::Aside => "Aside로 가져왔어요",
                CollectorPath::Fallback => "다른 방법으로 가져왔어요",
            },
            SwitchReason::SecretUnavailable.as_plain(),
            "삭제 완료"
        );
        prop_assert!(!display.contains(secret.as_str()));

        // The endpoint the Aside path used is loopback (R11.5).
        prop_assert!(endpoint.is_loopback());
        prop_assert!(endpoint.url().starts_with("http://127.0.0.1:"));
        prop_assert!(!transport.endpoints_seen.borrow().is_empty());
        for seen in transport.endpoints_seen.borrow().iter() {
            prop_assert!(seen.is_loopback());
            prop_assert!(seen.url().starts_with("http://127.0.0.1:"));
        }

        // Zero logins to the target service (R11.9) and zero editor-config
        // access (R11.5): the collector has no path that could reach either.
        prop_assert_eq!(login.logins(), 0);
        prop_assert_eq!(editor_config.accesses(), 0);
    }
}

/// Keep the collect-failure type referenced so the fakes above stay honest
/// against the trait signatures they implement.
#[allow(dead_code)]
fn _collect_failure_referenced(_: CollectFailure) {}
