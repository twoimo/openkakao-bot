import plistlib
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_repo_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class JarvisDesktopLauncherTests(unittest.TestCase):
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
