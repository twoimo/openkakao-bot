//! Fixed application payload. No directory traversal, PATH lookup, or venv copying.
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};

pub const MENUBAR_SCRIPT: &str = "scripts/auto-reply-menubar.py";
pub const LOCAL_MLX_READINESS_SCRIPT: &str = "scripts/local_mlx_model_readiness.py";
pub const VOICE_SCRIPT: &str = "scripts/jarvis_voice.py";
pub const TOOL_RUNTIME_SCRIPT: &str = "scripts/jarvis_tool_runtime.py";
pub const AX_UI_SCRIPT: &str = "scripts/auto_reply_ax_ui.py";
pub const BROWSER_USE_SCRIPT: &str = "scripts/jarvis_browser_use.py";
pub const METRICS_SCRIPT: &str = "scripts/auto_reply_metrics.py";
pub const CLI: &str = "bin/openkakao-cli";
pub const WAKE_MODEL: &str = "voice/models/hey_jarvis_ko_ridge.onnx";
pub const DATA_FILES: &[&str] = &[
    MENUBAR_SCRIPT,
    LOCAL_MLX_READINESS_SCRIPT,
    "scripts/_auto_reply_menubar_wrapper.cpython-311.pyc",
    "scripts/_bujamentor_menubar_overlay.cpython-311.pyc",
    "scripts/_bujamentor_menubar_impl.cpython-311.pyc",
    "scripts/auto-reply-tui.py",
    "scripts/bujamentor-tui.py",
    "scripts/auto_reply_transition_journal.py",
    "scripts/auto_reply_operator_prompt_store.py",
    "scripts/bujamentor_operator_prompt_store.py",
    "scripts/auto-reply-operator-prompts.json",
    "scripts/auto_reply_reference_store.py",
    "scripts/auto_reply_reference_search.py",
    "scripts/auto_reply_knowledge_graph.py",
    "scripts/auto_reply_ondevice.py",
    "scripts/local_mlx_gateway.py",
    "scripts/mlx_serve_lifecycle.py",
    METRICS_SCRIPT,
    AX_UI_SCRIPT,
    BROWSER_USE_SCRIPT,
    TOOL_RUNTIME_SCRIPT,
    VOICE_SCRIPT,
    "scripts/jarvis_abort.py",
    WAKE_MODEL,
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResourceError {
    Missing,
    Unsafe,
    DevelopmentDisabled,
}

#[derive(Clone, Copy)]
pub enum Kind {
    Directory,
    Data,
    Executable,
}

/// Check every component before using a path. Canonicalizing first would hide
/// symlinks (including dangling links and symlinked parent directories).
pub fn validate_path(path: &Path, kind: Kind) -> Result<(), ResourceError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
    {
        return Err(ResourceError::Unsafe);
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component);
        let meta = fs::symlink_metadata(&current).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                ResourceError::Missing
            } else {
                ResourceError::Unsafe
            }
        })?;
        if meta.file_type().is_symlink() {
            return Err(ResourceError::Unsafe);
        }
        let valid = if current != path || matches!(kind, Kind::Directory) {
            meta.is_dir() && meta.permissions().mode() & 0o111 != 0
        } else {
            meta.is_file()
                && meta.len() > 0
                && meta.permissions().mode() & 0o444 != 0
                && (!matches!(kind, Kind::Executable) || meta.permissions().mode() & 0o111 != 0)
        };
        if !valid {
            return Err(ResourceError::Unsafe);
        }
    }
    Ok(())
}

#[derive(Clone, Debug)]
pub struct ResourceLayout {
    pub root: PathBuf,
    pub installed: bool,
    pub script: PathBuf,
    pub bin: PathBuf,
}

#[derive(Default)]
pub struct DevOverrides {
    pub root: Option<PathBuf>,
    pub script: Option<PathBuf>,
    pub bin: Option<PathBuf>,
}

