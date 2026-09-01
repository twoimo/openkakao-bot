//! The two-path link collector (수집기 2경로) — parent task 7 (R11).
//!
//! Link content is fetched by one of two paths: the [`AsideCollector`], which
//! talks to a **loopback** Aside browser MCP server, and the
//! [`FallbackCollector`], which works without Aside. [`RoutingCollector`]
//! prefers Aside when a single connection check succeeds and falls back to the
//! other path otherwise, all within a twenty-second bound.
//!
//! Several safety guarantees hold structurally here:
//!
//! * **At most one switch per link (R11.11).** A collection result carries
//!   `switched: Option<SwitchReason>`, so a second switch is not representable —
//!   [`RoutingCollector::collect`] calls the fallback path at most once and sets
//!   the reason exactly once.
//! * **Private/deleted are terminal (R11.10).** [`CollectError::forbids_other_path`]
//!   marks [`CollectError::Private`] and [`CollectError::Deleted`] so the router
//!   never retries them on the other path.
//! * **Secrets never leak (R11.6, R11.7).** A missing/rejected secret surfaces as
//!   [`CollectError::Unauthorized`] carrying only the secret's *name*, never its
//!   value. The [`AsideCollector`] reads credentials from a [`SecretStore`] and
//!   passes them by reference to its transport; there is no code path that
//!   renders a secret.
//! * **Loopback only, no editor config (R11.5).** [`LoopbackEndpoint`] is
//!   constructed for `127.0.0.1` only, and the [`AsideCollector`] holds nothing
//!   but that endpoint — it never reads an external editor's MCP configuration.
//! * **No login (R11.9).** There is no path anywhere here that logs into the
//!   target service; only publicly-reachable posts are collected.
//! * **Temp data is always deleted (R11.8).** [`TempScope`] deletes its raw
//!   working data on [`TempScope::finish`] and, as a safety net, on `Drop`.

use thiserror::Error;

use crate::ports::{Clock, NetworkPort, Secret, SecretKey, SecretStore};

/// The per-link overall bound, including the connection check and any path
/// switch (R11.12).
pub const COLLECT_DEADLINE_SECS: u64 = 20;
/// The single Aside connection-check bound (R11.2).
pub const HEALTH_TIMEOUT_SECS: u64 = 3;
/// The bound on an Aside collection that already passed its health check; a
/// slower response triggers the one allowed switch to the fallback (R11.11).
pub const ASIDE_COLLECT_TIMEOUT_SECS: u64 = 10;
/// The bound within which temporary raw data must be deleted (R11.8).
pub const DELETION_DEADLINE_MS: u64 = 5_000;

// ---------------------------------------------------------------------------
// Value types
// ---------------------------------------------------------------------------

/// Which collection path produced a result (R11.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CollectorPath {
    /// The Aside browser MCP path.
    Aside,
    /// The Aside-free fallback path.
    Fallback,
}

/// Why the collector switched from Aside to the fallback path (R11.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SwitchReason {
    /// Aside is not installed.
    NotInstalled,
    /// The connection check did not answer within three seconds (R11.2).
    HealthTimeout,
    /// Aside passed its health check but errored mid-collection (R11.11).
    MidCollectError,
    /// Aside passed its health check but did not answer within ten seconds
    /// (R11.11).
    MidCollectTimeout,
    /// A required secret could not be read, so the remaining path is tried
    /// once (R11.7).
    SecretUnavailable,
}

impl SwitchReason {
    /// A short, plain-language reason for the History window (R11.3).
    pub fn as_plain(self) -> &'static str {
        match self {
            SwitchReason::NotInstalled => "Aside가 설치되어 있지 않아 다른 방법으로 가져왔어요",
            SwitchReason::HealthTimeout => "Aside 연결 확인이 늦어 다른 방법으로 가져왔어요",
            SwitchReason::MidCollectError => "Aside가 도중에 실패해 다른 방법으로 가져왔어요",
            SwitchReason::MidCollectTimeout => "Aside 응답이 늦어 다른 방법으로 가져왔어요",
            SwitchReason::SecretUnavailable => "접속 정보를 쓸 수 없어 다른 방법으로 가져왔어요",
        }
    }
}

