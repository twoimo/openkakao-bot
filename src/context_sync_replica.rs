use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use openkakao_cli::local_db::{LocalDbReader, LocalDbReplicaSource};
use tempfile::TempDir;

const CONTEXT_SYNC_REPLICA_COPY_ATTEMPTS: usize = 3;

#[derive(Debug, thiserror::Error)]
#[error("consistent isolated context sync SQLite replica unavailable")]
pub(crate) struct SnapshotRetryExhausted;

#[derive(Debug, thiserror::Error)]
#[error("context sync SQLite replica size changed during copy")]
struct SnapshotCopyChanged;

#[cfg(target_os = "macos")]
#[link(name = "System")]
extern "C" {
    fn clonefile(
        source: *const std::ffi::c_char,
        destination: *const std::ffi::c_char,
        flags: i32,
    ) -> i32;
}

/// Use an isolated APFS copy-on-write clone for the large database file.
/// The caller compares source signatures before and after the clone and
/// WAL copy. Filesystems without `clonefile` support use a byte copy.
fn clone_main_database(source: &Path, destination: &Path) -> io::Result<()> {
    #[cfg(target_os = "macos")]
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;

        let source_c = CString::new(source.as_os_str().as_bytes()).map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidInput, "database path contains NUL")
        })?;
        let destination_c = CString::new(destination.as_os_str().as_bytes()).map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidInput, "replica path contains NUL")
        })?;
        // CLONE_NOFOLLOW: never follow a source symlink that races the stat.
        let result = unsafe { clonefile(source_c.as_ptr(), destination_c.as_ptr(), 1) };
        if result == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if !matches!(
            error.raw_os_error(),
            Some(libc::EXDEV | libc::ENOTSUP | libc::EINVAL | libc::ENOSYS)
        ) {
            return Err(error);
        }
        // The destination lives in a private temp directory. Remove a failed
        // partial clone before preserving the existing full-copy fallback.
        match fs::remove_file(destination) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }

    copy_regular_file(source, destination)
}

fn copy_regular_file(source: &Path, destination: &Path) -> io::Result<()> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let mut input = options.open(source)?;
    if !input.metadata()?.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "context sync SQLite source is not a regular file",
        ));
    }
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    io::copy(&mut input, &mut output).map(|_| ())
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FileSignature {
    path: PathBuf,
    exists: bool,
    device: u64,
    inode: u64,
    size: u64,
    mtime_ns: i128,
    ctime_ns: i128,
}

fn sidecar_path(db_path: &Path, suffix: &str) -> Result<PathBuf> {
    let name = db_path
        .file_name()
        .context("context sync source database has no file name")?
        .to_string_lossy();
    Ok(db_path.with_file_name(format!("{name}{suffix}")))
}

fn source_paths(db_path: &Path) -> Result<[PathBuf; 2]> {
    Ok([db_path.to_path_buf(), sidecar_path(db_path, "-wal")?])
}

