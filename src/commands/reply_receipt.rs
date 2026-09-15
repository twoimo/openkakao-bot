//! `openkakao-cli reply-receipt` — the record window's read-only data source.
//!
//! The menu bar used to build its record lines in Python from the raw ledger,
//! which meant the Korean wording and the "was this turn healthy" judgement
//! lived in whichever layer happened to read the file. Both now come from
//! [`crate::reply_receipt`], a pure module, so the same ledger produces the same
//! sentence everywhere.
//!
//! The command is deliberately independent of `load_config()`: a broken config
//! must not hide the evidence about what the broken config did. `main` routes
//! it before the config load for exactly that reason.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

// Reached from both the library and the binary crate root, so the path goes
// through the library name rather than `crate::`.
use openkakao_cli::reply_receipt::{parse_ledger, render_room, DEFAULT_RECEIPT_LIMIT};

/// Where a room's ledger lives under a state root.
pub fn ledger_path(state_root: &Path, chat_id: &str) -> PathBuf {
    state_root
        .join("rooms")
        .join(chat_id)
        .join("reply-evidence.jsonl")
}

fn read_ledger(path: &Path) -> Result<Option<String>> {
    match std::fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).with_context(|| format!("read {}", path.display())),
    }
}

/// Collect one payload per requested room. A room with no ledger is reported as
/// "missing" rather than as an empty room, because "no file yet" and "a file
/// with no turns" are different facts.
pub fn collect(
    ledger: Option<&Path>,
    state_root: Option<&Path>,
    chat_ids: &[String],
    limit: usize,
) -> Result<Vec<serde_json::Value>> {
    let limit = if limit == 0 {
        DEFAULT_RECEIPT_LIMIT
    } else {
        limit
    };
    let mut targets: Vec<(String, PathBuf)> = Vec::new();
    match (ledger, state_root) {
        (Some(path), _) => targets.push((String::new(), path.to_path_buf())),
        (None, Some(root)) => {
            for chat_id in chat_ids {
                targets.push((chat_id.clone(), ledger_path(root, chat_id)));
            }
        }
        (None, None) => anyhow::bail!("pass --ledger or --state-root"),
    }

    let mut rooms = Vec::new();
    for (chat_id, path) in targets {
        let Some(text) = read_ledger(&path)? else {
            rooms.push(serde_json::json!({
                "chat_id": chat_id,
                "ledger": path.to_string_lossy(),
                "missing": true,
                "count": 0,
                "needs_attention": 0,
                "lines": [],
                "receipts": [],
            }));
            continue;
        };
        let records = parse_ledger(&text);
        let chat = if chat_id.is_empty() {
            path.parent()
                .and_then(Path::file_name)
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default()
        } else {
            chat_id
        };
        let mut payload = render_room(&chat, &records, limit);
        payload["ledger"] = serde_json::json!(path.to_string_lossy());
        payload["missing"] = serde_json::json!(false);
        // The window matches a receipt page to a room row by this id, so it is
        // always present: the requested id, or the directory name when the
        // caller passed a bare --ledger path.
        payload["chat_id"] = serde_json::json!(chat);
        rooms.push(payload);
    }
    Ok(rooms)
}

pub fn run(
    ledger: Option<String>,
    state_root: Option<String>,
    chat_ids: &[String],
    limit: usize,
    json: bool,
) -> Result<()> {
    let ledger_path = ledger.map(PathBuf::from);
    let state_root = state_root.map(PathBuf::from);
    let rooms = collect(
        ledger_path.as_deref(),
        state_root.as_deref(),
        chat_ids,
        limit,
    )?;
    if json {
        if rooms.len() == 1 {
            println!("{}", serde_json::to_string_pretty(&rooms[0])?);
        } else {
            println!("{}", serde_json::to_string_pretty(&rooms)?);
        }
        return Ok(());
    }
    for room in &rooms {
        if room["missing"] == serde_json::json!(true) {
            println!("(기록 파일 없음) {}", room["ledger"]);
            continue;
        }
        for line in room["lines"].as_array().into_iter().flatten() {
            println!("{}", line.as_str().unwrap_or_default());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ledger_path_follows_the_room_layout() {
        let path = ledger_path(Path::new("/tmp/state"), "417780809780519");
        assert!(path.ends_with("rooms/417780809780519/reply-evidence.jsonl"));
    }

    #[test]
    fn a_room_without_a_ledger_is_reported_as_missing() {
        let dir = std::env::temp_dir().join("openkakao-reply-receipt-test");
        std::fs::create_dir_all(&dir).unwrap();
        let rooms = collect(None, Some(&dir), &["404".to_string()], 10).unwrap();
        assert_eq!(rooms.len(), 1);
        assert_eq!(rooms[0]["missing"], serde_json::json!(true));
    }

    #[test]
    fn an_existing_ledger_is_summarized_without_raw_content() {
        let dir = std::env::temp_dir().join("openkakao-reply-receipt-test2");
        let room = dir.join("rooms/55");
        std::fs::create_dir_all(&room).unwrap();
        let line = serde_json::json!({
            "recorded_at": "2026-09-15T09:42:11Z",
            "event_id": "evt-1",
            "chat": "부자멘토멘티",
            "status": "scheduled",
            "decision": "reply",
            "reason": "direct_question",
            "reply": "네",
            "retrieval": {"attempted": true, "error": null, "evidence_ids": 4},
            "prompt_evidence_ids": 4,
        });
        std::fs::write(ledger_path(&dir, "55"), format!("{line}\n")).unwrap();
        let rooms = collect(None, Some(&dir), &["55".to_string()], 10).unwrap();
        assert_eq!(rooms[0]["missing"], serde_json::json!(false));
        assert_eq!(rooms[0]["count"], serde_json::json!(1));
        assert_eq!(rooms[0]["chat_id"], serde_json::json!("55"));
        assert!(rooms[0]["lines"][0].as_str().unwrap().contains("질문 응답"));
    }
}
