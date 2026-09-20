use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use openkakao_cli::local_db::{LocalDbReader, LocalDbReplicaSource};
use tempfile::TempDir;

const CONTEXT_SYNC_REPLICA_COPY_ATTEMPTS: usize = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
struct FileSignature {
    path: PathBuf,
    exists: bool,
    inode: u64,
    size: u64,
    mtime_ns: i128,
}

fn sidecar_path(db_path: &Path, suffix: &str) -> Result<PathBuf> {
    let name = db_path
        .file_name()
        .context("context sync source database has no file name")?
        .to_string_lossy();
    Ok(db_path.with_file_name(format!("{name}{suffix}")))
}

fn source_paths(db_path: &Path) -> Result<[PathBuf; 3]> {
    Ok([
        db_path.to_path_buf(),
        sidecar_path(db_path, "-wal")?,
        sidecar_path(db_path, "-shm")?,
    ])
}

fn file_signature(path: PathBuf, required: bool) -> Result<FileSignature> {
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound && !required => {
            return Ok(FileSignature {
                path,
                exists: false,
                inode: 0,
                size: 0,
                mtime_ns: 0,
            });
        }
        Err(error) => {
            return Err(error).with_context(|| format!("failed to stat {}", path.display()))
        }
    };
    if !metadata.file_type().is_file() {
        anyhow::bail!(
            "context sync SQLite source is not a regular file: {}",
            path.display()
        );
    }

    #[cfg(unix)]
    let (inode, mtime_ns) = {
        use std::os::unix::fs::MetadataExt;
        (
            metadata.ino(),
            i128::from(metadata.mtime()) * 1_000_000_000_i128 + i128::from(metadata.mtime_nsec()),
        )
    };
    #[cfg(not(unix))]
    let (inode, mtime_ns) = {
        let modified = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_nanos() as i128)
            .unwrap_or_default();
        (metadata.len(), modified)
    };

    Ok(FileSignature {
        path,
        exists: true,
        inode,
        size: metadata.len(),
        mtime_ns,
    })
}

fn snapshot_signature(db_path: &Path) -> Result<[FileSignature; 3]> {
    let [db, wal, shm] = source_paths(db_path)?;
    Ok([
        file_signature(db, true)?,
        file_signature(wal, false)?,
        file_signature(shm, false)?,
    ])
}

fn remove_previous_attempt(tmpdir: &Path, source: &[FileSignature; 3]) -> Result<()> {
    for item in source {
        let name = item
            .path
            .file_name()
            .context("context sync SQLite source has no file name")?;
        let destination = tmpdir.join(name);
        match fs::remove_file(&destination) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("failed to reset {}", destination.display()));
            }
        }
    }
    Ok(())
}

fn copy_signature_set(tmpdir: &Path, source: &[FileSignature; 3]) -> Result<PathBuf> {
    remove_previous_attempt(tmpdir, source)?;
    for item in source.iter().filter(|item| item.exists) {
        let name = item
            .path
            .file_name()
            .context("context sync SQLite source has no file name")?;
        let destination = tmpdir.join(name);
        fs::copy(&item.path, &destination).with_context(|| {
            format!(
                "failed to copy context sync SQLite source {} to {}",
                item.path.display(),
                destination.display()
            )
        })?;
        let copied = fs::metadata(&destination)
            .with_context(|| format!("failed to stat replica {}", destination.display()))?;
        if copied.len() != item.size {
            anyhow::bail!("context sync SQLite replica size changed during copy");
        }
    }
    let db_name = source[0]
        .path
        .file_name()
        .context("context sync source database has no file name")?;
    Ok(tmpdir.join(db_name))
}

fn copy_consistent_sqlite_replica_with_hook<F>(
    db_path: &Path,
    tmpdir: &Path,
    mut after_copy: F,
) -> Result<PathBuf>
where
    F: FnMut(usize, &Path) -> Result<()>,
{
    let mut last_error = None;
    for attempt in 1..=CONTEXT_SYNC_REPLICA_COPY_ATTEMPTS {
        let before = snapshot_signature(db_path)?;
        match copy_signature_set(tmpdir, &before) {
            Ok(replica_db) => {
                after_copy(attempt, db_path)?;
                let after = snapshot_signature(db_path)?;
                if before == after {
                    return Ok(replica_db);
                }
                last_error = Some(anyhow::anyhow!(
                    "context sync SQLite source changed during isolated copy"
                ));
            }
            Err(error) => last_error = Some(error),
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow::anyhow!("context sync SQLite replica unavailable")))
        .context("consistent isolated context sync SQLite replica unavailable")
}

fn copy_consistent_sqlite_replica(db_path: &Path, tmpdir: &Path) -> Result<PathBuf> {
    copy_consistent_sqlite_replica_with_hook(db_path, tmpdir, |_attempt, _source| Ok(()))
}

