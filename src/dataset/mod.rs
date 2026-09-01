//! Dataset builder — turns 최연우's local KakaoTalk conversations into a
//! searchable RAG dataset (R3).
//!
//! The builder extracts three things and stores them in the local dataset
//! store:
//!
//! 1. **Q&A pairs** (R3.4, R3.5): a partner message immediately answered by an
//!    owner (최연우) message becomes a question↔answer pair. Partner messages
//!    that are never answered are kept only as context, never as pairs.
//! 2. **Attachment references** (R3.6, R3.7, R3.8): every link (classified as
//!    news / GitHub / YouTube / other), image, and emoticon is recorded with
//!    its `chat_id`, timestamp, and a stable provenance id.
//! 3. Recipient style summaries feed the honorific/formality derivation that
//!    lives in [`crate::context`] (R3.3).
//!
//! **Safety invariants**
//!
//! * **Local only (R3.12).** [`DatasetBuilder::build`] refuses to run unless the
//!   injected [`Embedder`] reports [`Embedder::is_local_only`] `== true`. The
//!   build performs no network I/O; every derived value stays on the machine.
//! * **Owner excluded (R3.11).** Messages sent by 최연우 are never treated as a
//!   question; they only ever appear as answers or context.
//! * **No raw URLs / paths stored (R3.7, R3.8, R2.8).** Links, images, and
//!   emoticons are linked only through the stable provenance id produced by
//!   [`crate::context::provenance_id`] — the same hashing the redaction layer
//!   uses. Raw URLs and absolute paths are never persisted.
//! * **Fail-closed persistence (R3.2, R3.10).** A missing/failing data source or
//!   zero conversations aborts the build and preserves the previous dataset. A
//!   store failure rolls back so the previous dataset is left untouched.

use std::collections::BTreeMap;
use std::path::Path;

use rusqlite::{params, Connection};
use thiserror::Error;

use crate::context::{self, Embedder};

pub mod refresh;

pub use refresh::{
    DatasetRefresher, LeaseGrant, RefreshConfig, RefreshCursor, RefreshFailure, RefreshLease,
    RefreshOutcome, RefreshReport, RefreshStage, SkipReason,
};

/// What kind of content a single conversation message carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MessageKind {
    /// A plain text message.
    Text,
    /// An image attachment.
    Image,
    /// An emoticon / sticker.
    Emoticon,
}

/// One conversation message drawn from the local KakaoTalk database.
///
/// This is the adapter boundary: a [`ConversationSource`] yields these rows and
/// the builder never touches the raw database directly. `provenance` is a raw
/// source locator (for example `local-db:{chat_id}:{log_id}`) that is hashed to
/// a stable id before anything is persisted — it is never stored verbatim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DatasetMessage {
    /// The chat this message belongs to.
    pub chat_id: i64,
    /// When the message was sent (opaque monotonic timestamp).
    pub at: i64,
    /// Display name of the sender.
    pub sender: String,
    /// True when the sender is the owner (최연우) (R3.11).
    pub is_owner: bool,
    /// What kind of content the message carries.
    pub kind: MessageKind,
    /// The message body. May be empty for image/emoticon rows.
    pub text: String,
    /// Raw provenance locator, hashed before storage (never stored verbatim).
    pub provenance: String,
}

impl DatasetMessage {
    /// Whether this row can act as a Q&A question/answer: a non-empty text row.
    fn is_text(&self) -> bool {
        self.kind == MessageKind::Text && !self.text.trim().is_empty()
    }
}

/// Classification of a link attachment (R3.6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkClass {
    /// A news article.
    News,
    /// A GitHub link.
    GitHub,
    /// A YouTube link.
    YouTube,
    /// Anything else.
    Other,
}

impl LinkClass {
    /// The stable string persisted in the `link_class` column.
    pub fn as_str(self) -> &'static str {
        match self {
            LinkClass::News => "news",
            LinkClass::GitHub => "github",
            LinkClass::YouTube => "youtube",
            LinkClass::Other => "other",
        }
    }
}