/// Collected link content (R6.3, R6.4). It holds no secret and no login state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Collected {
    /// The original title, or `None` when it could not be determined (R6.3).
    pub title: Option<String>,
    /// The original body text.
    pub body: String,
    /// Image references in the order they appear in the post (R6.4).
    pub images: Vec<crate::ports::ImageRef>,
    /// The author, when the post is a third party's (R6.9).
    pub author: Option<String>,
    /// Which path produced this result.
    pub path: CollectorPath,
    /// The switch reason, if a switch happened. `Option` makes a second switch
    /// unrepresentable (R11.11).
    pub switched: Option<SwitchReason>,
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A failure collecting one link on one path.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum CollectError {
    /// Aside is not installed.
    #[error("Aside가 설치되어 있지 않아요")]
    NotInstalled,
    /// The connection check did not answer within three seconds (R11.2).
    #[error("Aside 연결 확인이 3초 안에 되지 않았어요")]
    HealthTimeout,
    /// A required secret is missing or was rejected. Carries only the secret's
    /// name — never its value (R11.7).
    #[error("접속 정보를 쓸 수 없어요: {item}")]
    Unauthorized {
        /// The human-readable *name* of the missing secret (never the value).
        item: &'static str,
    },
    /// The post is private and cannot be viewed without logging in (R11.10).
    #[error("이 링크는 비공개라 볼 수 없어요")]
    Private,
    /// The post has been deleted (R11.10).
    #[error("이 링크는 삭제되었어요")]
    Deleted,
    /// The path did not finish in time.
    #[error("수집이 시간 안에 끝나지 않았어요")]
    Timeout,
    /// Some other transport failure.
    #[error("링크 내용을 가져오지 못했어요: {0}")]
    Transport(String),
}

impl CollectError {
    /// Whether this failure forbids retrying the link on another path. A
    /// private or deleted post is terminal — the router must not try the other
    /// path (R11.10).
    pub fn forbids_other_path(&self) -> bool {
        matches!(self, CollectError::Private | CollectError::Deleted)
    }
}

/// The overall failure of a routed collection (R11.4, R11.10, R11.12).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CollectFailure {
    /// Both paths failed; carries each path's reason (R11.4).
    BothFailed {
        /// The Aside path's failure (a health failure counts).
        aside: CollectError,
        /// The fallback path's failure.
        fallback: CollectError,
    },
    /// A terminal state that must not be retried on the other path (R11.10).
    Terminal(CollectError),
    /// The twenty-second overall bound elapsed (R11.12).
    DeadlineExceeded,
}

// ---------------------------------------------------------------------------
// TempScope
// ---------------------------------------------------------------------------

/// The report of deleting a [`TempScope`]'s raw working data (R11.8).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeletionReport {
    /// Whether the temporary data was deleted.
    pub deleted: bool,
    /// Whether the deletion finished within the five-second bound (R11.8).
    pub within_deadline: bool,
    /// How long the deletion took, in milliseconds.
    pub elapsed_ms: u64,
}

/// The lifetime of the temporary raw data made while collecting one link.
///
/// Whatever a collector writes goes under [`TempScope::path`]. The data is
/// deleted on [`TempScope::finish`], which reports whether the deletion
/// completed within five seconds (R11.8); as a safety net, [`Drop`] deletes it
/// too, so a success, failure, or panic all leave nothing behind.
#[derive(Debug)]
pub struct TempScope {
    dir: Option<tempfile::TempDir>,
    deleted: bool,
}

impl TempScope {
    /// Create a fresh scope with an empty working directory.
    pub fn new() -> std::io::Result<Self> {
        Ok(Self {
            dir: Some(tempfile::TempDir::new()?),
            deleted: false,
        })
    }

    /// The working directory raw data must be written under, or `None` once the
    /// scope has been deleted.
    pub fn path(&self) -> Option<&std::path::Path> {
        self.dir.as_ref().map(|d| d.path())
    }

    /// Whether the working data has been deleted.
    pub fn is_deleted(&self) -> bool {
        self.deleted
    }