impl ResourceLayout {
    pub fn discover(
        executable: &Path,
        checkout: &Path,
        dev_enabled: bool,
        overrides: DevOverrides,
    ) -> Result<Self, ResourceError> {
        // Even an unsigned/debug .app is installed mode. An incomplete app
        // must never run the checkout embedded in CARGO_MANIFEST_DIR.
        let app = executable
            .ancestors()
            .find(|p| p.extension().is_some_and(|e| e == "app"));
        let layout = if let Some(app) = app {
            if executable.parent() != Some(app.join("Contents/MacOS").as_path()) {
                return Err(ResourceError::Unsafe);
            }
            validate_path(executable, Kind::Executable)?;
            let root = app.join("Contents/Resources");
            Self {
                script: root.join(MENUBAR_SCRIPT),
                bin: root.join(CLI),
                root,
                installed: true,
            }
        } else if dev_enabled {
            let root = overrides.root.unwrap_or_else(|| checkout.to_path_buf());
            Self {
                script: overrides
                    .script
                    .unwrap_or_else(|| root.join(MENUBAR_SCRIPT)),
                bin: overrides
                    .bin
                    .unwrap_or_else(|| root.join("target/debug/openkakao-cli")),
                root,
                installed: false,
            }
        } else {
            return Err(ResourceError::DevelopmentDisabled);
        };
        layout.validate()?;
        Ok(layout)
    }

