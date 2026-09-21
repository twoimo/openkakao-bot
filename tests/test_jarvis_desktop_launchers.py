import ast
import plistlib
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class JarvisDesktopLauncherTests(unittest.TestCase):
    def test_staged_python_entrypoint_dependencies_are_complete_and_staging_lists_match(
        self,
    ) -> None:
        resource_layout = read_repo_file("desktop/src-tauri/src/resource_layout.rs")
        tauri_config = read_repo_file("desktop/src-tauri/tauri.conf.json")

        data_files_match = re.search(
            r"pub const DATA_FILES: &\[&str\] = &\[(.*?)\n\];",
            resource_layout,
            re.DOTALL,
        )
        self.assertIsNotNone(data_files_match)
        data_files = data_files_match.group(1)
        rust_constants = dict(
            re.findall(
                r'pub const ([A-Z][A-Z0-9_]*): &str = "([^"]+)";',
                resource_layout,
            )
        )
        rust_staged = set(re.findall(r'"(scripts/[^"]+\.py)"', data_files))
        for constant_name in re.findall(r"\b[A-Z][A-Z0-9_]*\b", data_files):
            relative_path = rust_constants.get(constant_name)
            if (
                relative_path is not None
                and relative_path.startswith("scripts/")
                and relative_path.endswith(".py")
            ):
                rust_staged.add(relative_path)

        tauri_pairs = re.findall(
            r'"bundle-resources/(scripts/[^"]+\.py)"\s*:\s*"(scripts/[^"]+\.py)"',
            tauri_config,
        )
        for source_path, destination_path in tauri_pairs:
            self.assertEqual(source_path, destination_path)
        tauri_staged = {destination_path for _, destination_path in tauri_pairs}

        self.assertEqual(
            rust_staged,
            tauri_staged,
            msg=(
                "Rust DATA_FILES and Tauri bundle.resources disagree for Python "
                f"scripts: rust_only={sorted(rust_staged - tauri_staged)}, "
                f"tauri_only={sorted(tauri_staged - rust_staged)}"
            ),
        )

        seeds = (
            "scripts/auto-reply-menubar.py",
            "scripts/local_mlx_model_readiness.py",
            "scripts/jarvis_voice.py",
            "scripts/jarvis_tool_runtime.py",
            "scripts/auto_reply_ax_ui.py",
            "scripts/jarvis_browser_use.py",
            "scripts/auto_reply_metrics.py",
        )
        pending = [seed for seed in seeds if (ROOT / seed).is_file()]
        resolved: set[str] = set()

        while pending:
            relative_path = pending.pop()
            if relative_path in resolved:
                continue
            resolved.add(relative_path)
            tree = ast.parse(
                read_repo_file(relative_path),
                filename=relative_path,
            )
            imported_modules: list[str] = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    imported_modules.extend(alias.name for alias in node.names)
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and node.module is not None
                ):
                    imported_modules.append(node.module)

            for module_name in imported_modules:
                local_path = (
                    "scripts/" + module_name.replace(".", "/") + ".py"
                )
                if (ROOT / local_path).is_file() and local_path not in resolved:
                    pending.append(local_path)

        missing = sorted(resolved - rust_staged)
        self.assertFalse(
            missing,
            msg=f"staged entry scripts require unstaged local modules: {missing}",
        )

    def test_primary_and_compat_shell_scripts_pass_sh_syntax_check(self) -> None:
        scripts = (
            "scripts/build-jarvis-desktop.sh",
            "scripts/install-jarvis-desktop.sh",
            "scripts/build-auto-reply-menubar.sh",
            "scripts/install-auto-reply-menubar.sh",
            "scripts/start-auto-reply-menubar.command",
        )

        for relative_path in scripts:
            with self.subTest(script=relative_path):
                result = subprocess.run(
                    ["/bin/sh", "-n", str(ROOT / relative_path)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=result.stderr or result.stdout,
                )

    def test_compat_wrappers_default_to_tauri_and_swift_is_opt_in(self) -> None:
        wrappers = {
            "scripts/build-auto-reply-menubar.sh": (
                "build-jarvis-desktop.sh",
                "build-swift-auto-reply-menubar.sh",
            ),
            "scripts/install-auto-reply-menubar.sh": (
                "install-jarvis-desktop.sh",
                "install-swift-auto-reply-menubar.sh",
            ),
            "scripts/start-auto-reply-menubar.command": (
                None,
                "start-swift-auto-reply-menubar.command",
            ),
        }

        for relative_path, (tauri_target, swift_target) in wrappers.items():
            with self.subTest(wrapper=relative_path):
                source = read_repo_file(relative_path)
                self.assertIn(
                    "BACKEND=${OPENKAKAO_MENUBAR_BACKEND:-tauri}", source
                )
                self.assertIn("swift)", source)
                self.assertIn(f'scripts/{swift_target}" "$@"', source)
                self.assertIn(
                    "OPENKAKAO_MENUBAR_BACKEND must be tauri or swift", source
                )
                self.assertIn("exit 2", source)
                if tauri_target is not None:
                    self.assertIn(f'scripts/{tauri_target}" "$@"', source)
                else:
                    self.assertIn("tauri)\n    ;;", source)

    def test_launch_agent_plist_targets_installed_tauri_app(self) -> None:
        plist_path = (
            ROOT
            / "desktop/launchd/com.openkakao.jarvis.desktop.plist.example"
        )
        with plist_path.open("rb") as stream:
            launch_agent = plistlib.load(stream)

        self.assertEqual(launch_agent["Label"], "com.openkakao.jarvis.desktop")
        self.assertEqual(
            launch_agent["ProgramArguments"],
            [
                "/Applications/OpenKakao Jarvis.app/Contents/MacOS/"
                "openkakao-jarvis-desktop"
            ],
        )
        self.assertIs(launch_agent["RunAtLoad"], True)
        self.assertEqual(launch_agent["LimitLoadToSessionType"], "Aqua")

    def test_installer_has_bounded_launch_agent_disappearance_wait(self) -> None:
        source = read_repo_file("scripts/install-jarvis-desktop.sh")

        self.assertIn("wait_for_absent() {", source)
        self.assertIn('while [ "$wait_attempt" -le 10 ]; do', source)
        self.assertIn("/bin/sleep 0.5", source)
        self.assertIn('wait_attempt=$((wait_attempt + 1))', source)
        self.assertIn(
            '"$LAUNCHCTL" print "$wait_service" >>"$wait_log" 2>&1', source
        )
        self.assertIn('wait_for_absent "$LEGACY_SERVICE"', source)
        self.assertIn('wait_for_absent "$JARVIS_SERVICE"', source)
        self.assertIn('"$BACKUP_DIR/$LEGACY_LABEL.disabled.plist"', source)
        self.assertIn("return 1", source)

    def test_renamed_swift_scripts_use_legacy_paths_directly(self) -> None:
        legacy_scripts = (
            "scripts/build-swift-auto-reply-menubar.sh",
            "scripts/install-swift-auto-reply-menubar.sh",
            "scripts/start-swift-auto-reply-menubar.command",
        )
        for relative_path in legacy_scripts:
            with self.subTest(script=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

        start_source = read_repo_file(
            "scripts/start-swift-auto-reply-menubar.command"
        )
        self.assertIn(
            'APP=$("$ROOT/scripts/build-swift-auto-reply-menubar.sh")',
            start_source,
        )
        self.assertNotIn(
            'APP=$("$ROOT/scripts/build-auto-reply-menubar.sh")', start_source
        )

        install_source = read_repo_file(
            "scripts/install-swift-auto-reply-menubar.sh"
        )
        self.assertIn(
            "build the app first: scripts/build-swift-auto-reply-menubar.sh",
            install_source,
        )
        self.assertNotIn(
            "build the app first: scripts/build-auto-reply-menubar.sh",
            install_source,
        )


if __name__ == "__main__":
    unittest.main()