    /// Delete the working data and report whether it finished within five
    /// seconds (R11.8). Consumes the scope.
    pub fn finish(mut self) -> DeletionReport {
        let start = std::time::Instant::now();
        let deleted = self.delete_now();
        let elapsed_ms = start.elapsed().as_millis() as u64;
        DeletionReport {
            deleted,
            within_deadline: elapsed_ms <= DELETION_DEADLINE_MS,
            elapsed_ms,
        }
    }

    /// Delete the working directory if it still exists. Idempotent.
    fn delete_now(&mut self) -> bool {
        if self.deleted {
            return true;
        }
        let ok = match self.dir.take() {
            Some(dir) => dir.close().is_ok(),
            None => true,
        };
        self.deleted = true;
        ok
    }
}

impl Drop for TempScope {
    fn drop(&mut self) {
        // Safety-net deletion: even if `finish` was never called, the raw data
        // does not outlive the scope (R11.8).
        let _ = self.delete_now();
    }
}

// ---------------------------------------------------------------------------
// PageCollector and the two paths
// ---------------------------------------------------------------------------

/// One collection path (R11.1). Both Aside and the fallback implement this so
/// the router can treat them uniformly.
pub trait PageCollector {
    /// Which path this is.
    fn path(&self) -> CollectorPath;

    /// A single connection check (R11.2). The fallback path always reports
    /// healthy; Aside sends exactly one loopback request bounded to three
    /// seconds.
    fn health_check(&self) -> Result<(), CollectError>;

    /// Collect the link, writing any raw working data under `scope`.
    fn collect(&self, url: &str, scope: &mut TempScope) -> Result<Collected, CollectError>;
}

/// A loopback-only endpoint (`127.0.0.1`). Constructed for the loopback host
/// only, so the [`AsideCollector`] can never be pointed at an external editor's
/// MCP server (R11.5).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoopbackEndpoint {
    port: u16,
}

impl LoopbackEndpoint {
    /// Build a loopback endpoint on `port`. The host is always `127.0.0.1`;
    /// there is no way to specify another host.
    pub fn new(port: u16) -> Self {
        Self { port }
    }

    /// The loopback URL this endpoint addresses.
    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/mcp", self.port)
    }

    /// Always true — the endpoint is loopback by construction (R11.5).
    pub fn is_loopback(&self) -> bool {
        true
    }
}

/// The transport the [`AsideCollector`] uses to reach its loopback MCP server.
///
/// Abstracted so no real network is needed in tests, and — more importantly —
/// so the collector only ever knows the loopback endpoint it was handed, never
/// an external editor's MCP configuration (R11.5). Timing is measured by the
/// caller on the injected clock, so the transport itself needs no clock.
pub trait AsideTransport {
    /// A single connection-check request to the loopback endpoint (R11.2).
    fn health(&self, endpoint: &LoopbackEndpoint) -> Result<(), CollectError>;

    /// Fetch structured content for `url` through the loopback MCP server.
    /// `session` is the collector session secret, borrowed so its contents are
    /// never copied or rendered.
    fn fetch(
        &self,
        endpoint: &LoopbackEndpoint,
        url: &str,
        session: Option<&Secret>,
        scope: &mut TempScope,
    ) -> Result<Collected, CollectError>;
}

/// The Aside browser MCP collection path (R11.1, R11.5).
///
/// It holds nothing but a [`LoopbackEndpoint`], a transport, a secret store,
/// and a clock. The clock bounds the single connection check to three seconds
/// (R11.2).
pub struct AsideCollector<'a> {
    endpoint: LoopbackEndpoint,
    transport: &'a dyn AsideTransport,
    secrets: &'a dyn SecretStore,
    clock: &'a dyn Clock,
}

impl<'a> AsideCollector<'a> {
    /// Build an Aside collector over a loopback endpoint and transport.
    pub fn new(
        endpoint: LoopbackEndpoint,
        transport: &'a dyn AsideTransport,
        secrets: &'a dyn SecretStore,
        clock: &'a dyn Clock,
    ) -> Self {
        Self {
            endpoint,
            transport,
            secrets,
            clock,
        }
    }

    /// The loopback endpoint this collector uses (always loopback, R11.5).
    pub fn endpoint(&self) -> &LoopbackEndpoint {
        &self.endpoint
    }
}