struct IsolatedSqliteReplica {
    _tempdir: TempDir,
    db_path: PathBuf,
}

impl IsolatedSqliteReplica {
    fn create(source_path: &Path) -> Result<Self> {
        let tempdir = tempfile::Builder::new()
            .prefix("openkakao-context-sync-")
            .tempdir()
            .context("failed to create context sync SQLite replica directory")?;
        let db_path = copy_consistent_sqlite_replica(source_path, tempdir.path())?;
        Ok(Self {
            _tempdir: tempdir,
            db_path,
        })
    }
}

pub(crate) struct ContextSyncReplicaSource {
    source: LocalDbReplicaSource,
}

impl ContextSyncReplicaSource {
    pub(crate) fn discover() -> Result<Self> {
        Ok(Self {
            source: LocalDbReplicaSource::discover()?,
        })
    }

    pub(crate) fn account_fingerprint(&self) -> &str {
        self.source.account_fingerprint()
    }

    pub(crate) fn account_user_id(&self) -> i64 {
        self.source.account_user_id()
    }

    pub(crate) fn open_fresh(&self) -> Result<ContextSyncReplicaReader> {
        self.source.ensure_source_identity()?;
        let replica = IsolatedSqliteReplica::create(self.source.path())?;
        self.source.ensure_source_identity()?;
        let reader = self.source.open_replica(&replica.db_path)?;
        Ok(ContextSyncReplicaReader {
            reader: Some(reader),
            replica: Some(replica),
        })
    }
}

pub(crate) struct ContextSyncReplicaReader {
    reader: Option<LocalDbReader>,
    replica: Option<IsolatedSqliteReplica>,
}

impl ContextSyncReplicaReader {
    pub(crate) fn reader(&self) -> &LocalDbReader {
        self.reader
            .as_ref()
            .expect("context sync replica reader must exist while borrowed")
    }
}

impl Drop for ContextSyncReplicaReader {
    fn drop(&mut self) {
        drop(self.reader.take());
        drop(self.replica.take());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::{Connection, OpenFlags};

    fn wal_fixture() -> Result<(TempDir, PathBuf, Connection)> {
        let tempdir = tempfile::tempdir()?;
        let db_path = tempdir.path().join("fixture.sqlite3");
        let connection = Connection::open(&db_path)?;
        connection.execute_batch(
            "PRAGMA journal_mode=WAL;\
             PRAGMA wal_autocheckpoint=0;\
             CREATE TABLE messages(id INTEGER PRIMARY KEY, body TEXT NOT NULL);\
             INSERT INTO messages(body) VALUES ('first');",
        )?;
        Ok((tempdir, db_path, connection))
    }

    #[test]
    fn context_sync_local_replica_path_is_isolated_and_readable() -> Result<()> {
        let (_tempdir, db_path, _source_connection) = wal_fixture()?;
        let replica = IsolatedSqliteReplica::create(&db_path)?;
        assert_ne!(replica.db_path, db_path);
        let connection = Connection::open_with_flags(
            &replica.db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        assert_eq!(
            connection.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            1
        );
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_copies_wal_and_shm_sidecars() -> Result<()> {
        let (_tempdir, db_path, _source_connection) = wal_fixture()?;
        assert!(sidecar_path(&db_path, "-wal")?.exists());
        assert!(sidecar_path(&db_path, "-shm")?.exists());
        let replica = IsolatedSqliteReplica::create(&db_path)?;
        assert!(sidecar_path(&replica.db_path, "-wal")?.exists());
        assert!(sidecar_path(&replica.db_path, "-shm")?.exists());
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_fails_closed_when_source_changes_on_all_three_attempts(
    ) -> Result<()> {
        let (_tempdir, db_path, source_connection) = wal_fixture()?;
        let tempdir = tempfile::tempdir()?;
        let error = copy_consistent_sqlite_replica_with_hook(
            &db_path,
            tempdir.path(),
            |attempt, _source| {
                source_connection.execute(
                    "INSERT INTO messages(body) VALUES (?1)",
                    [format!("mutation-{attempt}")],
                )?;
                Ok(())
            },
        )
        .expect_err("a source that changes on every attempt must fail closed");
        assert!(error
            .to_string()
            .contains("consistent isolated context sync SQLite replica unavailable"));
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_can_copy_while_source_holds_exclusive_transaction() -> Result<()>
    {
        let (_tempdir, db_path, source_connection) = wal_fixture()?;
        source_connection.execute_batch("BEGIN EXCLUSIVE")?;
        let replica = IsolatedSqliteReplica::create(&db_path)?;
        let connection =
            Connection::open_with_flags(&replica.db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        connection.execute_batch("PRAGMA query_only=ON")?;
        assert_eq!(
            connection.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            1
        );
        source_connection.execute_batch("ROLLBACK")?;
        Ok(())
    }
}
