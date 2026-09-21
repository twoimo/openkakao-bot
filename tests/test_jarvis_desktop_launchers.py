import ast
import os
import plistlib
import re
import subprocess
import tempfile
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

    def test_installer_guards_previous_bundle_cleanup_against_stray_pids(
        self,
    ) -> None:
        source = read_repo_file("scripts/install-jarvis-desktop.sh")

        self.assertIn("PS=${OPENKAKAO_PS:-/bin/ps}", source)
        self.assertIn("PGREP=${OPENKAKAO_PGREP:-/usr/bin/pgrep}", source)
        self.assertIn("LSOF=${OPENKAKAO_LSOF:-/usr/sbin/lsof}", source)
        self.assertIn("list_live_app_pids() {", source)
        self.assertIn('if [ -x "$PGREP" ]; then', source)
        self.assertIn('"$PGREP" -f "$APP_EXECUTABLE"', source)
        self.assertIn('"$PS" -axo pid=,command=', source)
        self.assertIn("reported_app_path() {", source)
        self.assertIn('"$LSOF" -p "$reported_pid" -a -d txt -Fn', source)
        self.assertIn("wait_for_pid() {", source)
        self.assertIn('while [ "$wait_attempt" -le 10 ]; do', source)
        self.assertIn(
            '"$LAUNCHCTL" print "$wait_service" >"$wait_log" 2>&1', source
        )
        self.assertIn("/bin/sleep 0.5", source)
        self.assertIn('wait_attempt=$((wait_attempt + 1))', source)
        self.assertIn('if ! LAUNCHD_PID=$(wait_for_pid "$JARVIS_SERVICE"', source)
        self.assertIn("LaunchAgent pid was never reported", source)
        self.assertIn('-v launchd_pid="$LAUNCHD_PID" -v installer_pid="$$"', source)
        self.assertIn('$0 != launchd_pid && $0 != installer_pid', source)
        self.assertIn("previous bundle is being kept", source)
        self.assertRegex(
            source,
            re.compile(
                r'if \[ -n "\$STRAY_PIDS" \]; then.*?exit 3.*?'
                r'if \[ -n "\$PREVIOUS_APP" \]',
                re.DOTALL,
            ),
        )

    def test_installer_duplicate_process_guard_with_fake_adapters(self) -> None:
        installer = ROOT / "scripts/install-jarvis-desktop.sh"

        def write_executable(path: Path, body: str) -> None:
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

        for stray in (False, True):
            with self.subTest(stray=stray), tempfile.TemporaryDirectory() as raw_tmp:
                tmp = Path(raw_tmp)
                bin_dir = tmp / "bin"
                applications_dir = tmp / "Applications"
                launch_agents_dir = tmp / "LaunchAgents"
                backup_dir = tmp / "backups"
                source_app = tmp / "source" / "OpenKakao Jarvis.app"
                source_bin = (
                    source_app
                    / "Contents"
                    / "MacOS"
                    / "openkakao-jarvis-desktop"
                )
                target_app = applications_dir / "OpenKakao Jarvis.app"
                target_bin = (
                    target_app
                    / "Contents"
                    / "MacOS"
                    / "openkakao-jarvis-desktop"
                )
                state_file = tmp / "launchctl.loaded"

                bin_dir.mkdir()
                launch_agents_dir.mkdir()
                backup_dir.mkdir()
                source_bin.parent.mkdir(parents=True)
                source_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                source_bin.chmod(0o755)
                target_bin.parent.mkdir(parents=True)
                target_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                target_bin.chmod(0o755)

                write_executable(
                    bin_dir / "launchctl",
                    """#!/bin/sh
set -eu
case "$1" in
  print)
    if [ -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE" ]; then
      printf '{\n    pid = 4242\n}\n'
      exit 0
    fi
    exit 1
    ;;
  bootstrap)
    : >"$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  kickstart)
    ;;
  bootout)
    rm -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  *)
    exit 2
    ;;
esac
""",
                )
                write_executable(
                    bin_dir / "ditto",
                    """#!/bin/sh
set -eu
/bin/cp -R "$1" "$2"
""",
                )
                write_executable(
                    bin_dir / "PlistBuddy",
                    "#!/bin/sh\nexit 0\n",
                )
                write_executable(
                    bin_dir / "plutil",
                    "#!/bin/sh\nexit 0\n",
                )
                write_executable(
                    bin_dir / "pgrep",
                    """#!/bin/sh
set -eu
printf '4242\n'
if [ "${OPENKAKAO_FAKE_STRAY:-0}" = 1 ]; then
  printf '7777\n'
fi
""",
                )
                write_executable(
                    bin_dir / "ps",
                    """#!/bin/sh
set -eu
if [ "$1" = "-p" ] && [ "$2" = "7777" ]; then
  printf '%s\n' "$OPENKAKAO_FAKE_INSTALLED_BIN"
  exit 0
fi
if [ "$1" = "-axo" ]; then
  printf ' 4242 %s\n' "$OPENKAKAO_FAKE_INSTALLED_BIN"
  if [ "${OPENKAKAO_FAKE_STRAY:-0}" = 1 ]; then
    printf ' 7777 %s\n' "$OPENKAKAO_FAKE_INSTALLED_BIN"
  fi
  exit 0
fi
exit 1
""",
                )
                write_executable(
                    bin_dir / "lsof",
                    "#!/bin/sh\nexit 1\n",
                )

                env = os.environ.copy()
                env.update(
                    {
                        "OPENKAKAO_APPLICATIONS_DIR": str(applications_dir),
                        "OPENKAKAO_LAUNCH_AGENTS_DIR": str(launch_agents_dir),
                        "OPENKAKAO_JARVIS_BACKUP_DIR": str(backup_dir),
                        "OPENKAKAO_JARVIS_APP_SOURCE": str(source_app),
                        "OPENKAKAO_LAUNCHCTL": str(bin_dir / "launchctl"),
                        "OPENKAKAO_DITTO": str(bin_dir / "ditto"),
                        "OPENKAKAO_PLISTBUDDY": str(bin_dir / "PlistBuddy"),
                        "OPENKAKAO_PLUTIL": str(bin_dir / "plutil"),
                        "OPENKAKAO_PS": str(bin_dir / "ps"),
                        "OPENKAKAO_PGREP": str(bin_dir / "pgrep"),
                        "OPENKAKAO_LSOF": str(bin_dir / "lsof"),
                        "OPENKAKAO_FAKE_LAUNCHCTL_STATE": str(state_file),
                        "OPENKAKAO_FAKE_INSTALLED_BIN": str(target_bin),
                        "OPENKAKAO_FAKE_STRAY": "1" if stray else "0",
                    }
                )

                result = subprocess.run(
                    ["/bin/sh", str(installer)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                previous_apps = list(
                    applications_dir.glob(".openkakao-jarvis.previous.*.app")
                )

                if stray:
                    self.assertEqual(result.returncode, 3, msg=result.stderr)
                    self.assertIn("stray pid 7777", result.stderr)
                    self.assertIn(
                        f"reported path: {target_bin}", result.stderr
                    )
                    self.assertIn("previous bundle is being kept", result.stderr)
                    self.assertEqual(len(previous_apps), 1)
                    self.assertTrue(previous_apps[0].is_dir())
                else:
                    self.assertEqual(result.returncode, 0, msg=result.stderr)
                    self.assertEqual(previous_apps, [])
                    created_backups = list(backup_dir.iterdir())
                    self.assertEqual(len(created_backups), 1)
                    self.assertEqual(
                        result.stdout.splitlines(),
                        [
                            f"installed: {target_app}",
                            f"LaunchAgent: gui/{os.getuid()}/com.openkakao.jarvis.desktop",
                            f"backup: {created_backups[0]}",
                        ],
                    )

    def test_installer_waits_for_launchd_pid_before_duplicate_guard(self) -> None:
        installer = ROOT / "scripts/install-jarvis-desktop.sh"

        def write_executable(path: Path, body: str) -> None:
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            bin_dir = tmp / "bin"
            applications_dir = tmp / "Applications"
            launch_agents_dir = tmp / "LaunchAgents"
            backup_dir = tmp / "backups"
            source_app = tmp / "source" / "OpenKakao Jarvis.app"
            source_bin = (
                source_app / "Contents" / "MacOS" / "openkakao-jarvis-desktop"
            )
            target_app = applications_dir / "OpenKakao Jarvis.app"
            target_bin = (
                target_app / "Contents" / "MacOS" / "openkakao-jarvis-desktop"
            )
            state_file = tmp / "launchctl.loaded"
            count_file = tmp / "launchctl.print-count"

            bin_dir.mkdir()
            launch_agents_dir.mkdir()
            backup_dir.mkdir()
            source_bin.parent.mkdir(parents=True)
            source_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            source_bin.chmod(0o755)
            target_bin.parent.mkdir(parents=True)
            target_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target_bin.chmod(0o755)

            write_executable(
                bin_dir / "launchctl",
                """#!/bin/sh
set -eu
case "$1" in
  print)
    if [ ! -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE" ]; then
      exit 1
    fi
    count=0
    if [ -f "$OPENKAKAO_FAKE_PRINT_COUNT" ]; then
      count=$(/bin/cat "$OPENKAKAO_FAKE_PRINT_COUNT")
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$OPENKAKAO_FAKE_PRINT_COUNT"
    if [ "$count" -ge "$OPENKAKAO_FAKE_PID_AFTER" ]; then
      printf '{\n    pid = 4242\n}\n'
    else
      printf '{\n    state = spawn scheduled\n}\n'
    fi
    ;;
  bootstrap)
    : >"$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  kickstart)
    ;;
  bootout)
    rm -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                bin_dir / "ditto",
                "#!/bin/sh\nset -eu\n/bin/cp -R \"$1\" \"$2\"\n",
            )
            write_executable(bin_dir / "PlistBuddy", "#!/bin/sh\nexit 0\n")
            write_executable(bin_dir / "plutil", "#!/bin/sh\nexit 0\n")
            write_executable(
                bin_dir / "pgrep",
                "#!/bin/sh\nprintf '4242\\n'\n",
            )
            write_executable(bin_dir / "ps", "#!/bin/sh\nexit 1\n")
            write_executable(bin_dir / "lsof", "#!/bin/sh\nexit 1\n")

            env = os.environ.copy()
            env.update(
                {
                    "OPENKAKAO_APPLICATIONS_DIR": str(applications_dir),
                    "OPENKAKAO_LAUNCH_AGENTS_DIR": str(launch_agents_dir),
                    "OPENKAKAO_JARVIS_BACKUP_DIR": str(backup_dir),
                    "OPENKAKAO_JARVIS_APP_SOURCE": str(source_app),
                    "OPENKAKAO_LAUNCHCTL": str(bin_dir / "launchctl"),
                    "OPENKAKAO_DITTO": str(bin_dir / "ditto"),
                    "OPENKAKAO_PLISTBUDDY": str(bin_dir / "PlistBuddy"),
                    "OPENKAKAO_PLUTIL": str(bin_dir / "plutil"),
                    "OPENKAKAO_PS": str(bin_dir / "ps"),
                    "OPENKAKAO_PGREP": str(bin_dir / "pgrep"),
                    "OPENKAKAO_LSOF": str(bin_dir / "lsof"),
                    "OPENKAKAO_FAKE_LAUNCHCTL_STATE": str(state_file),
                    "OPENKAKAO_FAKE_PRINT_COUNT": str(count_file),
                    "OPENKAKAO_FAKE_PID_AFTER": "3",
                }
            )

            result = subprocess.run(
                ["/bin/sh", str(installer)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(
                list(applications_dir.glob(".openkakao-jarvis.previous.*.app")),
                [],
            )
            self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "3")

    def test_installer_keeps_previous_bundle_when_launchd_pid_never_appears(
        self,
    ) -> None:
        installer = ROOT / "scripts/install-jarvis-desktop.sh"

        def write_executable(path: Path, body: str) -> None:
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            bin_dir = tmp / "bin"
            applications_dir = tmp / "Applications"
            launch_agents_dir = tmp / "LaunchAgents"
            backup_dir = tmp / "backups"
            source_app = tmp / "source" / "OpenKakao Jarvis.app"
            source_bin = (
                source_app / "Contents" / "MacOS" / "openkakao-jarvis-desktop"
            )
            target_bin = (
                applications_dir
                / "OpenKakao Jarvis.app"
                / "Contents"
                / "MacOS"
                / "openkakao-jarvis-desktop"
            )
            state_file = tmp / "launchctl.loaded"
            count_file = tmp / "launchctl.print-count"

            bin_dir.mkdir()
            launch_agents_dir.mkdir()
            backup_dir.mkdir()
            source_bin.parent.mkdir(parents=True)
            source_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            source_bin.chmod(0o755)
            target_bin.parent.mkdir(parents=True)
            target_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target_bin.chmod(0o755)

            write_executable(
                bin_dir / "launchctl",
                """#!/bin/sh
set -eu
case "$1" in
  print)
    if [ ! -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE" ]; then
      exit 1
    fi
    count=0
    if [ -f "$OPENKAKAO_FAKE_PRINT_COUNT" ]; then
      count=$(/bin/cat "$OPENKAKAO_FAKE_PRINT_COUNT")
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$OPENKAKAO_FAKE_PRINT_COUNT"
    printf '{\n    state = spawn scheduled\n}\n'
    ;;
  bootstrap)
    : >"$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  kickstart)
    ;;
  bootout)
    rm -f "$OPENKAKAO_FAKE_LAUNCHCTL_STATE"
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                bin_dir / "ditto",
                "#!/bin/sh\nset -eu\n/bin/cp -R \"$1\" \"$2\"\n",
            )
            write_executable(bin_dir / "PlistBuddy", "#!/bin/sh\nexit 0\n")
            write_executable(bin_dir / "plutil", "#!/bin/sh\nexit 0\n")
            write_executable(
                bin_dir / "pgrep",
                "#!/bin/sh\nprintf '4242\\n'\n",
            )
            write_executable(bin_dir / "ps", "#!/bin/sh\nexit 1\n")
            write_executable(bin_dir / "lsof", "#!/bin/sh\nexit 1\n")

            env = os.environ.copy()
            env.update(
                {
                    "OPENKAKAO_APPLICATIONS_DIR": str(applications_dir),
                    "OPENKAKAO_LAUNCH_AGENTS_DIR": str(launch_agents_dir),
                    "OPENKAKAO_JARVIS_BACKUP_DIR": str(backup_dir),
                    "OPENKAKAO_JARVIS_APP_SOURCE": str(source_app),
                    "OPENKAKAO_LAUNCHCTL": str(bin_dir / "launchctl"),
                    "OPENKAKAO_DITTO": str(bin_dir / "ditto"),
                    "OPENKAKAO_PLISTBUDDY": str(bin_dir / "PlistBuddy"),
                    "OPENKAKAO_PLUTIL": str(bin_dir / "plutil"),
                    "OPENKAKAO_PS": str(bin_dir / "ps"),
                    "OPENKAKAO_PGREP": str(bin_dir / "pgrep"),
                    "OPENKAKAO_LSOF": str(bin_dir / "lsof"),
                    "OPENKAKAO_FAKE_LAUNCHCTL_STATE": str(state_file),
                    "OPENKAKAO_FAKE_PRINT_COUNT": str(count_file),
                }
            )

            result = subprocess.run(
                ["/bin/sh", str(installer)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            previous_apps = list(
                applications_dir.glob(".openkakao-jarvis.previous.*.app")
            )

            self.assertEqual(result.returncode, 3, msg=result.stderr)
            self.assertIn("LaunchAgent pid was never reported", result.stderr)
            self.assertIn("previous bundle is being kept", result.stderr)
            self.assertEqual(len(previous_apps), 1)
            self.assertTrue(previous_apps[0].is_dir())
            self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "11")

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
