//! Bounded, opt-in inspection of the production encrypted snapshot path.
//! Input contains at most three exact room IDs and existing positive cursors.
//! Output contains aggregate metadata only; it never prints messages or keys.
//! Run with a private TMPDIR and an external process timeout (see the report).
#![allow(dead_code)]

// Include the production modules unchanged so this measurement exercises the
// actual cloner and SQLCipher opener. Keeping the inspection in their lexical
// scope avoids adding an unchecked SQL or key-export API to the product.
extern crate openkakao_cli as production;
extern crate self as openkakao_cli;
pub use production::context;
#[path = "../src/alden_corpus.rs"]
mod alden_corpus;

mod local_db {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/local_db.rs"));

    pub(super) fn inspect_replica(reader: &LocalDbReader) -> Result<serde_json::Value> {
        let readonly = reader.conn.is_readonly(rusqlite::DatabaseName::Main)?;
        let query_only: i64 = reader
            .conn
            .query_row("PRAGMA query_only", [], |row| row.get(0))?;
        if !readonly || query_only != 1 {
            anyhow::bail!("replica is not read-only");
        }
        let cipher_version: String = reader
            .conn
            .query_row("PRAGMA cipher_version", [], |row| row.get(0))?;
        let started = std::time::Instant::now();
        let checks = reader
            .conn
            .prepare("PRAGMA quick_check")?
            .query_map([], |row| row.get::<_, String>(0))?
            .take(101)
            .collect::<rusqlite::Result<Vec<_>>>()?;
        if checks.as_slice() != ["ok"] {
            anyhow::bail!("encrypted replica integrity check failed");
        }
        Ok(serde_json::json!({
            "readonly": readonly,
            "query_only": query_only,
            "cipher_version": cipher_version,
            "quick_check": "ok",
            "quick_check_seconds": started.elapsed().as_secs_f64(),
        }))
    }
}

mod context_sync_replica {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/src/context_sync_replica.rs"
    ));

    pub(super) fn inspect(rooms: &[super::Room]) -> Result<serde_json::Value> {
        use sha2::Digest;
        use std::io::Read;
        use std::time::Instant;

        let total_started = Instant::now();
        let source = LocalDbReplicaSource::discover()?;
        let before = snapshot_signature(source.path())?;
        let source_db_bytes = before[0].size;
        let source_wal_bytes = before[1].size;
        let copy_started = Instant::now();
        let replica = IsolatedSqliteReplica::create(source.path())?;
        let copy_seconds = copy_started.elapsed().as_secs_f64();
        source.ensure_source_identity()?;
        let replica_path = replica.db_path.clone();
        let private_root = replica_path
            .parent()
            .context("replica parent missing")?
            .to_path_buf();
        let mut header = [0_u8; 16];
        fs::File::open(&replica_path)?.read_exact(&mut header)?;
        let encrypted_header = header != *b"SQLite format 3\0";
        if !encrypted_header {
            anyhow::bail!("expected the encrypted Kakao database");
        }
        let open_started = Instant::now();
        let reader = source.open_replica(&replica_path)?;
        let open_seconds = open_started.elapsed().as_secs_f64();
        let inspection = crate::local_db::inspect_replica(&reader)?;
        let mut polls = Vec::new();
        for room in rooms {
            let started = Instant::now();
            let envelope = reader.poll_after(room.chat_id, 128, Some(room.checkpoint_log_id))?;
            // Neither the envelope nor its message, author or attachment fields
            // escape this scope. IDs in the public result are one-way hashes.
            polls.push(serde_json::json!({
                "room_fingerprint": hex::encode(sha2::Sha256::digest(room.chat_id.to_string().as_bytes()))[..16],
                "messages_after_existing_checkpoint": envelope.messages.len(),
                "completeness": envelope.completeness.status,
                "has_more": envelope.completeness.has_more,
                "poll_seconds": started.elapsed().as_secs_f64(),
            }));
        }
        source.ensure_source_identity()?;
        let after = snapshot_signature(source.path())?;
        drop(reader);
        drop(replica);
        Ok(serde_json::json!({
            "schema_version": 1,
            "kind": "production-source-encrypted-replica-inspection",
            "source_sqlite_connection_opened": false,
            "source_db_bytes": source_db_bytes,
            "source_wal_bytes": source_wal_bytes,
            "source_db_wal_signature_unchanged": before == after,
            "source_identity_preserved": true,
            "encrypted_header": encrypted_header,
            "copy_seconds": copy_seconds,
            "open_seconds": open_seconds,
            "inspection": inspection,
            "polls": polls,
            "temporary_replica_removed": !private_root.exists(),
            "total_seconds": total_started.elapsed().as_secs_f64(),
            "messages_saved": false,
            "keys_exported": false,
            "model_calls": 0,
            "send_calls": 0,
            "production_index_writes": 0,
        }))
    }
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Room {
    chat_id: i64,
    checkpoint_log_id: i64,
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    rooms: Vec<Room>,
}

fn run() -> anyhow::Result<()> {
    use anyhow::Context;
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 1 {
        anyhow::bail!("one private input file is required");
    }
    let path = std::path::PathBuf::from(&args[0]);
    let metadata = std::fs::symlink_metadata(&path)?;
    if !metadata.is_file() || metadata.len() > 4096 {
        anyhow::bail!("invalid private input");
    }
    let input: Input = serde_json::from_slice(&std::fs::read(path)?)?;
    if input.rooms.is_empty() || input.rooms.len() > 3 {
        anyhow::bail!("one to three scoped rooms are required");
    }
    let mut seen = std::collections::BTreeSet::new();
    if input
        .rooms
        .iter()
        .any(|room| room.chat_id <= 0 || room.checkpoint_log_id <= 0 || !seen.insert(room.chat_id))
    {
        anyhow::bail!("unique positive room IDs and existing cursors are required");
    }
    let report = context_sync_replica::inspect(&input.rooms)?;
    println!(
        "{}",
        serde_json::to_string_pretty(&report).context("serialize inspection")?
    );
    Ok(())
}

fn main() -> std::process::ExitCode {
    match run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(_) => {
            // Generic by design: internal errors may contain private paths.
            eprintln!("Error: alden_encrypted_snapshot_probe_failed");
            std::process::ExitCode::FAILURE
        }
    }
}
