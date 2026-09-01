//! Unit tests for the dataset builder (Tasks 5.1, 5.2, 5.3).

use super::*;
use crate::context::LocalHashEmbedder;

/// The owner display name used by these tests. The production `OWNER_NAME`
/// constant was removed in favor of an injected `UserProfile::owner_display_name`
/// (task 3.1); the builder is owner-agnostic and takes the name via message
/// `is_owner` flags, so a fixed local value is enough here.
const OWNER_NAME: &str = "최연우";

fn msg(
    chat_id: i64,
    at: i64,
    sender: &str,
    is_owner: bool,
    kind: MessageKind,
    text: &str,
) -> DatasetMessage {
    DatasetMessage {
        chat_id,
        at,
        sender: sender.to_string(),
        is_owner,
        kind,
        text: text.to_string(),
        provenance: format!("local-db:{chat_id}:{at}:{sender}"),
    }
}

fn partner(chat_id: i64, at: i64, sender: &str, text: &str) -> DatasetMessage {
    msg(chat_id, at, sender, false, MessageKind::Text, text)
}

fn owner(chat_id: i64, at: i64, text: &str) -> DatasetMessage {
    msg(chat_id, at, OWNER_NAME, true, MessageKind::Text, text)
}

/// A non-local embedder used only to exercise the local-only rejection.
struct NonLocalEmbedder;
impl Embedder for NonLocalEmbedder {
    fn dim(&self) -> usize {
        8
    }
    fn embed(&self, _text: &str) -> Vec<f32> {
        vec![0.0; 8]
    }
    fn is_local_only(&self) -> bool {
        false
    }
}

struct FailingSource;
impl ConversationSource for FailingSource {
    fn load(&self) -> Result<Vec<DatasetMessage>, DatasetError> {
        Err(DatasetError::SourceUnavailable("database locked".into()))
    }
}

/// A sink that records nothing and always fails, used to prove build aborts.
struct FailingSink;
impl DatasetSink for FailingSink {
    fn store(&mut self, _dataset: &BuiltDataset) -> Result<(), DatasetError> {
        Err(DatasetError::StoreFailure("vector store offline".into()))
    }
}

/// A sink that just captures the built dataset for inspection.
#[derive(Default)]
struct CapturingSink {
    last: Option<BuiltDataset>,
}
impl DatasetSink for CapturingSink {
    fn store(&mut self, dataset: &BuiltDataset) -> Result<(), DatasetError> {
        self.last = Some(dataset.clone());
        Ok(())
    }
}

// ---- Task 5.1: Q&A pair extraction ---------------------------------------

#[test]
fn partner_then_owner_forms_a_pair() {
    let messages = vec![
        partner(1, 10, "민수", "회의 언제 할까"),
        owner(1, 20, "오후 3시 어때"),
    ];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    assert_eq!(built.qa_pairs.len(), 1);
    assert_eq!(built.skipped_no_answer, 0);
    assert_eq!(built.recipients_profiled, 1);
    // The pair links only stable ids, never raw text.
    let pair = &built.qa_pairs[0];
    assert!(pair.question_pid.starts_with("source:"));
    assert!(pair.answer_pid.starts_with("source:"));
    assert!(!pair.vector.is_empty());
}

#[test]
fn unanswered_partner_message_is_context_only() {
    let messages = vec![partner(1, 10, "민수", "거기 있어?")];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    assert_eq!(built.qa_pairs.len(), 0);
    assert_eq!(built.skipped_no_answer, 1);
    assert_eq!(built.recipients_profiled, 0);
}

#[test]
fn earlier_partner_message_without_reply_is_skipped() {
    // Two partner messages in a row; only the latest is answered.
    let messages = vec![
        partner(1, 10, "민수", "첫 질문"),
        partner(1, 15, "민수", "두 번째 질문"),
        owner(1, 20, "두 번째에 답할게"),
    ];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    assert_eq!(built.qa_pairs.len(), 1);
    // The first partner message was overwritten before any reply → context only.
    assert_eq!(built.skipped_no_answer, 1);
}

#[test]
fn owner_message_without_preceding_partner_makes_no_pair() {
    let messages = vec![owner(1, 10, "그냥 혼잣말"), owner(1, 20, "또 혼잣말")];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    assert_eq!(built.qa_pairs.len(), 0);
    assert_eq!(built.skipped_no_answer, 0);
}

