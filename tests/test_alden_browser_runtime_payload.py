from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "alden-browser-runtime.sh"
REQUIREMENTS = ROOT / "browser" / "requirements-runtime.txt"


class AldenBrowserRuntimePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="alden-browser-runtime-")
        self.root = Path(self.temp.name).resolve()
        self.wheelhouse = self.root / "wheelhouse"
        self.browsers = self.root / "ms-playwright"
        self.wheelhouse.mkdir()
        (self.wheelhouse / "fixture.whl").write_bytes(b"fixture wheel")
        self._make_browser_payload(self.browsers)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _make_browser_payload(root: Path) -> None:
        chromium = root / "chromium-1243"
        headless = root / "chromium_headless_shell-1243"
        ffmpeg = root / "ffmpeg-1011"
        chromium_bin = (
            chromium
            / "chrome-mac-arm64"
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
        headless_bin = headless / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell"
        ffmpeg_bin = ffmpeg / "ffmpeg-mac"
        for marker in [
            chromium / "INSTALLATION_COMPLETE",
            headless / "INSTALLATION_COMPLETE",
            ffmpeg / "INSTALLATION_COMPLETE",
        ]:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        for executable in [chromium_bin, headless_bin, ffmpeg_bin]:
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("fixture executable\n", encoding="utf-8")
            executable.chmod(0o755)
        resources = chromium / "chrome-mac-arm64" / "resources"
        resources.mkdir(parents=True)
        (resources / "target.dat").write_text("fixture\n", encoding="utf-8")
        (resources / "internal-link").symlink_to("target.dat")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.root / "home")
        return subprocess.run(
            ["/bin/sh", str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )

    def test_manifest_pins_runtime_dependency_and_browser_contract(self) -> None:
        result = self._run("manifest")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["runtime"], "browser")
        self.assertEqual(manifest["python"]["version"], "3.11.16")
        self.assertEqual(manifest["requirements"]["browser-use"], "0.13.10")
        self.assertEqual(manifest["requirements"]["playwright"], "1.63.0")
        self.assertEqual(manifest["playwright"]["chromium_revision"], "1243")
        self.assertEqual(manifest["playwright"]["ffmpeg_revision"], "1011")

    def test_preflight_accepts_fixed_payload_and_internal_browser_symlinks(self) -> None:
        result = self._run(
            "preflight",
            str(REQUIREMENTS),
            str(self.wheelhouse),
            str(self.browsers),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "preflight=ok")

    def test_preflight_rejects_tampered_requirements(self) -> None:
        tampered = self.root / "requirements.txt"
        tampered.write_bytes(REQUIREMENTS.read_bytes() + b"\n# tampered\n")
        result = self._run(
            "preflight",
            str(tampered),
            str(self.wheelhouse),
            str(self.browsers),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)

    def test_archive_validation_rejects_traversal_inside_python_root(self) -> None:
        # Exercise only the archive scanner, before extraction or installation.
        definitions = SCRIPT.read_text().rsplit("\ncommand=", 1)[0]
        archive = self.root / "traversal.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            member = tarfile.TarInfo("python/../escaped")
            member.size = 0
            handle.addfile(member)
        scan = self.root / "scan"
        scan.mkdir()
        result = subprocess.run(
            ["/bin/sh", "-c", definitions + '\nvalidate_archive_structure "$1" "$2"',
             "archive-test", str(archive), str(scan)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe CPython archive path", result.stderr)
        self.assertFalse((self.root / "escaped").exists())

    def test_preflight_rejects_non_wheel_artifacts(self) -> None:
        (self.wheelhouse / "unexpected.py").write_text("fixture\n")
        result = self._run("preflight", str(REQUIREMENTS), str(self.wheelhouse), str(self.browsers))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-wheel artifact", result.stderr)

    def test_preflight_rejects_wrong_or_symlinked_top_level_browser_revision(self) -> None:
        wrong = self.root / "wrong-ms-playwright"
        self._make_browser_payload(wrong)
        (wrong / "chromium-1243").rename(wrong / "chromium-1244")
        result = self._run(
            "preflight",
            str(REQUIREMENTS),
            str(self.wheelhouse),
            str(wrong),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chromium revision directory", result.stderr)

        linked = self.root / "linked-ms-playwright"
        linked.symlink_to(self.browsers, target_is_directory=True)
        result = self._run(
            "preflight",
            str(REQUIREMENTS),
            str(self.wheelhouse),
            str(linked),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Playwright browser payload is a symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