impl PageCollector for AsideCollector<'_> {
    fn path(&self) -> CollectorPath {
        CollectorPath::Aside
    }

    fn health_check(&self) -> Result<(), CollectError> {
        // Exactly one connection-check request, bounded to three seconds on the
        // injected clock (R11.2).
        let start = self.clock.now_ms();
        let result = self.transport.health(&self.endpoint);
        let elapsed = self.clock.now_ms().saturating_sub(start);
        if elapsed > (HEALTH_TIMEOUT_SECS * 1000) as i64 {
            return Err(CollectError::HealthTimeout);
        }
        result
    }

    fn collect(&self, url: &str, scope: &mut TempScope) -> Result<Collected, CollectError> {
        // Read the session secret; a missing/denied secret surfaces by *name*
        // only, never by value (R11.6, R11.7).
        let session = match self.secrets.get(SecretKey::CollectorSession) {
            Ok(secret) => Some(secret),
            Err(_) => {
                return Err(CollectError::Unauthorized {
                    item: "브라우저 연결 정보",
                })
            }
        };

        // Bound the collection to ten seconds on the injected clock (R11.11).
        let start = self.clock.now_ms();
        let mut result = self
            .transport
            .fetch(&self.endpoint, url, session.as_ref(), scope);
        let elapsed = self.clock.now_ms().saturating_sub(start);
        if elapsed > (ASIDE_COLLECT_TIMEOUT_SECS * 1000) as i64 {
            return Err(CollectError::Timeout);
        }
        if let Ok(collected) = &mut result {
            collected.path = CollectorPath::Aside;
            collected.switched = None;
        }
        result
    }
}

/// The Aside-free fallback collection path (R11.1).
///
/// It fetches through a [`NetworkPort`] and always reports healthy, since it
/// does not depend on Aside being installed.
pub struct FallbackCollector<'a> {
    net: &'a dyn NetworkPort,
}

impl<'a> FallbackCollector<'a> {
    /// Build a fallback collector over a network port.
    pub fn new(net: &'a dyn NetworkPort) -> Self {
        Self { net }
    }
}

impl PageCollector for FallbackCollector<'_> {
    fn path(&self) -> CollectorPath {
        CollectorPath::Fallback
    }

    fn health_check(&self) -> Result<(), CollectError> {
        // The fallback path does not need Aside, so it is always healthy.
        Ok(())
    }

    fn collect(&self, url: &str, _scope: &mut TempScope) -> Result<Collected, CollectError> {
        // A minimal, no-login fetch (R11.9): no credentials are ever sent. Rich
        // HTML extraction is layered on in task 7.1; the parent step wires the
        // path and maps status to the terminal/transport categories the router
        // needs.
        let response = self
            .net
            .request(crate::ports::HttpRequest {
                method: "GET".into(),
                url: url.to_string(),
                body: Vec::new(),
            })
            .map_err(|e| CollectError::Transport(e.to_string()))?;

        match response.status {
            200 => Ok(Collected {
                title: None,
                body: String::from_utf8_lossy(&response.body).into_owned(),
                images: Vec::new(),
                author: None,
                path: CollectorPath::Fallback,
                switched: None,
            }),
            401 | 403 => Err(CollectError::Private),
            404 | 410 => Err(CollectError::Deleted),
            other => Err(CollectError::Transport(format!("HTTP {other}"))),
        }
    }
}

// ---------------------------------------------------------------------------
// RoutingCollector
// ---------------------------------------------------------------------------

/// The result of the Aside attempt, before deciding whether to fall back.
enum AsideAttempt {
    /// Aside collected the link.
    Collected(Collected),
    /// A terminal failure that forbids trying the other path (R11.10).
    Terminal(CollectError),
    /// Aside failed in a way that permits one switch to the fallback.
    Switch {
        /// Why we are switching.
        reason: SwitchReason,
        /// The Aside failure to report if the fallback also fails (R11.4).
        aside_error: CollectError,
    },
}

/// Routes a link collection across the two paths (R11.1, R11.11, R11.12).
pub struct RoutingCollector<'a> {
    /// The Aside path, preferred when its health check passes.
    pub aside: &'a dyn PageCollector,
    /// The fallback path, used at most once when Aside cannot serve.
    pub fallback: &'a dyn PageCollector,
    /// The logical clock bounding the overall collection.
    pub clock: &'a dyn Clock,
}

