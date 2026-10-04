//! Separate, resumable local corpus. Never opens a live Kakao database.
use anyhow::{bail, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

#[derive(Serialize)]
pub struct Report {
    pub account: String,
    pub indexed_messages: i64,
    pub indexed_rooms: i64,
    pub pending_messages: i64,
    pub changed: usize,
    pub complete: bool,
    pub snapshot_at: i64,
}

pub fn private_directory(path: &Path) -> Result<()> {
    if !path.is_absolute() {
        bail!("corpus_path_must_be_absolute");
    }
    for parent in path.ancestors() {
        if parent.is_symlink() {
            bail!("corpus_symlink_path");
        }
    }
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
        .or_else(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                Ok(())
            } else {
                Err(e)
            }
        })?;
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() || meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 {
        bail!("corpus_directory_not_private");
    }
    Ok(())
}

pub fn account_directory(root: &Path, account: &str) -> Result<PathBuf> {
    if account.len() != 64 || !account.bytes().all(|b| b.is_ascii_hexdigit()) {
        bail!("corpus_account_invalid");
    }
    private_directory(root)?;
    let path = root.join(account);
    private_directory(&path)?;
    Ok(path)
}

pub fn private_file(path: &Path) -> Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file()
        || meta.uid() != unsafe { libc::geteuid() }
        || meta.mode() & 0o077 != 0
        || meta.nlink() != 1
    {
        bail!("corpus_file_not_private");
    }
    Ok(file)
}

fn meta(conn: &Connection, key: &str) -> Result<String> {
    Ok(conn
        .query_row("SELECT value FROM corpus_meta WHERE key=?", [key], |r| {
            r.get(0)
        })
        .optional()?
        .unwrap_or_default())
}
fn set_meta(conn: &Connection, key: &str, value: impl ToString) -> Result<()> {
    conn.execute(
        "INSERT INTO corpus_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        params![key, value.to_string()],
    )?;
    Ok(())
}
fn aborted(path: Option<&Path>) -> Result<bool> {
    let Some(path) = path else { return Ok(false) };
    if !path.exists() {
        return Ok(false);
    };
    if path.is_symlink() || path.metadata()?.len() > 4096 {
        bail!("corpus_abort_state_invalid")
    }
    let state: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;
    state["latched"]
        .as_bool()
        .ok_or_else(|| anyhow::anyhow!("corpus_abort_state_invalid"))
}

