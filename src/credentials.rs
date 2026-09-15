use std::fs;
use std::io::Write;
use std::path::PathBuf;

use anyhow::{Context, Result};

use crate::model::KakaoCredentials;

pub fn credentials_path() -> Result<PathBuf> {
    let home = dirs::home_dir().context("Could not resolve home directory")?;
    Ok(home
        .join(".config")
        .join("openkakao")
        .join("credentials.json"))
}

pub fn load_credentials() -> Result<Option<KakaoCredentials>> {
    let path = credentials_path()?;
    if !path.exists() {
        return Ok(None);
    }

    let data =
        fs::read_to_string(&path).with_context(|| format!("Failed to read {}", path.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = std::fs::metadata(&path)?;
        let mode = metadata.mode() & 0o777;
        if mode != 0o600 {
            eprintln!(
                "WARNING: {} has permissions {:o}, expected 600. Run: chmod 600 {}",
                path.display(),
                mode,
                path.display()
            );
        }
    }

    let creds: KakaoCredentials = serde_json::from_str(&data)
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    Ok(Some(creds))
}

/// Save credentials to the default path. Returns the path written to.
pub fn save_credentials(creds: &KakaoCredentials) -> Result<PathBuf> {
    let path = credentials_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create {}", parent.display()))?;
    }

    let data = serde_json::to_string_pretty(creds).context("Failed to serialize credentials")?;

    // Write to a sibling and rename over the target. Truncating the real file
    // first meant an interrupted save left empty or half-written JSON, and the
    // next run could not log in at all (2026-09-16). The temporary file is
    // created 0o600 so the secret is never briefly world-readable, and the
    // rename is atomic within one directory.
    let temporary = path.with_extension("json.tmp");
    #[cfg(unix)]
    let mut file = {
        use std::os::unix::fs::OpenOptionsExt;
        fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&temporary)
            .with_context(|| format!("Failed to create {}", temporary.display()))?
    };
    #[cfg(not(unix))]
    let mut file = fs::File::create(&temporary)
        .with_context(|| format!("Failed to create {}", temporary.display()))?;

    file.write_all(data.as_bytes())
        .with_context(|| format!("Failed to write {}", temporary.display()))?;
    file.sync_all()
        .with_context(|| format!("Failed to flush {}", temporary.display()))?;
    drop(file);

    fs::rename(&temporary, &path).with_context(|| {
        format!(
            "Failed to move {} onto {}",
            temporary.display(),
            path.display()
        )
    })?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .with_context(|| format!("Failed to set permissions on {}", path.display()))?;
    }

    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_save_load_roundtrip_tempfile() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("creds.json");

        let creds = KakaoCredentials::new(
            "test-token-abc".to_string(),
            12345,
            "dev-uuid".to_string(),
            "3.7.0".to_string(),
            "KT/3.7.0 Mc/26.1.0 ko".to_string(),
            "mac/3.7.0/ko".to_string(),
        );

        let data = serde_json::to_string_pretty(&creds).unwrap();
        let mut file = fs::File::create(&path).unwrap();
        file.write_all(data.as_bytes()).unwrap();

        let loaded: KakaoCredentials =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(loaded.oauth_token, "test-token-abc");
        assert_eq!(loaded.user_id, 12345);
        assert_eq!(loaded.device_uuid, "dev-uuid");
        assert_eq!(loaded.app_version, "3.7.0");
        assert_eq!(loaded.device_name, "openkakao-cli");
    }

    #[test]
    fn test_credentials_path_not_empty() {
        let path = credentials_path().unwrap();
        assert!(path.to_string_lossy().contains("openkakao"));
        assert!(path.to_string_lossy().ends_with("credentials.json"));
    }

    #[cfg(unix)]
    #[test]
    fn test_save_credentials_sets_600_permissions() {
        use std::os::unix::fs::MetadataExt;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("openkakao").join("credentials.json");
        let creds = KakaoCredentials::new(
            "tok".to_string(),
            1,
            "u".to_string(),
            "3.7.0".to_string(),
            "agent".to_string(),
            "a".to_string(),
        );
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let data = serde_json::to_string_pretty(&creds).unwrap();
        {
            use std::os::unix::fs::OpenOptionsExt;
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .mode(0o600)
                .open(&path)
                .unwrap();
            file.write_all(data.as_bytes()).unwrap();
        }
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let meta = std::fs::metadata(&path).unwrap();
        assert_eq!(meta.mode() & 0o777, 0o600);
    }

    #[test]
    fn test_save_credentials_replaces_the_file_atomically() {
        // The save writes a temporary sibling and renames it, so a reader never
        // sees a half-written file and no leftover temporary remains
        // (2026-09-16).
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("openkakao").join("credentials.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"{\"oauth_token\":\"old\"}").unwrap();

        let creds = KakaoCredentials::new(
            "new-token".to_string(),
            7,
            "u".to_string(),
            "3.7.0".to_string(),
            "agent".to_string(),
            "a".to_string(),
        );
        let data = serde_json::to_string_pretty(&creds).unwrap();
        let temporary = path.with_extension("json.tmp");
        fs::write(&temporary, data.as_bytes()).unwrap();
        fs::rename(&temporary, &path).unwrap();

        let loaded: KakaoCredentials =
            serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(loaded.oauth_token, "new-token");
        assert!(!temporary.exists(), "임시 파일이 남았습니다");
    }
}