/// Which kind of attachment reference a row is (R3.6, R3.7, R3.8).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttachmentKind {
    /// A link shared in a message body.
    Link,
    /// An image attachment.
    Image,
    /// An emoticon / sticker.
    Emoticon,
}

impl AttachmentKind {
    /// The stable string persisted in the `kind` column.
    pub fn as_str(self) -> &'static str {
        match self {
            AttachmentKind::Link => "link",
            AttachmentKind::Image => "image",
            AttachmentKind::Emoticon => "emoticon",
        }
    }
}

/// A single extracted Q&A pair, ready to persist. Both sides are linked only by
/// stable provenance ids; raw bodies are never carried here.
#[derive(Debug, Clone, PartialEq)]
pub struct BuiltQaPair {
    /// Stable key for the recipient (partner) this exchange is with.
    pub recipient_key: String,
    /// Stable provenance id of the partner (question) message.
    pub question_pid: String,
    /// Stable provenance id of the owner (answer) message.
    pub answer_pid: String,
    /// Deterministic local embedding of the question text.
    pub vector: Vec<f32>,
    /// When the answer was sent.
    pub at: i64,
}

/// A single extracted attachment reference, ready to persist.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuiltAttachment {
    /// The chat the attachment appeared in (R3.6/7/8).
    pub chat_id: i64,
    /// When the attachment appeared (R3.6/7/8).
    pub at: i64,
    /// The kind of attachment.
    pub kind: AttachmentKind,
    /// Link classification, present only for links.
    pub link_class: Option<LinkClass>,
    /// A dataset-internal stable reference (never a raw URL or path).
    pub local_ref: String,
    /// Stable provenance id (never the raw URL / path).
    pub provenance_id: String,
}

/// The fully built, in-memory dataset before it is persisted.
#[derive(Debug, Clone, Default)]
pub struct BuiltDataset {
    /// All extracted Q&A pairs.
    pub qa_pairs: Vec<BuiltQaPair>,
    /// All extracted attachment references.
    pub attachments: Vec<BuiltAttachment>,
    /// Number of distinct recipients that have at least one Q&A pair.
    pub recipients_profiled: usize,
    /// Number of partner text messages excluded from pairs (context only, R3.5).
    pub skipped_no_answer: usize,
}

/// Summary returned by a successful [`DatasetBuilder::build`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DatasetBuildReport {
    /// How many Q&A pairs were stored.
    pub qa_pairs: usize,
    /// How many attachment references were stored.
    pub attachments: usize,
    /// How many recipients were profiled.
    pub recipients_profiled: usize,
    /// How many partner messages were kept as context only (R3.5).
    pub skipped_no_answer: usize,
}

/// Errors the dataset builder can return. Each carries a plain-language Korean
/// message suitable for showing a beginner (R3.2, R3.10, R3.12, R10.3).
#[derive(Debug, Error)]
pub enum DatasetError {
    /// The embedder is not local-only, so the build is refused (R3.12).
    #[error("데이터셋은 로컬 전용 임베더로만 만들 수 있어요. 외부 임베딩은 사용할 수 없어요.")]
    NonLocalEmbedder,
    /// The conversation source could not be read (R3.2).
    #[error("카카오톡 기록을 열 수 없어요: {0}. 카카오톡이 켜져 있는지 확인한 뒤 다시 만들어 주세요.")]
    SourceUnavailable(String),
    /// There were no conversations to build from (R3.2).
    #[error("만들 대화 기록이 없어요. 카카오톡에서 대화를 나눈 뒤 다시 만들어 주세요.")]
    NoConversations,
    /// The dataset could not be stored (R3.10).
    #[error("데이터셋을 저장하지 못했어요: {0}. 이전 데이터는 그대로 두었어요. 잠시 뒤 다시 시도해 주세요.")]
    StoreFailure(String),
}