/// A batch reads only one immutable source snapshot, and commits rows with its
/// cursor. Replaying a crash cannot duplicate documents or merge authors.
#[derive(Clone, Copy)]
pub(crate) struct Options<'a> {
    pub root: &'a Path,
    pub account: &'a str,
    pub self_id: i64,
    pub snapshot_id: &'a str,
    pub snapshot_at: i64,
    pub max_rows: usize,
    pub abort: Option<&'a Path>,
}
pub(crate) fn synchronize(source: &Connection, options: Options<'_>) -> Result<Report> {
    let Options {
        root,
        account,
        self_id,
        snapshot_id,
        snapshot_at,
        max_rows,
        abort,
    } = options;
    if !(1..=500_000).contains(&max_rows) {
        bail!("corpus_batch_limit_invalid")
    }
    if aborted(abort)? {
        bail!("corpus_paused")
    }
    let directory = account_directory(root, account)?;
    let path = directory.join("working.sqlite3");
    let _file = private_file(&path)?;
    for suffix in ["-wal", "-shm"] {
        if PathBuf::from(format!("{}{suffix}", path.display())).is_symlink() {
            bail!("corpus_sidecar_unsafe")
        }
    }
    let mut out = Connection::open(&path)?;
    out.busy_timeout(std::time::Duration::from_millis(200))?;
    out.execute_batch("PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS corpus_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS corpus_seen(chat_id INTEGER,log_id INTEGER,PRIMARY KEY(chat_id,log_id)) WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS alden_rooms(chat TEXT PRIMARY KEY,chat_id TEXT NOT NULL,label TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS alden_authors(author_id TEXT PRIMARY KEY,label TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS alden_messages(id INTEGER PRIMARY KEY,chat TEXT NOT NULL,chat_id TEXT NOT NULL,log_id TEXT NOT NULL,author_id TEXT NOT NULL,user_name TEXT NOT NULL,message TEXT NOT NULL,attachment TEXT NOT NULL,message_type INTEGER NOT NULL,sent_at INTEGER NOT NULL,date TEXT NOT NULL,is_self INTEGER NOT NULL,source TEXT NOT NULL,digest BLOB NOT NULL,seen TEXT NOT NULL,UNIQUE(chat_id,log_id));
      CREATE TABLE IF NOT EXISTS context_message_topics(message_id INTEGER,topic TEXT,PRIMARY KEY(message_id,topic));
      CREATE INDEX IF NOT EXISTS corpus_topic_page ON context_message_topics(topic,message_id);
      CREATE VIEW IF NOT EXISTS context_topic_stats AS SELECT m.chat,m.source,t.topic,COUNT(*) AS message_count,MAX(m.date) AS last_date FROM context_message_topics t JOIN alden_messages m ON m.id=t.message_id GROUP BY m.chat,m.source,t.topic;
      CREATE VIEW IF NOT EXISTS context_messages AS SELECT id,source,chat,date,user_name,message,X'' AS vector,author_id,chat_id,log_id,is_self,message_type,sent_at FROM alden_messages;
      CREATE INDEX IF NOT EXISTS corpus_by_room ON alden_messages(chat,id);
      CREATE INDEX IF NOT EXISTS corpus_by_actor ON alden_messages(author_id,chat,id);
      CREATE VIRTUAL TABLE IF NOT EXISTS context_messages_fts USING fts5(message,user_name,chat,content='alden_messages',content_rowid='id');
      CREATE TRIGGER IF NOT EXISTS corpus_ai AFTER INSERT ON alden_messages BEGIN INSERT INTO context_messages_fts(rowid,message,user_name,chat)VALUES(new.id,new.message,new.user_name,new.chat);END;
      CREATE TRIGGER IF NOT EXISTS corpus_ad AFTER DELETE ON alden_messages BEGIN INSERT INTO context_messages_fts(context_messages_fts,rowid,message,user_name,chat)VALUES('delete',old.id,old.message,old.user_name,old.chat);END;
      CREATE TRIGGER IF NOT EXISTS corpus_au AFTER UPDATE OF digest ON alden_messages BEGIN INSERT INTO context_messages_fts(context_messages_fts,rowid,message,user_name,chat)VALUES('delete',old.id,old.message,old.user_name,old.chat);INSERT INTO context_messages_fts(rowid,message,user_name,chat)VALUES(new.id,new.message,new.user_name,new.chat);END;")?;
    let columns = out
        .prepare("PRAGMA table_info(alden_messages)")?
        .query_map([], |r| r.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    if !columns.iter().any(|n| n == "sent_at_source") {
        out.execute_batch("ALTER TABLE alden_messages ADD COLUMN sent_at_source TEXT NOT NULL DEFAULT '';UPDATE alden_messages SET sent_at_source='integer:'||sent_at;")?;
    }
    if meta(&out, "seen_version")? != "2" {
        let tx = out.transaction()?;
        tx.execute("INSERT OR IGNORE INTO corpus_seen SELECT CAST(chat_id AS INTEGER),CAST(log_id AS INTEGER) FROM alden_messages WHERE seen=?",[meta(&tx,"snapshot")?])?;
        set_meta(&tx, "seen_version", 2)?;
        tx.commit()?;
    }
    let previous = meta(&out, "snapshot")?;
    if previous != snapshot_id {
        let tx = out.transaction()?;
        tx.execute("DELETE FROM corpus_seen", [])?;
        set_meta(&tx, "snapshot", snapshot_id)?;
        set_meta(&tx, "cursor", 0)?;
        set_meta(&tx, "processed", 0)?;
        set_meta(&tx, "complete", 0)?;
        tx.commit()?;
    }
    let cursor = meta(&out, "cursor")?.parse::<i64>().unwrap_or(0);
    let total: i64 = source.query_row("SELECT count(*) FROM NTChatMessage", [], |r| r.get(0))?;
    let names=source.prepare("SELECT userId,COALESCE(NULLIF(displayName,''),NULLIF(friendNickName,''),nickName,'') FROM NTUser WHERE linkId=0")?
        .query_map([],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?)))?.collect::<Result<HashMap<_,_>,_>>()?;
    let room_columns = source
        .prepare("PRAGMA table_info(NTChatRoom)")?
        .query_map([], |r| r.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    let room_sql = if room_columns.iter().any(|n| n == "directChatMemberUserId") {
        "SELECT r.chatId,COALESCE(NULLIF(r.chatName,''),(SELECT COALESCE(NULLIF(u.displayName,''),NULLIF(u.friendNickName,''),u.nickName,'') FROM NTUser u WHERE u.userId=r.directChatMemberUserId AND u.linkId=0 LIMIT 1),'') FROM NTChatRoom r"
    } else {
        "SELECT chatId,COALESCE(chatName,'') FROM NTChatRoom"
    };
    let rooms = source
        .prepare(room_sql)?
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?
        .collect::<Result<HashMap<_, _>, _>>()?;
    let tx = out.transaction()?;
    for (id, label) in &rooms {
        let key = format!("kakao:{account}:room:{id}");
        tx.execute("INSERT INTO alden_rooms VALUES(?,?,?) ON CONFLICT(chat)DO UPDATE SET label=excluded.label",params![key,id.to_string(),label])?;
    }
    for (id, label) in &names {
        tx.execute("INSERT INTO alden_authors VALUES(?,?) ON CONFLICT(author_id)DO UPDATE SET label=excluded.label",params![id.to_string(),label])?;
    }
    let mut changed = 0;
    let mut processed = 0;
    let mut last = cursor;
    {
        // rowid, not logId, provides a unique total order even if imported
        // source formats reuse a log number in another room.
        let mut read=source.prepare("SELECT rowid,logId,chatId,authorId,COALESCE(message,''),COALESCE(attachment,''),type,sentAt FROM NTChatMessage WHERE rowid>? ORDER BY rowid LIMIT ?")?;
        let mut rows = read.query(params![cursor, max_rows as i64])?;
        let mut insert=tx.prepare("INSERT INTO alden_messages(chat,chat_id,log_id,author_id,user_name,message,attachment,message_type,sent_at,date,is_self,source,digest,seen,sent_at_source)VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chat_id,log_id)DO UPDATE SET author_id=excluded.author_id,user_name=excluded.user_name,message=excluded.message,attachment=excluded.attachment,message_type=excluded.message_type,sent_at=excluded.sent_at,date=excluded.date,is_self=excluded.is_self,digest=excluded.digest,seen=excluded.seen,sent_at_source=excluded.sent_at_source WHERE digest!=excluded.digest")?;
        let mut seen = tx.prepare("INSERT OR IGNORE INTO corpus_seen VALUES(?,?)")?;
        while let Some(row) = rows.next()? {
            if processed % 512 == 0 && aborted(abort)? {
                bail!("corpus_paused")
            }
            last = row.get(0)?;
            let log: i64 = row.get(1)?;
            let chat: i64 = row.get(2)?;
            let author: i64 = row.get(3)?;
            let text: String = row.get(4)?;
            let attachment: String = row.get(5)?;
            let kind: i32 = row.get(6)?;
            let (sent, sent_at_source) = match row.get_ref(7)? {
                rusqlite::types::ValueRef::Integer(n) => (n, format!("integer:{n}")),
                rusqlite::types::ValueRef::Real(n) => (
                    if n.is_finite() { n.floor() as i64 } else { 0 },
                    format!("real_bits:{:016x}", n.to_bits()),
                ),
                rusqlite::types::ValueRef::Null => (0, "null".into()),
                _ => bail!("corpus_timestamp_type_invalid"),
            };
            let name = names.get(&author).cloned().unwrap_or_default();
            let room = format!("kakao:{account}:room:{chat}");
            let event = room.clone();
            let date = chrono::DateTime::from_timestamp(sent, 0)
                .filter(|_| sent > 0)
                .map(|d| d.format("%Y-%m-%dT%H:%M:%SZ").to_string())
                .unwrap_or_default();
            let bytes =
                serde_json::to_vec(&(author, &name, &text, &attachment, kind, &sent_at_source))?;
            let digest = Sha256::digest(bytes);
            let writes = insert.execute(params![
                room,
                chat.to_string(),
                log.to_string(),
                author.to_string(),
                &name,
                &text,
                &attachment,
                kind,
                sent,
                date,
                crate::local_db::is_self_author(author, self_id),
                event,
                digest.as_slice(),
                snapshot_id,
                sent_at_source
            ])?;
            changed += writes;
            if writes > 0 {
                let id: i64 = tx.query_row(
                    "SELECT id FROM alden_messages WHERE chat_id=? AND log_id=?",
                    params![chat.to_string(), log.to_string()],
                    |r| r.get(0),
                )?;
                tx.execute(
                    "DELETE FROM context_message_topics WHERE message_id=?",
                    [id],
                )?;
                for topic in crate::context::classify_message_topics(&text) {
                    tx.execute(
                        "INSERT INTO context_message_topics VALUES(?,?)",
                        params![id, topic],
                    )?;
                }
            }
            seen.execute(params![chat, log])?;
            processed += 1;
        }
    }
    let finished = source.query_row(
        "SELECT NOT EXISTS(SELECT 1 FROM NTChatMessage WHERE rowid>?)",
        [last],
        |r| r.get::<_, bool>(0),
    )?;
    if finished {
        tx.execute("DELETE FROM context_message_topics WHERE message_id IN(SELECT id FROM alden_messages WHERE NOT EXISTS(SELECT 1 FROM corpus_seen s WHERE s.chat_id=CAST(alden_messages.chat_id AS INTEGER) AND s.log_id=CAST(alden_messages.log_id AS INTEGER)))",[]) ?;
        tx.execute("DELETE FROM alden_messages WHERE NOT EXISTS(SELECT 1 FROM corpus_seen s WHERE s.chat_id=CAST(alden_messages.chat_id AS INTEGER) AND s.log_id=CAST(alden_messages.log_id AS INTEGER))", [])?;
        set_meta(&tx, "complete", 1)?;
    }
    let cumulative = meta(&tx, "processed")?.parse::<i64>().unwrap_or(0) + processed;
    set_meta(&tx, "cursor", last)?;
    set_meta(&tx, "processed", cumulative)?;
    set_meta(&tx, "account", account)?;
    set_meta(&tx, "snapshot_at", snapshot_at)?;
    if aborted(abort)? {
        bail!("corpus_paused")
    }
    tx.commit()?;
    let indexed: i64 = out.query_row("SELECT count(*) FROM alden_messages", [], |r| r.get(0))?;
    let indexed_rooms: i64 =
        out.query_row("SELECT count(DISTINCT chat) FROM alden_messages", [], |r| {
            r.get(0)
        })?;
    if finished && indexed != total {
        bail!("corpus_snapshot_count_mismatch")
    }
    if finished {
        // Publish a complete plaintext corpus with Online Backup, leaving the
        // preceding published DB available throughout a resumed refresh.
        let staged = directory.join("published.next.sqlite3");
        let _staged_file = private_file(&staged)?;
        let mut published = Connection::open(&staged)?;
        {
            let backup = rusqlite::backup::Backup::new(&out, &mut published)?;
            backup.run_to_completion(256, std::time::Duration::from_millis(1), None)?;
        }
        published.close().map_err(|(_, e)| e)?;
        File::open(&staged)?.sync_all()?;
        let target = directory.join("context.sqlite3");
        if target.is_symlink() {
            bail!("corpus_published_unsafe")
        }
        fs::rename(staged, &target)?;
        let pointer = root.join("current.json");
        let temporary = root.join("current.next.json");
        let mut file = private_file(&temporary)?;
        use std::io::Write;
        file.set_len(0)?;
        file.write_all(serde_json::to_vec(&serde_json::json!({"schema_version":1,"account":account,"snapshot":snapshot_id,"snapshot_at":snapshot_at,"messages":indexed,"rooms":indexed_rooms}))?.as_slice())?;
        file.sync_all()?;
        if pointer.is_symlink() {
            bail!("corpus_pointer_unsafe")
        };
        fs::rename(temporary, pointer)?;
    }
    Ok(Report {
        account: account.into(),
        indexed_messages: indexed,
        indexed_rooms,
        pending_messages: (total - cumulative).max(0),
        changed,
        complete: finished,
        snapshot_at,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    macro_rules! sync {
        ($source:expr,$root:expr,$account:expr,$self_id:expr,$snapshot:expr,$at:expr,$limit:expr,$abort:expr) => {
            synchronize(
                $source,
                Options {
                    root: $root,
                    account: $account,
                    self_id: $self_id,
                    snapshot_id: $snapshot,
                    snapshot_at: $at,
                    max_rows: $limit,
                    abort: $abort,
                },
            )
        };
    }
    fn source() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch("CREATE TABLE NTChatMessage(logId INTEGER,chatId INTEGER,authorId INTEGER,message TEXT,attachment TEXT,type INTEGER,sentAt INTEGER);CREATE TABLE NTUser(userId INTEGER,displayName TEXT,friendNickName TEXT,nickName TEXT,linkId INTEGER);CREATE TABLE NTChatRoom(chatId INTEGER,chatName TEXT);INSERT INTO NTUser VALUES(7,'同名','','',0),(8,'同名','','',0);INSERT INTO NTChatRoom VALUES(42,'同名房'),(84,'同名房');INSERT INTO NTChatMessage VALUES(9007199254740997,42,7,'원문 <script>\n全','',1,1234),(9007199254740997,84,8,'다른 방','',1,1234),(100,42,7,'끝','',1,1234);PRAGMA query_only=ON;").unwrap();
        c
    }
    #[test]
    fn resume_and_replay_preserve_full_text_room_and_actor_identity() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let root = tmp.path().canonicalize()?.join("corpus");
        let account = "a".repeat(64);
        let src = source();
        assert!(!sync!(&src, &root, &account, 7, "snapshot-a", 1, 2, None)?.complete);
        assert!(!root.join("current.json").exists());
        let done = sync!(&src, &root, &account, 7, "snapshot-a", 1, 2, None)?;
        assert!(done.complete);
        assert_eq!(done.indexed_messages, 3);
        assert_eq!(
            sync!(&src, &root, &account, 7, "snapshot-a", 1, 2, None)?.changed,
            0
        );
        let db = Connection::open(root.join(&account).join("context.sqlite3"))?;
        assert_eq!(db.query_row("SELECT message FROM context_messages WHERE chat_id='42' AND log_id='9007199254740997'",[],|r|r.get::<_,String>(0))?,"원문 <script>\n全");
        assert_eq!(
            db.query_row(
                "SELECT count(DISTINCT author_id) FROM context_messages",
                [],
                |r| r.get::<_, i64>(0)
            )?,
            2
        );
        assert_eq!(
            db.query_row(
                "SELECT count(*) FROM context_messages_fts WHERE context_messages_fts MATCH '원문'",
                [],
                |r| r.get::<_, i64>(0)
            )?,
            1
        );
        Ok(())
    }
    #[test]
    fn next_snapshot_updates_edits_and_retracts_deleted_rows() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let root = tmp.path().canonicalize()?.join("corpus");
        let account = "b".repeat(64);
        let src = source();
        sync!(&src, &root, &account, 7, "first", 1, 10, None)?;
        src.execute_batch("PRAGMA query_only=OFF;UPDATE NTChatMessage SET message='수정된 내용' WHERE chatId=42 AND logId=9007199254740997;DELETE FROM NTChatMessage WHERE chatId=84;PRAGMA query_only=ON;")?;
        let r = sync!(&src, &root, &account, 7, "second", 2, 10, None)?;
        assert!(r.complete);
        assert_eq!(r.indexed_messages, 2);
        let db = Connection::open(root.join(account).join("context.sqlite3"))?;
        assert_eq!(db.query_row("SELECT count(*) FROM context_messages_fts WHERE context_messages_fts MATCH '수정된'",[],|r|r.get::<_,i64>(0))?,1);
        assert_eq!(
            db.query_row(
                "SELECT count(*) FROM context_messages_fts WHERE context_messages_fts MATCH '다른'",
                [],
                |r| r.get::<_, i64>(0)
            )?,
            0
        );
        Ok(())
    }
    #[test]
    fn real_timestamp_is_preserved_without_losing_its_fraction() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let root = tmp.path().canonicalize()?.join("corpus");
        let src = source();
        src.execute_batch("PRAGMA query_only=OFF;UPDATE NTChatMessage SET sentAt=1234.125 WHERE rowid=1;PRAGMA query_only=ON;")?;
        sync!(&src, &root, &"c".repeat(64), 7, "fraction", 1, 10, None)?;
        let db = Connection::open(root.join("c".repeat(64)).join("context.sqlite3"))?;
        let value: String = db.query_row(
            "SELECT sent_at_source FROM alden_messages WHERE id=1",
            [],
            |r| r.get(0),
        )?;
        assert_eq!(value, format!("real_bits:{:016x}", 1234.125_f64.to_bits()));
        Ok(())
    }
    #[test]
    fn readers_keep_previous_publication_until_refresh_finishes() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let root = tmp.path().canonicalize()?.join("corpus");
        let account = "d".repeat(64);
        let src = source();
        sync!(&src, &root, &account, 7, "first", 1, 10, None)?;
        src.execute_batch("PRAGMA query_only=OFF;UPDATE NTChatMessage SET message='새 내용' WHERE rowid=1;PRAGMA query_only=ON;")?;
        assert!(!sync!(&src, &root, &account, 7, "second", 2, 1, None)?.complete);
        let pointer: serde_json::Value =
            serde_json::from_slice(&fs::read(root.join("current.json"))?)?;
        assert_eq!(pointer["snapshot"], "first");
        let old = Connection::open(root.join(&account).join("context.sqlite3"))?;
        assert_eq!(
            old.query_row("SELECT message FROM context_messages WHERE id=1", [], |r| r
                .get::<_, String>(0))?,
            "원문 <script>\n全"
        );
        drop(old);
        assert!(sync!(&src, &root, &account, 7, "second", 2, 10, None)?.complete);
        let new = Connection::open(root.join(&account).join("context.sqlite3"))?;
        assert_eq!(
            new.query_row("SELECT message FROM context_messages WHERE id=1", [], |r| r
                .get::<_, String>(0))?,
            "새 내용"
        );
        Ok(())
    }
    #[test]
    fn unsafe_paths_and_abort_do_not_publish() -> Result<()> {
        let tmp = tempfile::tempdir()?;
        let root = tmp.path().canonicalize()?.join("corpus");
        let abort = tmp.path().join("abort.json");
        fs::write(&abort, b"{\"latched\":true}")?;
        assert!(sync!(
            &source(),
            &root,
            &"a".repeat(64),
            7,
            "one",
            1,
            10,
            Some(&abort)
        )
        .is_err());
        assert!(!root.exists());
        fs::create_dir(&root)?;
        std::os::unix::fs::symlink(tmp.path(), root.join("link"))?;
        assert!(private_directory(&root.join("link")).is_err());
        Ok(())
    }
}
