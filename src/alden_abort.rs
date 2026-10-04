use std::ffi::{CString, OsString};
use std::fs::File;
use std::io::Read;
use std::os::fd::FromRawFd;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::Deserialize;

pub const ALDEN_ABORT_STATE_ROOT_ENV: &str = "OPENKAKAO_ALDEN_ABORT_STATE_ROOT";
pub const ALDEN_ABORT_EPOCH_ENV: &str = "OPENKAKAO_ALDEN_ABORT_EPOCH";

const ABORT_SCHEMA_VERSION: u64 = 1;
const ABORT_STATE_NAME: &str = "alden-abort.json";
const ABORT_MAX_EPOCH: u64 = 9_007_199_254_740_991;
const ABORT_MAX_STATE_BYTES: usize = 4096;
const ABORT_MAX_REASON_CHARS: usize = 96;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AbortState {
    schema_version: u64,
    epoch: u64,
    latched: bool,
    reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AldenAbortFence {
    state_root: PathBuf,
    expected_epoch: u64,
}

impl AldenAbortFence {
    /// Load the paired worker-only relay without changing legacy/manual local-send behavior.
    /// The canonical root resolver is intentionally lazy so a manual invocation with no relay
    /// does not start validating otherwise-unused AutoReply configuration.
    pub fn from_worker_env<F>(worker_identity: bool, canonical_root: F) -> Result<Option<Self>>
    where
        F: FnOnce() -> Result<PathBuf>,
    {
        Self::from_worker_relay(
            worker_identity,
            std::env::var_os(ALDEN_ABORT_STATE_ROOT_ENV),
            std::env::var_os(ALDEN_ABORT_EPOCH_ENV),
            canonical_root,
        )
    }

    fn from_worker_relay<F>(
        worker_identity: bool,
        root_value: Option<OsString>,
        epoch_value: Option<OsString>,
        canonical_root: F,
    ) -> Result<Option<Self>>
    where
        F: FnOnce() -> Result<PathBuf>,
    {
        let (root_value, epoch_value) = match (root_value, epoch_value) {
            (Some(root), Some(epoch)) => (root, epoch),
            (None, None) => return Ok(None),
            _ => anyhow::bail!("alden_abort_relay_partial"),
        };
        if !worker_identity {
            anyhow::bail!("alden_abort_relay_worker_required");
        }

        let root_raw = root_value
            .to_str()
            .context("alden_abort_relay_root_not_utf8")?;
        if root_raw.is_empty() {
            anyhow::bail!("alden_abort_relay_root_empty");
        }
        let relay_root = PathBuf::from(root_raw);
        let canonical_root = canonical_root()?;
        if !canonical_root.is_absolute() || relay_root != canonical_root {
            anyhow::bail!("alden_abort_relay_root_mismatch");
        }

        let epoch_raw = epoch_value
            .to_str()
            .context("alden_abort_relay_epoch_not_utf8")?;
        let expected_epoch = epoch_raw
            .parse::<u64>()
            .ok()
            .filter(|epoch| *epoch <= ABORT_MAX_EPOCH)
            .context("alden_abort_relay_epoch_invalid")?;

        let fence = Self {
            state_root: canonical_root,
            expected_epoch,
        };
        fence.check()?;
        Ok(Some(fence))
    }

    pub fn check(&self) -> Result<()> {
        let state = read_abort_state(&self.state_root)?;
        let Some(state) = state else {
            if self.expected_epoch == 0 {
                return Ok(());
            }
            anyhow::bail!("alden_abort_state_missing_for_nonzero_epoch");
        };
        validate_state(&state)?;
        if state.latched {
            anyhow::bail!("alden_abort_latched");
        }
        if state.epoch != self.expected_epoch {
            anyhow::bail!("alden_abort_epoch_changed");
        }
        Ok(())
    }

    #[cfg(test)]
    fn for_test(state_root: &Path, expected_epoch: u64) -> Self {
        Self {
            state_root: state_root.to_path_buf(),
            expected_epoch,
        }
    }
}

fn validate_state(state: &AbortState) -> Result<()> {
    if state.schema_version != ABORT_SCHEMA_VERSION || state.epoch > ABORT_MAX_EPOCH {
        anyhow::bail!("alden_abort_state_schema_or_epoch");
    }
    if state.reason.chars().count() > ABORT_MAX_REASON_CHARS
        || state.reason.chars().any(char::is_control)
    {
        anyhow::bail!("alden_abort_state_reason");
    }
    if state.latched && state.reason.is_empty() {
        anyhow::bail!("alden_abort_state_reason");
    }
    if !state.latched
        && state.reason != "human_resume"
        && !(state.epoch == 0 && state.reason.is_empty())
    {
        anyhow::bail!("alden_abort_state_resume_reason");
    }
    Ok(())
}

fn open_root(root: &Path) -> Result<File> {
    let path = CString::new(root.as_os_str().as_bytes()).context("alden_abort_root_nul")?;
    let flags =
        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC;
    let fd = unsafe { libc::open(path.as_ptr(), flags) };
    if fd < 0 {
        let error = std::io::Error::last_os_error();
        anyhow::bail!(
            "alden_abort_root_open:{}",
            error.raw_os_error().unwrap_or(-1)
        );
    }
    let file = unsafe { File::from_raw_fd(fd) };
    let metadata = file.metadata().context("alden_abort_root_stat")?;
    use std::os::unix::fs::MetadataExt;
    if !metadata.file_type().is_dir() {
        anyhow::bail!("alden_abort_root_type");
    }
    if metadata.uid() != unsafe { libc::geteuid() } {
        anyhow::bail!("alden_abort_root_owner");
    }
    if metadata.mode() & 0o7777 != 0o700 {
        anyhow::bail!("alden_abort_root_mode");
    }
    Ok(file)
}

fn read_abort_state(root: &Path) -> Result<Option<AbortState>> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::MetadataExt;

    let root = open_root(root)?;
    let name = CString::new(ABORT_STATE_NAME).expect("static abort state name");
    let flags = libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC;
    let fd = unsafe { libc::openat(root.as_raw_fd(), name.as_ptr(), flags) };
    if fd < 0 {
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ENOENT) {
            return Ok(None);
        }
        anyhow::bail!(
            "alden_abort_state_open:{}",
            error.raw_os_error().unwrap_or(-1)
        );
    }
    let mut file = unsafe { File::from_raw_fd(fd) };
    let metadata = file.metadata().context("alden_abort_state_stat")?;
    if !metadata.file_type().is_file() {
        anyhow::bail!("alden_abort_state_type");
    }
    if metadata.uid() != unsafe { libc::geteuid() } {
        anyhow::bail!("alden_abort_state_owner");
    }
    if metadata.mode() & 0o7777 != 0o600 {
        anyhow::bail!("alden_abort_state_mode");
    }
    if metadata.nlink() != 1 {
        anyhow::bail!("alden_abort_state_link_count");
    }
    if metadata.len() > ABORT_MAX_STATE_BYTES as u64 {
        anyhow::bail!("alden_abort_state_too_large");
    }

    let mut raw = Vec::with_capacity(metadata.len() as usize);
    file.by_ref()
        .take((ABORT_MAX_STATE_BYTES + 1) as u64)
        .read_to_end(&mut raw)
        .context("alden_abort_state_read")?;
    if raw.len() > ABORT_MAX_STATE_BYTES {
        anyhow::bail!("alden_abort_state_too_large");
    }
    let state: AbortState = serde_json::from_slice(&raw).context("alden_abort_state_malformed")?;
    Ok(Some(state))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};

    fn private_root() -> tempfile::TempDir {
        let root = tempfile::tempdir().expect("temp root");
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).expect("chmod root");
        root
    }

    fn write_state(root: &Path, raw: &[u8]) {
        let path = root.join(ABORT_STATE_NAME);
        fs::write(&path, raw).expect("write state");
        fs::set_permissions(path, fs::Permissions::from_mode(0o600)).expect("chmod state");
    }

    #[test]
    fn paired_relay_is_worker_only_and_absence_preserves_legacy_behavior() {
        let absent = AldenAbortFence::from_worker_relay(false, None, None, || {
            panic!("legacy/manual local-send must not resolve AutoReply state")
        })
        .expect("absent relay must preserve legacy/manual behavior");
        assert!(absent.is_none());

        let root = private_root();
        let root_value = root.path().as_os_str().to_os_string();
        assert!(
            AldenAbortFence::from_worker_relay(true, Some(root_value.clone()), None, || Ok(root
                .path()
                .to_path_buf()),)
            .unwrap_err()
            .to_string()
            .contains("relay_partial")
        );
        assert!(
            AldenAbortFence::from_worker_relay(true, None, Some(OsString::from("0")), || Ok(root
                .path()
                .to_path_buf()),)
            .unwrap_err()
            .to_string()
            .contains("relay_partial")
        );
        assert!(AldenAbortFence::from_worker_relay(
            false,
            Some(root_value.clone()),
            Some(OsString::from("0")),
            || Ok(root.path().to_path_buf()),
        )
        .unwrap_err()
        .to_string()
        .contains("worker_required"));
        assert!(AldenAbortFence::from_worker_relay(
            true,
            Some(root_value.clone()),
            Some(OsString::from("not-an-epoch")),
            || Ok(root.path().to_path_buf()),
        )
        .unwrap_err()
        .to_string()
        .contains("epoch_invalid"));
        assert!(AldenAbortFence::from_worker_relay(
            true,
            Some(root_value.clone()),
            Some(OsString::from("0")),
            || Ok(root.path().join("other")),
        )
        .unwrap_err()
        .to_string()
        .contains("root_mismatch"));

        let fence = AldenAbortFence::from_worker_relay(
            true,
            Some(root_value),
            Some(OsString::from("0")),
            || Ok(root.path().to_path_buf()),
        )
        .expect("valid paired worker relay")
        .expect("paired relay enables the fence");
        fence.check().expect("fresh epoch zero remains valid");
    }

    #[test]
    fn unchanged_epoch_is_permitted_and_stop_resume_epoch_change_is_not_adopted() {
        let root = private_root();
        write_state(
            root.path(),
            br#"{"schema_version":1,"epoch":4,"latched":false,"reason":"human_resume"}"#,
        );
        let fence = AldenAbortFence::for_test(root.path(), 4);
        fence.check().expect("matching resumed epoch");

        write_state(
            root.path(),
            br#"{"schema_version":1,"epoch":5,"latched":false,"reason":"human_resume"}"#,
        );
        assert!(fence
            .check()
            .unwrap_err()
            .to_string()
            .contains("epoch_changed"));
    }

    #[test]
    fn latched_and_malformed_states_fail_closed() {
        let root = private_root();
        write_state(
            root.path(),
            br#"{"schema_version":1,"epoch":7,"latched":true,"reason":"global_abort"}"#,
        );
        assert!(AldenAbortFence::for_test(root.path(), 7).check().is_err());

        write_state(
            root.path(),
            br#"{"schema_version":1,"epoch":7,"epoch":7,"latched":false,"reason":"human_resume"}"#,
        );
        assert!(AldenAbortFence::for_test(root.path(), 7).check().is_err());
    }

    #[test]
    fn absent_state_is_valid_only_for_epoch_zero_and_existing_private_root() {
        let root = private_root();
        AldenAbortFence::for_test(root.path(), 0)
            .check()
            .expect("fresh epoch zero may omit state file");
        assert!(AldenAbortFence::for_test(root.path(), 1).check().is_err());

        let missing = root.path().join("missing");
        assert!(AldenAbortFence::for_test(&missing, 0).check().is_err());
    }

    #[test]
    fn unsafe_root_or_state_file_fails_closed() {
        let root = private_root();
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o755)).expect("chmod root");
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());

        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).expect("chmod root");
        let target = root.path().join("target");
        fs::write(
            &target,
            br#"{"schema_version":1,"epoch":0,"latched":false,"reason":""}"#,
        )
        .expect("write target");
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).expect("chmod target");
        symlink(&target, root.path().join(ABORT_STATE_NAME)).expect("symlink state");
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());
    }

    #[test]
    fn corrupt_or_non_private_state_file_fails_closed() {
        let root = private_root();
        write_state(
            root.path(),
            br#"{"schema_version":1,"epoch":0,"latched":false,"reason":""}"#,
        );
        let state = root.path().join(ABORT_STATE_NAME);
        fs::set_permissions(&state, fs::Permissions::from_mode(0o644)).expect("chmod state");
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());

        fs::set_permissions(&state, fs::Permissions::from_mode(0o600)).expect("chmod state");
        let sibling = root.path().join("abort-hardlink.json");
        fs::hard_link(&state, &sibling).expect("hard link state");
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());
        fs::remove_file(&sibling).expect("remove hard link");

        write_state(root.path(), b"{malformed");
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());

        write_state(root.path(), &vec![b'x'; ABORT_MAX_STATE_BYTES + 1]);
        assert!(AldenAbortFence::for_test(root.path(), 0).check().is_err());
    }
}