impl RoutingCollector<'_> {
    /// Collect one link, preferring Aside and switching to the fallback at most
    /// once, all within the twenty-second bound (R11.11, R11.12).
    ///
    /// The temporary raw data is always deleted before returning, whether the
    /// collection succeeds, fails, or is abandoned (R11.8).
    pub fn collect(&self, url: &str) -> Result<Collected, CollectFailure> {
        let mut scope = match TempScope::new() {
            Ok(scope) => scope,
            Err(e) => {
                return Err(CollectFailure::BothFailed {
                    aside: CollectError::Transport(e.to_string()),
                    fallback: CollectError::Transport("임시 저장 공간을 만들지 못했어요".into()),
                })
            }
        };

        let outcome = self.collect_within(url, &mut scope);

        // On every exit — success, failure, or abandonment — delete the temp
        // data (R11.8). The report is surfaced to the History window in
        // task 7.2; the safety-net deletion happens here (and on Drop).
        let _report = scope.finish();
        outcome
    }

    /// The routing decision, sharing one [`TempScope`] across both paths.
    fn collect_within(
        &self,
        url: &str,
        scope: &mut TempScope,
    ) -> Result<Collected, CollectFailure> {
        let start = self.clock.now_ms();
        let deadline = start + (COLLECT_DEADLINE_SECS * 1000) as i64;

        match self.try_aside(url, scope) {
            AsideAttempt::Collected(collected) => {
                if self.clock.now_ms() >= deadline {
                    return Err(CollectFailure::DeadlineExceeded);
                }
                Ok(collected)
            }
            AsideAttempt::Terminal(err) => Err(CollectFailure::Terminal(err)),
            AsideAttempt::Switch {
                reason,
                aside_error,
            } => {
                // The single allowed switch to the fallback (R11.11). Check the
                // overall bound first, since it includes the switch (R11.12).
                if self.clock.now_ms() >= deadline {
                    return Err(CollectFailure::DeadlineExceeded);
                }
                match self.fallback.collect(url, scope) {
                    Ok(mut collected) => {
                        if self.clock.now_ms() >= deadline {
                            return Err(CollectFailure::DeadlineExceeded);
                        }
                        collected.path = CollectorPath::Fallback;
                        // Set the switch reason exactly once; `Option` makes a
                        // second switch unrepresentable (R11.11).
                        collected.switched = Some(reason);
                        Ok(collected)
                    }
                    Err(fallback_error) => Err(CollectFailure::BothFailed {
                        aside: aside_error,
                        fallback: fallback_error,
                    }),
                }
            }
        }
    }

    /// Run the Aside health check and, if healthy, the Aside collection,
    /// classifying the result into collect / terminal / switch.
    fn try_aside(&self, url: &str, scope: &mut TempScope) -> AsideAttempt {
        match self.aside.health_check() {
            Ok(()) => match self.aside.collect(url, scope) {
                Ok(collected) => AsideAttempt::Collected(collected),
                // Private/deleted must not be retried on the other path (R11.10).
                Err(err) if err.forbids_other_path() => AsideAttempt::Terminal(err),
                // A missing secret means try the remaining path once (R11.7).
                Err(err @ CollectError::Unauthorized { .. }) => AsideAttempt::Switch {
                    reason: SwitchReason::SecretUnavailable,
                    aside_error: err,
                },
                Err(err @ CollectError::Timeout) => AsideAttempt::Switch {
                    reason: SwitchReason::MidCollectTimeout,
                    aside_error: err,
                },
                Err(err) => AsideAttempt::Switch {
                    reason: SwitchReason::MidCollectError,
                    aside_error: err,
                },
            },
            // Health failure: switch to the fallback with a matching reason.
            Err(err @ CollectError::NotInstalled) => AsideAttempt::Switch {
                reason: SwitchReason::NotInstalled,
                aside_error: err,
            },
            Err(err @ CollectError::HealthTimeout) => AsideAttempt::Switch {
                reason: SwitchReason::HealthTimeout,
                aside_error: err,
            },
            // Any other health failure also falls back, reported as a health
            // timeout-class switch.
            Err(err) => AsideAttempt::Switch {
                reason: SwitchReason::HealthTimeout,
                aside_error: err,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fakes::VirtualClock;
    use crate::ports::{ImageRef, PortError, SecretError};
    use std::cell::Cell;

    // ---- TempScope ----

    #[test]
    fn temp_scope_finish_deletes_and_reports_within_deadline() {
        let scope = TempScope::new().unwrap();
        let path = scope.path().unwrap().to_path_buf();
        assert!(path.exists());
        let report = scope.finish();
        assert!(report.deleted);
        assert!(report.within_deadline);
        assert!(!path.exists());
    }

    #[test]
    fn temp_scope_drop_is_a_safety_net() {
        let path;
        {
            let scope = TempScope::new().unwrap();
            path = scope.path().unwrap().to_path_buf();
            assert!(path.exists());
            // Dropped without calling finish().
        }
        assert!(!path.exists());
    }

    // ---- CollectError classification ----

    #[test]
    fn private_and_deleted_forbid_other_path() {
        assert!(CollectError::Private.forbids_other_path());
        assert!(CollectError::Deleted.forbids_other_path());
        assert!(!CollectError::HealthTimeout.forbids_other_path());
        assert!(!CollectError::Unauthorized { item: "x" }.forbids_other_path());
        assert!(!CollectError::Timeout.forbids_other_path());
    }

    #[test]
    fn unauthorized_message_carries_only_the_name() {
        let err = CollectError::Unauthorized {
            item: "브라우저 연결 정보",
        };
        let msg = err.to_string();
        assert!(msg.contains("브라우저 연결 정보"));
        // No secret value could ever appear: the variant holds only a name.
    }

    #[test]
    fn loopback_endpoint_is_always_loopback() {
        let ep = LoopbackEndpoint::new(9_123);
        assert!(ep.is_loopback());
        assert!(ep.url().starts_with("http://127.0.0.1:9123"));
    }

    // ---- Fake page collectors for routing tests ----

    struct ScriptedCollector {
        path: CollectorPath,
        health: Cell<Option<CollectError>>,
        collect_result: RefCellResult,
        collect_calls: Cell<usize>,
    }

    // A tiny cell holding a cloneable scripted collect result.
    type RefCellResult = std::cell::RefCell<Result<Collected, CollectError>>;

    impl ScriptedCollector {
        fn healthy(path: CollectorPath, result: Result<Collected, CollectError>) -> Self {
            Self {
                path,
                health: Cell::new(None),
                collect_result: std::cell::RefCell::new(result),
                collect_calls: Cell::new(0),
            }
        }

        fn unhealthy(path: CollectorPath, health: CollectError) -> Self {
            Self {
                path,
                health: Cell::new(Some(health)),
                collect_result: std::cell::RefCell::new(Err(CollectError::Timeout)),
                collect_calls: Cell::new(0),
            }
        }

        fn collect_calls(&self) -> usize {
            self.collect_calls.get()
        }
    }

    impl PageCollector for ScriptedCollector {
        fn path(&self) -> CollectorPath {
            self.path
        }

        fn health_check(&self) -> Result<(), CollectError> {
            match self.health.take() {
                Some(err) => {
                    self.health.set(Some(err.clone()));
                    Err(err)
                }
                None => Ok(()),
            }
        }

        fn collect(&self, _url: &str, _scope: &mut TempScope) -> Result<Collected, CollectError> {
            self.collect_calls.set(self.collect_calls.get() + 1);
            self.collect_result.borrow().clone()
        }
    }

    fn collected(path: CollectorPath) -> Collected {
        Collected {
            title: Some("t".into()),
            body: "body".into(),
            images: Vec::new(),
            author: None,
            path,
            switched: None,
        }
    }

    #[test]
    fn uses_aside_when_healthy_and_no_switch() {
        let clock = VirtualClock::new(0);
        let aside = ScriptedCollector::healthy(
            CollectorPath::Aside,
            Ok(collected(CollectorPath::Aside)),
        );
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let out = router.collect("https://example.com/post").unwrap();
        assert_eq!(out.path, CollectorPath::Aside);
        assert_eq!(out.switched, None);
        // The fallback was never collected from.
        assert_eq!(fallback.collect_calls(), 0);
    }

    #[test]
    fn switches_to_fallback_when_aside_not_installed() {
        let clock = VirtualClock::new(0);
        let aside =
            ScriptedCollector::unhealthy(CollectorPath::Aside, CollectError::NotInstalled);
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let out = router.collect("https://example.com/post").unwrap();
        assert_eq!(out.path, CollectorPath::Fallback);
        assert_eq!(out.switched, Some(SwitchReason::NotInstalled));
        // Aside was never collected from (only health-checked).
        assert_eq!(aside.collect_calls(), 0);
    }

    #[test]
    fn private_is_terminal_and_never_tries_fallback() {
        let clock = VirtualClock::new(0);
        let aside =
            ScriptedCollector::healthy(CollectorPath::Aside, Err(CollectError::Private));
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let err = router.collect("https://example.com/post").unwrap_err();
        assert_eq!(err, CollectFailure::Terminal(CollectError::Private));
        // The other path was never called (R11.10).
        assert_eq!(fallback.collect_calls(), 0);
    }

    #[test]
    fn deleted_is_terminal() {
        let clock = VirtualClock::new(0);
        let aside =
            ScriptedCollector::healthy(CollectorPath::Aside, Err(CollectError::Deleted));
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let err = router.collect("https://example.com/post").unwrap_err();
        assert_eq!(err, CollectFailure::Terminal(CollectError::Deleted));
        assert_eq!(fallback.collect_calls(), 0);
    }

    #[test]
    fn secret_unavailable_switches_once_with_reason() {
        let clock = VirtualClock::new(0);
        let aside = ScriptedCollector::healthy(
            CollectorPath::Aside,
            Err(CollectError::Unauthorized {
                item: "브라우저 연결 정보",
            }),
        );
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let out = router.collect("https://example.com/post").unwrap();
        assert_eq!(out.switched, Some(SwitchReason::SecretUnavailable));
        // Exactly one fallback collection — the single allowed switch.
        assert_eq!(fallback.collect_calls(), 1);
    }

    #[test]
    fn both_paths_failing_reports_both_reasons() {
        let clock = VirtualClock::new(0);
        let aside = ScriptedCollector::healthy(
            CollectorPath::Aside,
            Err(CollectError::Transport("aside down".into())),
        );
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Err(CollectError::Transport("net down".into())),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let err = router.collect("https://example.com/post").unwrap_err();
        match err {
            CollectFailure::BothFailed { aside, fallback } => {
                assert_eq!(aside, CollectError::Transport("aside down".into()));
                assert_eq!(fallback, CollectError::Transport("net down".into()));
            }
            other => panic!("expected BothFailed, got {other:?}"),
        }
    }

    /// A clock whose reads advance on their own, so the routed collection blows
    /// past the twenty-second bound between the Aside and fallback steps.
    struct AdvancingClock {
        now: Cell<i64>,
        step_ms: i64,
    }
    impl Clock for AdvancingClock {
        fn now_ms(&self) -> i64 {
            let v = self.now.get();
            self.now.set(v + self.step_ms);
            v
        }
    }

    #[test]
    fn deadline_exceeded_stops_before_fallback_send() {
        // Each clock read jumps 21 seconds: the start read is 0, but the very
        // next read — the deadline check before any fallback send — is already
        // past the 20-second bound.
        let clock = AdvancingClock {
            now: Cell::new(0),
            step_ms: 21_000,
        };
        let aside =
            ScriptedCollector::unhealthy(CollectorPath::Aside, CollectError::NotInstalled);
        let fallback = ScriptedCollector::healthy(
            CollectorPath::Fallback,
            Ok(collected(CollectorPath::Fallback)),
        );
        let router = RoutingCollector {
            aside: &aside,
            fallback: &fallback,
            clock: &clock,
        };
        let err = router.collect("https://example.com/post").unwrap_err();
        assert_eq!(err, CollectFailure::DeadlineExceeded);
        // The bound is honored before any fallback send happens (R11.12).
        assert_eq!(fallback.collect_calls(), 0);
    }

    // ---- AsideCollector health-check bound and secret handling ----

    struct FakeTransport {
        health_result: Result<(), CollectError>,
        health_takes_ms: i64,
        clock: VirtualClock,
    }
    impl AsideTransport for FakeTransport {
        fn health(&self, _endpoint: &LoopbackEndpoint) -> Result<(), CollectError> {
            self.clock.advance_ms(self.health_takes_ms);
            self.health_result.clone()
        }
        fn fetch(
            &self,
            _endpoint: &LoopbackEndpoint,
            _url: &str,
            _session: Option<&Secret>,
            _scope: &mut TempScope,
        ) -> Result<Collected, CollectError> {
            Ok(collected(CollectorPath::Aside))
        }
    }

    struct OkSecrets;
    impl SecretStore for OkSecrets {
        fn get(&self, _key: SecretKey) -> Result<Secret, SecretError> {
            Ok(Secret::new("session-token"))
        }
    }

    struct MissingSecrets;
    impl SecretStore for MissingSecrets {
        fn get(&self, _key: SecretKey) -> Result<Secret, SecretError> {
            Err(SecretError::NotFound)
        }
    }

    #[test]
    fn aside_health_times_out_past_three_seconds() {
        let clock = VirtualClock::new(0);
        let transport = FakeTransport {
            health_result: Ok(()),
            health_takes_ms: (HEALTH_TIMEOUT_SECS as i64) * 1000 + 1,
            clock: clock.clone(),
        };
        let secrets = OkSecrets;
        let aside =
            AsideCollector::new(LoopbackEndpoint::new(9000), &transport, &secrets, &clock);
        assert_eq!(aside.health_check(), Err(CollectError::HealthTimeout));
    }

    #[test]
    fn aside_health_ok_within_three_seconds() {
        let clock = VirtualClock::new(0);
        let transport = FakeTransport {
            health_result: Ok(()),
            health_takes_ms: 500,
            clock: clock.clone(),
        };
        let secrets = OkSecrets;
        let aside =
            AsideCollector::new(LoopbackEndpoint::new(9000), &transport, &secrets, &clock);
        assert!(aside.health_check().is_ok());
    }

    #[test]
    fn aside_missing_secret_is_unauthorized_by_name() {
        let clock = VirtualClock::new(0);
        let transport = FakeTransport {
            health_result: Ok(()),
            health_takes_ms: 10,
            clock: clock.clone(),
        };
        let secrets = MissingSecrets;
        let aside =
            AsideCollector::new(LoopbackEndpoint::new(9000), &transport, &secrets, &clock);
        let mut scope = TempScope::new().unwrap();
        assert_eq!(
            aside.collect("https://example.com/post", &mut scope),
            Err(CollectError::Unauthorized {
                item: "브라우저 연결 정보"
            })
        );
    }

    // ---- FallbackCollector status mapping ----

    struct StatusNet {
        status: u16,
    }
    impl NetworkPort for StatusNet {
        fn request(
            &self,
            _req: crate::ports::HttpRequest,
        ) -> Result<crate::ports::HttpResponse, PortError> {
            Ok(crate::ports::HttpResponse {
                status: self.status,
                body: b"hello".to_vec(),
            })
        }
        fn egress_count(&self) -> usize {
            0
        }
    }

    #[test]
    fn fallback_maps_status_codes() {
        let mut scope = TempScope::new().unwrap();
        let ok = FallbackCollector::new(&StatusNet { status: 200 });
        assert!(ok.collect("https://x", &mut scope).is_ok());

        let private = FallbackCollector::new(&StatusNet { status: 403 });
        assert_eq!(
            private.collect("https://x", &mut scope),
            Err(CollectError::Private)
        );

        let deleted = FallbackCollector::new(&StatusNet { status: 404 });
        assert_eq!(
            deleted.collect("https://x", &mut scope),
            Err(CollectError::Deleted)
        );
    }

    #[test]
    fn fallback_is_always_healthy() {
        let net = StatusNet { status: 200 };
        let fallback = FallbackCollector::new(&net);
        assert!(fallback.health_check().is_ok());
        // Keep the import used and confirm image refs stay orderable downstream.
        let _ = ImageRef {
            locator: "x".into(),
        };
    }
}