fn file_signature(path: PathBuf, required: bool) -> Result<FileSignature> {
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound && !required => {
            return Ok(FileSignature {
                path,
                exists: false,
                device: 0,
                inode: 0,
                size: 0,
                mtime_ns: 0,
                ctime_ns: 0,
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
    let (device, inode, mtime_ns, ctime_ns) = {
        use std::os::unix::fs::MetadataExt;
        (
            metadata.dev(),
            metadata.ino(),
            i128::from(metadata.mtime()) * 1_000_000_000_i128 + i128::from(metadata.mtime_nsec()),
            i128::from(metadata.ctime()) * 1_000_000_000_i128 + i128::from(metadata.ctime_nsec()),
        )
    };
    #[cfg(not(unix))]
    let (device, inode, mtime_ns, ctime_ns) = {
        let modified = metadata
            .modified()
            .ok()
            .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|value| value.as_nanos() as i128)
            .unwrap_or_default();
        (0, metadata.len(), modified, modified)
    };

    Ok(FileSignature {
        path,
        exists: true,
        device,
        inode,
        size: metadata.len(),
        mtime_ns,
        ctime_ns,
    })
}

fn snapshot_signature(db_path: &Path) -> Result<[FileSignature; 2]> {
    // SHM is a transient wal-index/read-mark cache, not database content.
    // SQLite rebuilds it from the stable DB+WAL in the private writable dir.
    let [db, wal] = source_paths(db_path)?;
    Ok([file_signature(db, true)?, file_signature(wal, false)?])
}

fn remove_previous_attempt(tmpdir: &Path, source: &[FileSignature; 2]) -> Result<()> {
    for path in source_paths(&source[0].path)?
        .into_iter()
        .chain(std::iter::once(sidecar_path(&source[0].path, "-shm")?))
    {
        let name = path
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

fn copy_signature_set(tmpdir: &Path, source: &[FileSignature; 2]) -> Result<PathBuf> {
    remove_previous_attempt(tmpdir, source)?;
    for (index, item) in source.iter().enumerate().filter(|(_, item)| item.exists) {
        let name = item
            .path
            .file_name()
            .context("context sync SQLite source has no file name")?;
        let destination = tmpdir.join(name);
        let copy_result = if index == 0 {
            clone_main_database(&item.path, &destination)
        } else {
            copy_regular_file(&item.path, &destination)
        };
        if let Err(error) = &copy_result {
            // Only disappearance of an optional WAL is a possible copy race.
            // Missing DB, permission, disk and other copy errors stay terminal.
            if index == 1
                && error.kind() == io::ErrorKind::NotFound
                && !file_signature(item.path.clone(), false)?.exists
            {
                return Err(SnapshotCopyChanged.into());
            }
        }
        copy_result.with_context(|| {
            format!(
                "failed to create isolated context sync SQLite replica {} from {}",
                destination.display(),
                item.path.display()
            )
        })?;
        let copied = fs::symlink_metadata(&destination)
            .with_context(|| format!("failed to stat replica {}", destination.display()))?;
        if !copied.file_type().is_file() {
            anyhow::bail!("context sync SQLite replica is not a regular file");
        }
        if copied.len() != item.size {
            return Err(SnapshotCopyChanged.into());
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
    after_copy: F,
) -> Result<PathBuf>
where
    F: FnMut(usize, &Path) -> Result<()>,
{
    copy_consistent_sqlite_replica_with_adapters(db_path, tmpdir, copy_signature_set, after_copy)
}

fn copy_consistent_sqlite_replica_with_adapters<C, F>(
    db_path: &Path,
    tmpdir: &Path,
    mut copy: C,
    mut after_copy: F,
) -> Result<PathBuf>
where
    C: FnMut(&Path, &[FileSignature; 2]) -> Result<PathBuf>,
    F: FnMut(usize, &Path) -> Result<()>,
{
    let mut source_identity = None;
    for attempt in 1..=CONTEXT_SYNC_REPLICA_COPY_ATTEMPTS {
        let before = snapshot_signature(db_path)?;
        let identity = (before[0].device, before[0].inode);
        if *source_identity.get_or_insert(identity) != identity {
            anyhow::bail!("context sync SQLite source identity changed during isolated copy");
        }
        let copied = match copy(tmpdir, &before) {
            Ok(replica_db) => Some(replica_db),
            Err(error) if error.is::<SnapshotCopyChanged>() => None,
            Err(error) => return Err(error),
        };
        if copied.is_some() {
            after_copy(attempt, db_path)?;
        }
        let after = snapshot_signature(db_path)?;
        if (after[0].device, after[0].inode) != identity {
            anyhow::bail!("context sync SQLite source identity changed during isolated copy");
        }
        if before == after {
            if let Some(replica_db) = copied {
                return Ok(replica_db);
            }
            // A copy-time size/disappearance race still consumes this attempt
            // even if the source has settled back to the same signature.
        }
        // Discard this incoherent clone; the next attempt removes its sidecars.
    }
    Err(SnapshotRetryExhausted.into())
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

    /// Persist one already-consistent encrypted DB+WAL copy across bounded
    /// ingestion batches. The lock is held until its read-only reader drops.
    pub(crate) fn open_corpus_snapshot(
        &self,
        root: &Path,
    ) -> Result<(ContextSyncReplicaReader, String, i64)> {
        use openkakao_cli::alden_corpus::{account_directory, private_directory, private_file};
        use std::io::Write;
        use std::os::fd::AsRawFd;
        let account = self.account_fingerprint();
        let directory = account_directory(root, account)?;
        let lock = private_file(&directory.join("ingest.lock"))?;
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            anyhow::bail!("corpus_ingest_busy")
        }
        let snapshot = directory.join("snapshot");
        let manifest = directory.join("snapshot.json");
        let now = chrono::Utc::now().timestamp();
        let mut state: serde_json::Value = if manifest.exists() {
            if manifest.is_symlink() || manifest.metadata()?.len() > 8192 {
                anyhow::bail!("corpus_snapshot_manifest_unsafe")
            }
            serde_json::from_slice(&fs::read(&manifest)?)?
        } else {
            serde_json::Value::Null
        };
        let complete = directory.join("snapshot-complete");
        let aged = state["created_at"]
            .as_i64()
            .is_some_and(|at| now - at >= 300);
        if !manifest.exists() || (complete.is_file() && aged) {
            private_directory(&snapshot)?;
            self.source.ensure_source_identity()?;
            let db = copy_consistent_sqlite_replica(self.source.path(), &snapshot)?;
            // Decryption/query-only readback precedes publication of the cache.
            let reader = self.source.open_replica(&db)?;
            drop(reader);
            self.source.ensure_source_identity()?;
            let id = format!(
                "{}-{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?
                    .as_nanos(),
                std::process::id()
            );
            state = serde_json::json!({"schema_version":1,"account":account,"id":id,"created_at":now,"file":db.file_name().and_then(|n|n.to_str()).ok_or_else(||anyhow::anyhow!("corpus_snapshot_filename_invalid"))?});
            let temporary = directory.join("snapshot.next.json");
            let mut file = private_file(&temporary)?;
            file.set_len(0)?;
            file.write_all(&serde_json::to_vec(&state)?)?;
            file.sync_all()?;
            fs::rename(temporary, &manifest)?;
            if complete.exists() {
                fs::remove_file(&complete)?;
            }
        }
        if state["schema_version"] != 1 || state["account"] != account {
            anyhow::bail!("corpus_snapshot_identity_invalid")
        }
        let name = state["file"]
            .as_str()
            .filter(|n| !n.is_empty() && !n.contains(['/', '\\']) && *n != "." && *n != "..")
            .ok_or_else(|| anyhow::anyhow!("corpus_snapshot_filename_invalid"))?;
        let reader = self.source.open_replica(&snapshot.join(name))?;
        Ok((
            ContextSyncReplicaReader {
                reader: Some(reader),
                replica: None,
                _corpus_lock: Some(lock),
            },
            state["id"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("corpus_snapshot_id_invalid"))?
                .into(),
            state["created_at"]
                .as_i64()
                .ok_or_else(|| anyhow::anyhow!("corpus_snapshot_time_invalid"))?,
        ))
    }

    pub(crate) fn open_fresh(&self) -> Result<ContextSyncReplicaReader> {
        self.source.ensure_source_identity()?;
        let replica = IsolatedSqliteReplica::create(self.source.path())?;
        self.source.ensure_source_identity()?;
        let reader = self.source.open_replica(&replica.db_path)?;
        Ok(ContextSyncReplicaReader {
            reader: Some(reader),
            replica: Some(replica),
            _corpus_lock: None,
        })
    }
}

pub(crate) struct ContextSyncReplicaReader {
    reader: Option<LocalDbReader>,
    replica: Option<IsolatedSqliteReplica>,
    _corpus_lock: Option<std::fs::File>,
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
    fn context_sync_replica_main_file_remains_isolated_after_source_write() -> Result<()> {
        let tempdir = tempfile::tempdir()?;
        let source = tempdir.path().join("source.sqlite3");
        let replica = tempdir.path().join("replica.sqlite3");
        fs::write(&source, b"snapshot-before-write")?;

        clone_main_database(&source, &replica)?;
        fs::write(&source, b"source-after-write")?;

        assert_eq!(fs::read(&replica)?, b"snapshot-before-write");
        assert_eq!(fs::read(&source)?, b"source-after-write");
        Ok(())
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
    fn context_sync_local_replica_rebuilds_private_shm_and_reads_committed_wal_tail() -> Result<()>
    {
        let (tempdir, db_path, source_connection) = wal_fixture()?;
        source_connection.execute_batch(
            "PRAGMA wal_checkpoint(TRUNCATE);\
             INSERT INTO messages(body) VALUES ('wal-tail');",
        )?;

        let source_wal = sidecar_path(&db_path, "-wal")?;
        let source_shm = sidecar_path(&db_path, "-shm")?;
        assert!(source_wal.exists());
        assert!(source_shm.exists());

        let db_only_path = tempdir.path().join("db-only.sqlite3");
        copy_regular_file(&db_path, &db_only_path)?;
        let db_only = Connection::open_with_flags(
            &db_only_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        assert_eq!(
            db_only.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            1,
            "the newest committed row must exist only in the WAL fixture",
        );
        drop(db_only);

        let source_db_bytes = fs::read(&db_path)?;
        let source_wal_bytes = fs::read(&source_wal)?;
        let source_shm_bytes = fs::read(&source_shm)?;
        let replica = IsolatedSqliteReplica::create(&db_path)?;
        let replica_wal = sidecar_path(&replica.db_path, "-wal")?;
        let replica_shm = sidecar_path(&replica.db_path, "-shm")?;
        assert!(replica_wal.exists());
        assert!(
            !replica_shm.exists(),
            "SHM must not be copied from the live source"
        );

        let connection = Connection::open_with_flags(
            &replica.db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        connection.execute_batch("PRAGMA query_only=ON")?;
        assert_eq!(
            connection.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            2,
            "the private readonly replica must apply the committed WAL tail",
        );
        assert_eq!(
            connection.query_row(
                "SELECT COUNT(*) FROM messages WHERE body = 'wal-tail'",
                [],
                |row| row.get::<_, i64>(0),
            )?,
            1,
        );
        assert!(
            replica_shm.exists(),
            "readonly SQLite must rebuild transient SHM inside the private writable directory",
        );
        assert_eq!(fs::read(&db_path)?, source_db_bytes);
        assert_eq!(fs::read(&source_wal)?, source_wal_bytes);
        assert_eq!(fs::read(&source_shm)?, source_shm_bytes);
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_ignores_source_shm_only_churn_on_first_attempt() -> Result<()> {
        let source_dir = tempfile::tempdir()?;
        let db_path = source_dir.path().join("fixture.sqlite3");
        let wal_path = sidecar_path(&db_path, "-wal")?;
        let shm_path = sidecar_path(&db_path, "-shm")?;
        fs::write(&db_path, b"db-stable")?;
        fs::write(&wal_path, b"wal-stable")?;
        fs::write(&shm_path, b"shm-before")?;
        let replica_dir = tempfile::tempdir()?;
        let mut attempts = Vec::new();

        let replica_db = copy_consistent_sqlite_replica_with_hook(
            &db_path,
            replica_dir.path(),
            |attempt, source| {
                attempts.push(attempt);
                fs::write(sidecar_path(source, "-shm")?, b"shm-after")?;
                Ok(())
            },
        )?;

        assert_eq!(attempts, [1]);
        assert_eq!(fs::read(&replica_db)?, b"db-stable");
        assert_eq!(fs::read(sidecar_path(&replica_db, "-wal")?)?, b"wal-stable");
        assert!(!sidecar_path(&replica_db, "-shm")?.exists());
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_retries_db_and_wal_churn_exactly_three_times() -> Result<()> {
        use std::io::Write;

        let source_dir = tempfile::tempdir()?;
        let db_path = source_dir.path().join("fixture.sqlite3");
        let wal_path = sidecar_path(&db_path, "-wal")?;
        fs::write(&db_path, b"db")?;
        fs::write(&wal_path, b"wal")?;
        let replica_dir = tempfile::tempdir()?;
        let mut attempts = Vec::new();
        let error = copy_consistent_sqlite_replica_with_hook(
            &db_path,
            replica_dir.path(),
            |attempt, source| {
                attempts.push(attempt);
                fs::OpenOptions::new()
                    .append(true)
                    .open(source)?
                    .write_all(&[attempt as u8])?;
                fs::OpenOptions::new()
                    .append(true)
                    .open(sidecar_path(source, "-wal")?)?
                    .write_all(&[attempt as u8])?;
                Ok(())
            },
        )
        .expect_err("a source that changes on every attempt must fail closed");
        assert_eq!(attempts, [1, 2, 3]);
        assert!(error.is::<SnapshotRetryExhausted>());
        assert_eq!(
            error.to_string(),
            "consistent isolated context sync SQLite replica unavailable",
        );
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_copy_io_error_is_terminal_without_retry() -> Result<()> {
        let source_dir = tempfile::tempdir()?;
        let db_path = source_dir.path().join("fixture.sqlite3");
        fs::write(&db_path, b"db")?;
        let replica_dir = tempfile::tempdir()?;
        let mut copy_calls = 0;
        let error = copy_consistent_sqlite_replica_with_adapters(
            &db_path,
            replica_dir.path(),
            |_tmpdir, _source| -> Result<PathBuf> {
                copy_calls += 1;
                Err(
                    io::Error::new(io::ErrorKind::PermissionDenied, "fixture permission denied")
                        .into(),
                )
            },
            |_attempt, _source| Ok(()),
        )
        .expect_err("a true copy IO error must remain terminal");
        assert_eq!(copy_calls, 1);
        assert!(!error.is::<SnapshotRetryExhausted>());
        assert_eq!(
            error.downcast_ref::<io::Error>().map(io::Error::kind),
            Some(io::ErrorKind::PermissionDenied),
        );

        let mut disk_copy_calls = 0;
        let error = copy_consistent_sqlite_replica_with_adapters(
            &db_path,
            replica_dir.path(),
            |_tmpdir, _source| -> Result<PathBuf> {
                disk_copy_calls += 1;
                Err(io::Error::from_raw_os_error(libc::ENOSPC).into())
            },
            |_attempt, _source| Ok(()),
        )
        .expect_err("a disk-full copy error must remain terminal");
        assert_eq!(disk_copy_calls, 1);
        assert!(!error.is::<SnapshotRetryExhausted>());
        assert_eq!(
            error
                .downcast_ref::<io::Error>()
                .and_then(io::Error::raw_os_error),
            Some(libc::ENOSPC),
        );
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_copy_changed_race_consumes_all_three_attempts() -> Result<()> {
        let source_dir = tempfile::tempdir()?;
        let db_path = source_dir.path().join("fixture.sqlite3");
        fs::write(&db_path, b"db-stable")?;
        let replica_dir = tempfile::tempdir()?;
        let mut copy_calls = 0;
        let error = copy_consistent_sqlite_replica_with_adapters(
            &db_path,
            replica_dir.path(),
            |_tmpdir, _source| -> Result<PathBuf> {
                copy_calls += 1;
                Err(SnapshotCopyChanged.into())
            },
            |_attempt, _source| panic!("copy-changed attempts must not run the post-copy hook"),
        )
        .expect_err("copy-time races must exhaust the bounded retry budget");
        assert_eq!(copy_calls, CONTEXT_SYNC_REPLICA_COPY_ATTEMPTS);
        assert!(error.is::<SnapshotRetryExhausted>());
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_required_source_identity_change_is_terminal() -> Result<()> {
        let source_dir = tempfile::tempdir()?;
        let db_path = source_dir.path().join("fixture.sqlite3");
        fs::write(&db_path, b"db-stable")?;
        let replica_dir = tempfile::tempdir()?;
        let error = copy_consistent_sqlite_replica_with_hook(
            &db_path,
            replica_dir.path(),
            |_attempt, source| {
                let replacement = source.with_extension("replacement");
                fs::write(&replacement, b"db-stable")?;
                fs::rename(&replacement, source)?;
                Ok(())
            },
        )
        .expect_err("replacing the required DB identity must be terminal");
        assert!(!error.is::<SnapshotRetryExhausted>());
        assert_eq!(
            error.to_string(),
            "context sync SQLite source identity changed during isolated copy",
        );
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_missing_nonregular_and_symlink_sources_are_terminal() -> Result<()>
    {
        let source_dir = tempfile::tempdir()?;
        let replica_dir = tempfile::tempdir()?;
        let missing = source_dir.path().join("missing.sqlite3");
        let error = copy_consistent_sqlite_replica(&missing, replica_dir.path())
            .expect_err("a missing required DB must be terminal");
        assert!(!error.is::<SnapshotRetryExhausted>());

        let directory = source_dir.path().join("directory.sqlite3");
        fs::create_dir(&directory)?;
        let error = copy_consistent_sqlite_replica(&directory, replica_dir.path())
            .expect_err("a nonregular required DB must be terminal");
        assert!(!error.is::<SnapshotRetryExhausted>());
        assert!(error.to_string().contains("not a regular file"));

        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;

            let target = source_dir.path().join("target.sqlite3");
            let symlink_path = source_dir.path().join("symlink.sqlite3");
            fs::write(&target, b"db")?;
            symlink(&target, &symlink_path)?;
            let error = copy_consistent_sqlite_replica(&symlink_path, replica_dir.path())
                .expect_err("a symlink required DB must be terminal");
            assert!(!error.is::<SnapshotRetryExhausted>());
            assert!(error.to_string().contains("not a regular file"));
        }
        Ok(())
    }

    #[test]
    fn context_sync_local_replica_can_copy_while_source_holds_exclusive_transaction() -> Result<()>
    {
        let (_tempdir, db_path, source_connection) = wal_fixture()?;
        source_connection.execute_batch(
            "BEGIN EXCLUSIVE;\
             INSERT INTO messages(body) VALUES ('uncommitted');",
        )?;
        assert_eq!(
            source_connection.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            2,
        );
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
        assert_eq!(
            source_connection.query_row("SELECT COUNT(*) FROM messages", [], |row| row
                .get::<_, i64>(0))?,
            1,
        );
        Ok(())
    }
}
