//! `okc dataset build --local-only` — build the local RAG dataset (R3).
//!
//! This wires the real KakaoTalk local database (read-only) to the
//! [`DatasetBuilder`], using the deterministic local-only embedder. It never
//! contacts any server: the source is a local SQLCipher read and the store is
//! the local `context.sqlite3` file (R3.12).

use anyhow::Result;
use serde_json::json;

use openkakao_cli::context::LocalHashEmbedder;
use openkakao_cli::dataset::{
    ConversationSource, DatasetBuilder, DatasetError, DatasetMessage, MessageKind,
    SqliteDatasetSink,
};

use crate::local_db::LocalDbReader;

/// How many chats and messages-per-chat to read when building the dataset.
const MAX_CHATS: usize = 200;
const MAX_MESSAGES_PER_CHAT: usize = 5_000;

/// A [`ConversationSource`] backed by the local KakaoTalk database. Read-only
/// and offline: it opens the database in no-mutation mode and never writes.
struct LocalDbConversationSource;

/// Map a KakaoTalk message type to a coarse dataset message kind. Only the low
/// 8 bits matter (KakaoTalk ORs flag bits onto the type).
fn message_kind(message_type: i32) -> MessageKind {
    match message_type & 0xff {
        2 | 27 => MessageKind::Image,
        6 | 12 | 14 | 20 => MessageKind::Emoticon,
        _ => MessageKind::Text,
    }
}

impl ConversationSource for LocalDbConversationSource {
    fn load(&self) -> Result<Vec<DatasetMessage>, DatasetError> {
        let reader = LocalDbReader::open_no_mutation()
            .map_err(|e| DatasetError::SourceUnavailable(e.to_string()))?;
        let chats = reader
            .list_chats(MAX_CHATS)
            .map_err(|e| DatasetError::SourceUnavailable(e.to_string()))?;

        let mut messages = Vec::new();
        for chat in chats {
            let rows = reader
                .read_messages(chat.chat_id, MAX_MESSAGES_PER_CHAT, None)
                .map_err(|e| DatasetError::SourceUnavailable(e.to_string()))?;
            for row in rows {
                messages.push(DatasetMessage {
                    chat_id: row.chat_id,
                    at: row.sent_at,
                    sender: row.sender_name,
                    is_owner: row.is_self,
                    kind: message_kind(row.message_type),
                    text: row.message,
                    // Raw locator; hashed to a stable id before anything is stored.
                    provenance: format!("local-db:{}:{}", row.chat_id, row.log_id),
                });
            }
        }
        Ok(messages)
    }
}

/// Build the local RAG dataset. `local_only` is an explicit acknowledgment that
/// this is the only supported mode; the build always uses a local-only embedder
/// and performs no network I/O (R3.12).
pub fn cmd_dataset_build(local_only: bool, json: bool) -> Result<()> {
    // Local-only is the only supported mode. Passing the flag makes the intent
    // explicit; either way the embedder is local-only and nothing leaves the
    // machine.
    let _ = local_only;

    let embedder = LocalHashEmbedder::new();
    let source = LocalDbConversationSource;
    let db_path = openkakao_cli::context::default_db_path();

    let build_result = SqliteDatasetSink::open(&db_path).and_then(|mut sink| {
        let mut builder = DatasetBuilder::new(&source, &mut sink);
        builder.build(&embedder)
    });

    match build_result {
        Ok(report) => {
            if json {
                println!(
                    "{}",
                    serde_json::to_string(&json!({
                        "ok": true,
                        "action": "dataset_build",
                        "local_only": true,
                        "network": false,
                        "qa_pairs": report.qa_pairs,
                        "attachments": report.attachments,
                        "recipients_profiled": report.recipients_profiled,
                        "skipped_no_answer": report.skipped_no_answer,
                    }))?
                );
            } else {
                println!("데이터셋을 만들었어요 (로컬 전용, 서버 접속 없음).");
                println!("  질문-답변 쌍: {}개", report.qa_pairs);
                println!("  첨부 근거: {}개", report.attachments);
                println!("  말투를 익힌 상대: {}명", report.recipients_profiled);
                println!("  답변이 없어 맥락으로만 둔 메시지: {}개", report.skipped_no_answer);
            }
            Ok(())
        }
        Err(error) => {
            report_build_error(json, &error);
            Err(anyhow::anyhow!(error.to_string()))
        }
    }
}

/// Print a build failure. The plain-language message goes to stderr for a human;
/// JSON callers also get a structured error on stdout.
fn report_build_error(json: bool, error: &DatasetError) {
    if json {
        let payload = json!({
            "ok": false,
            "action": "dataset_build",
            "local_only": true,
            "network": false,
            "error": error.to_string(),
        });
        if let Ok(text) = serde_json::to_string(&payload) {
            println!("{text}");
        }
    }
    eprintln!("{error}");
}