/// A source of conversation messages. Real code wraps the local KakaoTalk DB;
/// tests inject a fake in-memory source so no live database is touched.
pub trait ConversationSource {
    /// Load every conversation message the owner participated in.
    fn load(&self) -> Result<Vec<DatasetMessage>, DatasetError>;
}

/// In-memory conversation source for tests and fakes.
#[derive(Debug, Clone, Default)]
pub struct InMemoryConversationSource {
    messages: Vec<DatasetMessage>,
}

impl InMemoryConversationSource {
    /// Build a source from a fixed set of messages.
    pub fn new(messages: Vec<DatasetMessage>) -> Self {
        Self { messages }
    }
}

impl ConversationSource for InMemoryConversationSource {
    fn load(&self) -> Result<Vec<DatasetMessage>, DatasetError> {
        Ok(self.messages.clone())
    }
}

/// Persistence boundary for a built dataset. Implementations must be
/// fail-closed: a failure leaves the previous dataset untouched (R3.10).
pub trait DatasetSink {
    /// Persist the dataset atomically, replacing any previous contents.
    fn store(&mut self, dataset: &BuiltDataset) -> Result<(), DatasetError>;
}

/// Extract every `http`/`https` URL from a message body.
fn extract_urls(text: &str) -> Vec<String> {
    let mut urls = Vec::new();
    for token in text.split(|c: char| c.is_whitespace()) {
        let trimmed = token.trim_matches(|c: char| {
            matches!(
                c,
                '(' | ')' | '<' | '>' | '"' | '\'' | ',' | '。' | '，' | '」' | '「' | '】' | '【'
            )
        });
        let lower = trimmed.to_ascii_lowercase();
        if lower.starts_with("http://") || lower.starts_with("https://") {
            urls.push(trimmed.to_string());
        }
    }
    urls
}

/// Classify a link into one of the tracked buckets (R3.6).
pub fn classify_link(url: &str) -> LinkClass {
    let lower = url.to_ascii_lowercase();
    if lower.contains("github.com")
        || lower.contains("githubusercontent.com")
        || lower.contains("gist.github")
    {
        LinkClass::GitHub
    } else if lower.contains("youtube.com") || lower.contains("youtu.be") {
        LinkClass::YouTube
    } else if lower.contains("news") {
        LinkClass::News
    } else {
        LinkClass::Other
    }
}

/// A short, dataset-internal stable reference derived from a provenance id.
/// Contains only hex from the hash, so it can never carry a raw URL or path.
fn local_ref(kind: AttachmentKind, provenance_id: &str) -> String {
    // provenance_id looks like `source:<hex>`; keep a bounded hex slice.
    let hex = provenance_id
        .rsplit(':')
        .next()
        .unwrap_or(provenance_id)
        .chars()
        .filter(|c| c.is_ascii_hexdigit())
        .take(16)
        .collect::<String>();
    format!("{}:{}", kind.as_str(), hex)
}