#[test]
fn pairs_are_scoped_per_chat() {
    let messages = vec![
        partner(1, 10, "민수", "1번방 질문"),
        partner(2, 11, "지현", "2번방 질문"),
        owner(1, 20, "1번방 답"),
        owner(2, 21, "2번방 답"),
    ];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    assert_eq!(built.qa_pairs.len(), 2);
    assert_eq!(built.recipients_profiled, 2);
    assert_eq!(built.skipped_no_answer, 0);
}

// ---- Task 5.2: attachment references -------------------------------------

#[test]
fn classifies_link_kinds() {
    assert_eq!(
        classify_link("https://github.com/rust-lang/rust"),
        LinkClass::GitHub
    );
    assert_eq!(
        classify_link("https://youtu.be/dQw4w9WgXcQ"),
        LinkClass::YouTube
    );
    assert_eq!(
        classify_link("https://news.naver.com/article/123"),
        LinkClass::News
    );
    assert_eq!(classify_link("https://example.com/page"), LinkClass::Other);
}

#[test]
fn extracts_links_images_and_emoticons() {
    let messages = vec![
        partner(
            1,
            10,
            "민수",
            "이거 봐 https://github.com/foo/bar 그리고 https://youtu.be/xyz",
        ),
        msg(1, 20, OWNER_NAME, true, MessageKind::Image, ""),
        msg(1, 30, "민수", false, MessageKind::Emoticon, ""),
    ];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    let kinds: Vec<_> = built.attachments.iter().map(|a| a.kind).collect();
    assert_eq!(
        built.attachments.len(),
        4,
        "two links + one image + one emoticon"
    );
    assert!(kinds.contains(&AttachmentKind::Link));
    assert!(kinds.contains(&AttachmentKind::Image));
    assert!(kinds.contains(&AttachmentKind::Emoticon));
    let link_classes: Vec<_> = built
        .attachments
        .iter()
        .filter_map(|a| a.link_class)
        .collect();
    assert!(link_classes.contains(&LinkClass::GitHub));
    assert!(link_classes.contains(&LinkClass::YouTube));
}

#[test]
fn attachments_never_store_raw_urls_or_paths() {
    let messages = vec![partner(
        1,
        10,
        "민수",
        "https://news.naver.com/read?id=42&secret=/Users/me/x",
    )];
    let built = build_in_memory(&messages, &LocalHashEmbedder);
    for attachment in &built.attachments {
        assert!(!attachment.provenance_id.contains("http"));
        assert!(!attachment.provenance_id.contains('/'));
        assert!(!attachment.local_ref.contains("http"));
        assert!(!attachment.local_ref.contains('/'));
        assert!(attachment.provenance_id.starts_with("source:"));
    }
}

// ---- Task 5.3: build orchestration, errors, persistence ------------------

#[test]
fn build_rejects_non_local_embedder() {
    let source = InMemoryConversationSource::new(vec![partner(1, 1, "민수", "hi")]);
    let mut sink = CapturingSink::default();
    let mut builder = DatasetBuilder::new(&source, &mut sink);
    let err = builder.build(&NonLocalEmbedder).unwrap_err();
    assert!(matches!(err, DatasetError::NonLocalEmbedder));
    // Nothing was built / stored.
    assert!(sink.last.is_none());
}

#[test]
fn build_aborts_on_source_failure() {
    let source = FailingSource;
    let mut sink = CapturingSink::default();
    let mut builder = DatasetBuilder::new(&source, &mut sink);
    let err = builder.build(&LocalHashEmbedder).unwrap_err();
    assert!(matches!(err, DatasetError::SourceUnavailable(_)));
    assert!(sink.last.is_none());
}

#[test]
fn build_aborts_on_zero_conversations() {
    let source = InMemoryConversationSource::new(vec![]);
    let mut sink = CapturingSink::default();
    let mut builder = DatasetBuilder::new(&source, &mut sink);
    let err = builder.build(&LocalHashEmbedder).unwrap_err();
    assert!(matches!(err, DatasetError::NoConversations));
    assert!(sink.last.is_none());
}