    pub fn validate(&self) -> Result<(), ResourceError> {
        validate_path(&self.root, Kind::Directory)?;
        for relative in DATA_FILES {
            validate_path(&self.root.join(relative), Kind::Data)?;
        }
        // Dev overrides remain inside the explicitly selected safe root.
        if !self.script.starts_with(&self.root) || !self.bin.starts_with(&self.root) {
            return Err(ResourceError::Unsafe);
        }
        validate_path(&self.script, Kind::Data)?;
        validate_path(&self.bin, Kind::Executable)?;
        let model = fs::metadata(self.root.join(WAKE_MODEL)).map_err(|_| ResourceError::Missing)?;
        if model.len() > 64 * 1024 * 1024 {
            return Err(ResourceError::Unsafe);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().canonicalize().unwrap().join(format!(
                "jarvis-resources-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&root).unwrap();
            Self(root)
        }
        fn file(&self, path: &Path, executable: bool) {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"fixture only; never execute").unwrap();
            fs::set_permissions(
                path,
                fs::Permissions::from_mode(if executable { 0o755 } else { 0o644 }),
            )
            .unwrap();
        }
        fn payload(&self, root: &Path, installed: bool) {
            for relative in DATA_FILES {
                self.file(&root.join(relative), false);
            }
            self.file(
                &root.join(if installed {
                    CLI
                } else {
                    "target/debug/openkakao-cli"
                }),
                true,
            );
        }
        fn app(&self) -> (PathBuf, PathBuf) {
            let app = self.0.join("Moved Jarvis.app");
            let executable = app.join("Contents/MacOS/openkakao-jarvis-desktop");
            self.file(&executable, true);
            let root = app.join("Contents/Resources");
            self.payload(&root, true);
            (executable, root)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn installed_resources_discovery_ignores_checkout_and_overrides_even_in_debug() {
        let f = Fixture::new();
        let (executable, root) = f.app();
        let invalid = f.0.join("never-read-checkout-or-venv");
        for dev in [true, false] {
            let layout = ResourceLayout::discover(
                &executable,
                &invalid,
                dev,
                DevOverrides {
                    root: Some(invalid.clone()),
                    script: Some(invalid.clone()),
                    bin: Some(invalid.clone()),
                },
            )
            .unwrap();
            assert!(layout.installed);
            assert_eq!(layout.root, root);
            assert_eq!(layout.script, root.join(MENUBAR_SCRIPT));
            assert_eq!(layout.bin, root.join(CLI));
        }
    }

    #[test]
    fn incomplete_bundle_never_falls_back_to_valid_development_checkout() {
        let f = Fixture::new();
        let (exe, root) = f.app();
        let checkout = f.0.join("checkout");
        f.payload(&checkout, false);
        for relative in DATA_FILES.iter().copied().chain(std::iter::once(CLI)) {
            let path = root.join(relative);
            fs::remove_file(&path).unwrap();
            assert_eq!(
                ResourceLayout::discover(&exe, &checkout, true, DevOverrides::default())
                    .unwrap_err(),
                ResourceError::Missing
            );
            f.file(&path, relative == CLI);
        }
        fs::remove_dir_all(root).unwrap();
        assert!(ResourceLayout::discover(&exe, &checkout, true, DevOverrides::default()).is_err());
    }

    #[test]
    fn symlinked_files_parents_roots_and_dangling_links_fail_closed() {
        let f = Fixture::new();
        for target in [MENUBAR_SCRIPT, VOICE_SCRIPT, WAKE_MODEL, CLI, "scripts", ""] {
            let (exe, root) = f.app();
            let path = if target.is_empty() {
                root.clone()
            } else {
                root.join(target)
            };
            let moved = f.0.join("moved");
            fs::rename(&path, &moved).unwrap();
            symlink(&moved, &path).unwrap();
            assert_eq!(
                ResourceLayout::discover(&exe, &f.0, true, DevOverrides::default()).unwrap_err(),
                ResourceError::Unsafe
            );
            fs::remove_file(&path).unwrap();
            fs::rename(&moved, &path).unwrap();
        }
        let (exe, root) = f.app();
        let path = root.join(CLI);
        fs::remove_file(&path).unwrap();
        symlink(f.0.join("does-not-exist"), &path).unwrap();
        assert_eq!(
            ResourceLayout::discover(&exe, &f.0, true, DevOverrides::default()).unwrap_err(),
            ResourceError::Unsafe
        );
    }

    #[test]
    fn rejects_wrong_types_and_missing_execution_or_read_permissions() {
        let f = Fixture::new();
        let (exe, root) = f.app();
        for relative in [MENUBAR_SCRIPT, CLI] {
            let path = root.join(relative);
            fs::remove_file(&path).unwrap();
            fs::create_dir(&path).unwrap();
            assert!(ResourceLayout::discover(&exe, &f.0, true, DevOverrides::default()).is_err());
            fs::remove_dir(&path).unwrap();
            f.file(&path, relative == CLI);
            fs::set_permissions(&path, fs::Permissions::from_mode(0o000)).unwrap();
            assert!(ResourceLayout::discover(&exe, &f.0, true, DevOverrides::default()).is_err());
            fs::set_permissions(
                &path,
                fs::Permissions::from_mode(if relative == CLI { 0o755 } else { 0o644 }),
            )
            .unwrap();
        }
        fs::set_permissions(root.join(CLI), fs::Permissions::from_mode(0o644)).unwrap();
        assert!(ResourceLayout::discover(&exe, &f.0, true, DevOverrides::default()).is_err());
    }

    #[test]
    fn checkout_fallback_and_overrides_are_development_only_and_contained() {
        let f = Fixture::new();
        let checkout = f.0.join("checkout");
        f.payload(&checkout, false);
        let exe = checkout.join("desktop/target/debug/jarvis");
        let layout =
            ResourceLayout::discover(&exe, &checkout, true, DevOverrides::default()).unwrap();
        assert!(!layout.installed);
        assert_eq!(layout.bin, checkout.join("target/debug/openkakao-cli"));
        assert_eq!(
            ResourceLayout::discover(&exe, &checkout, false, DevOverrides::default()).unwrap_err(),
            ResourceError::DevelopmentDisabled
        );
        let custom = checkout.join("custom-menubar.py");
        f.file(&custom, false);
        let layout = ResourceLayout::discover(
            &exe,
            &f.0.join("absent"),
            true,
            DevOverrides {
                root: Some(checkout.clone()),
                script: Some(custom.clone()),
                bin: None,
            },
        )
        .unwrap();
        assert_eq!(layout.script, custom);
        assert!(ResourceLayout::discover(
            &exe,
            &checkout,
            true,
            DevOverrides {
                script: Some(f.0.join("outside.py")),
                ..Default::default()
            }
        )
        .is_err());
        assert!(validate_path(&checkout.join("../outside"), Kind::Data).is_err());
    }

    #[test]
    fn tauri_resources_declare_exact_allowlist_without_globs_venvs_or_secrets() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let resources = config["bundle"]["resources"].as_object().unwrap();
        assert_eq!(resources.len(), DATA_FILES.len() + 1);
        for relative in DATA_FILES.iter().copied().chain(std::iter::once(CLI)) {
            assert_eq!(resources[&format!("bundle-resources/{relative}")], relative);
        }
    }

    #[test]
    fn tool_runtime_and_import_dependencies_are_exact_resources() {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let resources = config["bundle"]["resources"].as_object().unwrap();
        for relative in [
            TOOL_RUNTIME_SCRIPT,
            AX_UI_SCRIPT,
            BROWSER_USE_SCRIPT,
            METRICS_SCRIPT,
        ] {
            assert!(DATA_FILES.contains(&relative));
            assert_eq!(resources[&format!("bundle-resources/{relative}")], relative);
        }
    }
}