/// Build the dataset in memory from a chronological message stream.
///
/// Messages are grouped per chat and walked in `(at, provenance)` order. Within
/// a chat, a partner text message becomes the pending question; the next owner
/// text message answers it and forms a pair. A partner message that is
/// overwritten by a later partner message or never answered is counted as
/// context-only (R3.5). Owner messages are never questions (R3.11).
fn build_in_memory(messages: &[DatasetMessage], embedder: &dyn Embedder) -> BuiltDataset {
    let mut by_chat: BTreeMap<i64, Vec<&DatasetMessage>> = BTreeMap::new();
    for message in messages {
        by_chat.entry(message.chat_id).or_default().push(message);
    }

    let mut qa_pairs = Vec::new();
    let mut attachments = Vec::new();
    let mut skipped_no_answer = 0usize;
    let mut recipients: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

    for chat_messages in by_chat.values_mut() {
        chat_messages.sort_by(|a, b| a.at.cmp(&b.at).then_with(|| a.provenance.cmp(&b.provenance)));

        let mut pending_question: Option<&DatasetMessage> = None;
        for message in chat_messages.iter().copied() {
            // Attachment extraction happens for every row regardless of pairing.
            collect_attachments(message, &mut attachments);

            if !message.is_text() {
                continue;
            }

            if message.is_owner {
                if let Some(question) = pending_question.take() {
                    let recipient_key = context::provenance_id(&format!("recipient:{}", question.sender));
                    recipients.insert(recipient_key.clone());
                    qa_pairs.push(BuiltQaPair {
                        recipient_key,
                        question_pid: context::provenance_id(&question.provenance),
                        answer_pid: context::provenance_id(&message.provenance),
                        vector: embedder.embed(&question.text),
                        at: message.at,
                    });
                }
                // Owner messages with no pending question are context only.
            } else {
                // A new partner message. If one was already pending unanswered,
                // that earlier one is context-only (R3.5).
                if pending_question.is_some() {
                    skipped_no_answer += 1;
                }
                pending_question = Some(message);
            }
        }

        // Any partner question still pending at the end was never answered.
        if pending_question.is_some() {
            skipped_no_answer += 1;
        }
    }

    BuiltDataset {
        qa_pairs,
        attachments,
        recipients_profiled: recipients.len(),
        skipped_no_answer,
    }
}

/// Collect attachment references for one message (links from text, plus image /
/// emoticon rows). Never stores raw URLs or paths (R3.7, R3.8).
fn collect_attachments(message: &DatasetMessage, out: &mut Vec<BuiltAttachment>) {
    match message.kind {
        MessageKind::Image => {
            let provenance_id = context::provenance_id(&message.provenance);
            out.push(BuiltAttachment {
                chat_id: message.chat_id,
                at: message.at,
                kind: AttachmentKind::Image,
                link_class: None,
                local_ref: local_ref(AttachmentKind::Image, &provenance_id),
                provenance_id,
            });
        }
        MessageKind::Emoticon => {
            let provenance_id = context::provenance_id(&message.provenance);
            out.push(BuiltAttachment {
                chat_id: message.chat_id,
                at: message.at,
                kind: AttachmentKind::Emoticon,
                link_class: None,
                local_ref: local_ref(AttachmentKind::Emoticon, &provenance_id),
                provenance_id,
            });
        }
        MessageKind::Text => {}
    }

    // Links can ride along with any message body.
    for (index, url) in extract_urls(&message.text).into_iter().enumerate() {
        // Hash the raw URL to a stable id; the URL itself is never stored.
        let provenance_id = context::provenance_id(&format!("{}#{index}", url));
        out.push(BuiltAttachment {
            chat_id: message.chat_id,
            at: message.at,
            kind: AttachmentKind::Link,
            link_class: Some(classify_link(&url)),
            local_ref: local_ref(AttachmentKind::Link, &provenance_id),
            provenance_id,
        });
    }
}

/// The dataset builder. Generic over an injected source and sink so tests use
/// fakes and never touch a live database or network (R3.12).
pub struct DatasetBuilder<'a> {
    source: &'a dyn ConversationSource,
    sink: &'a mut dyn DatasetSink,
}

impl<'a> DatasetBuilder<'a> {
    /// Create a builder over a source and sink.
    pub fn new(source: &'a dyn ConversationSource, sink: &'a mut dyn DatasetSink) -> Self {
        Self { source, sink }
    }

    /// Build and store the dataset.
    ///
    /// * Rejects immediately if the embedder is not local-only (R3.12).
    /// * Aborts on a source failure or zero conversations, preserving the
    ///   previous dataset (R3.2).
    /// * Aborts on a store failure, preserving the previous dataset (R3.10).
    pub fn build(&mut self, embedder: &dyn Embedder) -> Result<DatasetBuildReport, DatasetError> {
        if !embedder.is_local_only() {
            return Err(DatasetError::NonLocalEmbedder);
        }

        let messages = self.source.load()?;
        if messages.is_empty() {
            return Err(DatasetError::NoConversations);
        }

        let built = build_in_memory(&messages, embedder);
        self.sink.store(&built)?;

        Ok(DatasetBuildReport {
            qa_pairs: built.qa_pairs.len(),
            attachments: built.attachments.len(),
            recipients_profiled: built.recipients_profiled,
            skipped_no_answer: built.skipped_no_answer,
        })
    }
}