#[test]
fn build_aborts_on_store_failure() {
    let source = InMemoryConversationSource::new(vec![
        partner(1, 10, "민수", "질문"),
        owner(1, 20, "답변"),
    ]);
    let mut sink = FailingSink;
    let mut builder = DatasetBuilder::new(&source, &mut sink);
    let err = builder.build(&LocalHashEmbedder).unwrap_err();
    assert!(matches!(err, DatasetError::StoreFailure(_)));
}

#[test]
fn sqlite_sink_round_trip_counts() {
    let source = InMemoryConversationSource::new(vec![
        partner(1, 10, "민수", "회의 https://github.com/foo/bar"),
        owner(1, 20, "확인했어"),
        msg(1, 30, "민수", false, MessageKind::Emoticon, ""),
    ]);
    let mut sink = SqliteDatasetSink::open_in_memory().unwrap();
    let report = {
        let mut builder = DatasetBuilder::new(&source, &mut sink);
        builder.build(&LocalHashEmbedder).unwrap()
    };
    assert_eq!(report.qa_pairs, 1);
    assert_eq!(report.attachments, 2); // one link + one emoticon
    assert_eq!(report.recipients_profiled, 1);

    // Verify rows actually landed.
    let qa_count: i64 = sink
        .conn
        .query_row("SELECT COUNT(*) FROM qa_pair", [], |r| r.get(0))
        .unwrap();
    let attach_count: i64 = sink
        .conn
        .query_row("SELECT COUNT(*) FROM attachment_reference", [], |r| r.get(0))
        .unwrap();
    assert_eq!(qa_count, 1);
    assert_eq!(attach_count, 2);
}

#[test]
fn sqlite_sink_failure_preserves_previous_dataset() {
    let dir = tempfile::tempdir().unwrap();
    let db_path = dir.path().join("dataset.sqlite3");

    // First build: two pairs plus attachments, stored successfully.
    let source_a = InMemoryConversationSource::new(vec![
        partner(1, 10, "민수", "질문1 https://github.com/foo/bar"),
        owner(1, 20, "답1"),
        partner(1, 30, "민수", "질문2"),
        owner(1, 40, "답2"),
    ]);
    {
        let mut sink = SqliteDatasetSink::open(&db_path).unwrap();
        let mut builder = DatasetBuilder::new(&source_a, &mut sink);
        let report = builder.build(&LocalHashEmbedder).unwrap();
        assert_eq!(report.qa_pairs, 2);
    }

    // A separate connection installs a trigger that fails every attachment
    // insert, then confirms the first build's rows are present.
    let checker = Connection::open(&db_path).unwrap();
    let qa_before: i64 = checker
        .query_row("SELECT COUNT(*) FROM qa_pair", [], |r| r.get(0))
        .unwrap();
    let attach_before: i64 = checker
        .query_row("SELECT COUNT(*) FROM attachment_reference", [], |r| r.get(0))
        .unwrap();
    assert_eq!(qa_before, 2);
    assert!(attach_before >= 1);
    checker
        .execute_batch(
            "CREATE TRIGGER fail_attach BEFORE INSERT ON attachment_reference
             BEGIN SELECT RAISE(ABORT, 'boom'); END;",
        )
        .unwrap();

    // Second build has an attachment → the store hits the trigger and aborts.
    let source_b = InMemoryConversationSource::new(vec![
        partner(1, 100, "지현", "새 질문 https://youtu.be/xyz"),
        owner(1, 110, "새 답"),
    ]);
    {
        let mut sink = SqliteDatasetSink::open(&db_path).unwrap();
        let mut builder = DatasetBuilder::new(&source_b, &mut sink);
        let err = builder.build(&LocalHashEmbedder).unwrap_err();
        assert!(matches!(err, DatasetError::StoreFailure(_)));
    }

    // Previous dataset is fully preserved: the DELETE + INSERT rolled back.
    let qa_after: i64 = checker
        .query_row("SELECT COUNT(*) FROM qa_pair", [], |r| r.get(0))
        .unwrap();
    let attach_after: i64 = checker
        .query_row("SELECT COUNT(*) FROM attachment_reference", [], |r| r.get(0))
        .unwrap();
    assert_eq!(qa_after, qa_before, "qa_pair rows must be preserved");
    assert_eq!(
        attach_after, attach_before,
        "attachment rows must be preserved"
    );
}