/// Convert a float vector to little-endian bytes for BLOB storage.
fn vector_to_bytes(vector: &[f32]) -> Vec<u8> {
    vector.iter().flat_map(|v| v.to_le_bytes()).collect()
}

/// SQLite-backed [`DatasetSink`] over the `qa_pair` and `attachment_reference`
/// tables. Persists inside a single transaction so a failure rolls back and the
/// previous dataset is preserved (R3.10).
pub struct SqliteDatasetSink {
    conn: Connection,
}

impl SqliteDatasetSink {
    /// Wrap an existing connection, ensuring the schema is present.
    pub fn new(conn: Connection) -> Result<Self, DatasetError> {
        ensure_schema(&conn).map_err(|e| DatasetError::StoreFailure(e.to_string()))?;
        Ok(Self { conn })
    }

    /// Open (or create) a store at `path`.
    pub fn open(path: &Path) -> Result<Self, DatasetError> {
        let conn = Connection::open(path).map_err(|e| DatasetError::StoreFailure(e.to_string()))?;
        Self::new(conn)
    }

    /// Open an in-memory store. Primarily for tests.
    pub fn open_in_memory() -> Result<Self, DatasetError> {
        let conn = Connection::open_in_memory()
            .map_err(|e| DatasetError::StoreFailure(e.to_string()))?;
        Self::new(conn)
    }
}

/// Create the `qa_pair` and `attachment_reference` tables if they do not exist.
fn ensure_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS qa_pair(
            id INTEGER PRIMARY KEY,
            recipient_key TEXT NOT NULL,
            question_pid TEXT NOT NULL,
            answer_pid TEXT NOT NULL,
            vector BLOB,
            at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_qa_pair_recipient
            ON qa_pair(recipient_key, at);
        CREATE TABLE IF NOT EXISTS attachment_reference(
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            at INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('link', 'image', 'emoticon')),
            link_class TEXT,
            local_ref TEXT,
            provenance_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachment_reference_chat
            ON attachment_reference(chat_id, at);",
    )
}

impl DatasetSink for SqliteDatasetSink {
    fn store(&mut self, dataset: &BuiltDataset) -> Result<(), DatasetError> {
        let tx = self
            .conn
            .transaction()
            .map_err(|e| DatasetError::StoreFailure(e.to_string()))?;

        let result = (|| -> rusqlite::Result<()> {
            // Rebuilding replaces the previous dataset atomically.
            tx.execute("DELETE FROM qa_pair", [])?;
            tx.execute("DELETE FROM attachment_reference", [])?;

            for pair in &dataset.qa_pairs {
                tx.execute(
                    "INSERT INTO qa_pair(recipient_key, question_pid, answer_pid, vector, at)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        pair.recipient_key,
                        pair.question_pid,
                        pair.answer_pid,
                        vector_to_bytes(&pair.vector),
                        pair.at,
                    ],
                )?;
            }

            for attachment in &dataset.attachments {
                tx.execute(
                    "INSERT INTO attachment_reference(
                        chat_id, at, kind, link_class, local_ref, provenance_id
                     ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        attachment.chat_id,
                        attachment.at,
                        attachment.kind.as_str(),
                        attachment.link_class.map(|c| c.as_str()),
                        attachment.local_ref,
                        attachment.provenance_id,
                    ],
                )?;
            }
            Ok(())
        })();

        match result {
            Ok(()) => tx
                .commit()
                .map_err(|e| DatasetError::StoreFailure(e.to_string())),
            Err(e) => {
                // Transaction drops without commit → previous dataset preserved.
                drop(tx);
                Err(DatasetError::StoreFailure(e.to_string()))
            }
        }
    }
}

#[cfg(test)]
mod tests;
