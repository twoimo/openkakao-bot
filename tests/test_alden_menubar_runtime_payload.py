from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "alden-menubar-runtime.sh"
PINNED_SHA = "e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547"
SENTINEL = "ALDEN_CPYTHON_3_11_16_ARM64_OK"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@unittest.skipUnless(
    sys.platform == "darwin" and platform.machine() == "arm64",
    "Alden runtime payload is macOS arm64-only",
)
class AldenMenubarRuntimePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="alden-runtime-test-")
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.tmpdir = self.root / "tmp"
        self.home.mkdir()
        self.tmpdir.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _compile_runtime(self, *, probe_output: str = SENTINEL) -> Path:
        payload = self.root / f"payload-{len(list(self.root.glob('payload-*')))}"
        bindir = payload / "python" / "bin"
        libdir = payload / "python" / "lib"
        licensedir = libdir / "python3.11"
        bindir.mkdir(parents=True)
        licensedir.mkdir(parents=True)

        probe_c = payload / "probe.c"
        probe_c.write_text(
            textwrap.dedent(
                f"""
                #include <stdio.h>
                #include <string.h>

                int main(int argc, char **argv) {{
                    if (argc != 5 || strcmp(argv[1], "-I") != 0 ||
                        strcmp(argv[2], "-S") != 0 || strcmp(argv[3], "-c") != 0) {{
                        return 64;
                    }}
                    if (strstr(argv[4], "sys.version_info[:3]==(3,11,16)") == NULL ||
                        strstr(argv[4], "platform.machine()==\\\"arm64\\\"") == NULL) {{
                        return 65;
                    }}
                    puts("{probe_output}");
                    return 0;
                }}
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        dylib_c = payload / "dylib.c"
        dylib_c.write_text("int alden_test_symbol(void) { return 0; }\n", encoding="utf-8")

        subprocess.run(
            [
                "/usr/bin/clang",
                "-arch",
                "arm64",
                "-Wl,-rpath,@executable_path/../lib",
                "-o",
                str(bindir / "python3.11"),
                str(probe_c),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            [
                "/usr/bin/clang",
                "-arch",
                "arm64",
                "-dynamiclib",
                "-o",
                str(libdir / "libpython3.11.dylib"),
                str(dylib_c),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        (licensedir / "LICENSE.txt").write_text("synthetic CPython license fixture\n", encoding="utf-8")
        (bindir / "python3").symlink_to("python3.11")
        return payload

    def _archive(self, payload: Path, *, name: str = "runtime.tar.gz") -> Path:
        archive = self.root / name
        with tarfile.open(archive, "w:gz", dereference=False) as handle:
            handle.add(payload / "python", arcname="python", recursive=True)
        return archive

    def _patched_script(self, archive: Path, *, suffix: str = "") -> Path:
        script = self.root / f"alden-menubar-runtime{suffix}.sh"
        source = SCRIPT.read_text(encoding="utf-8")
        digest = _sha256(archive)
        self.assertIn(f"SOURCE_SHA256='{PINNED_SHA}'", source)
        source = source.replace(
            f"SOURCE_SHA256='{PINNED_SHA}'",
            f"SOURCE_SHA256='{digest}'",
            1,
        )
        script.write_text(source, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    def _run(
        self,
        script: Path,
        *args: str,
        path: str = "/usr/bin:/bin",
        home: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home or self.home),
                "TMPDIR": str(self.tmpdir),
                "PATH": path,
            }
        )
        return subprocess.run(
            ["/bin/sh", str(script), *args],
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )

    def _package_valid_fixture(self) -> tuple[Path, Path, Path, Path]:
        payload = self._compile_runtime()
        archive = self._archive(payload)
        script = self._patched_script(archive)
        output = self.root / "dist"
        result = self._run(script, "package", str(archive), str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        release_archive = output / "Alden-menubar-cpython-3.11.16-macos-arm64.tar.gz"
        manifest = output / "alden-menubar-runtime-v1.json"
        installer = output / "install-alden-menubar-runtime.sh"
        self.assertEqual(_sha256(release_archive), _sha256(archive))
        return release_archive, manifest, installer, script

    def test_successful_package_and_offline_install_use_fixed_home_path(self) -> None:
        release_archive, manifest, installer, _script = self._package_valid_fixture()

        result = self._run(
            installer,
            "install",
            str(release_archive),
            str(manifest),
            path="/path-that-does-not-exist",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = (
            self.home
            / "Library"
            / "Application Support"
            / "openkakao"
            / "runtimes"
            / "menubar"
            / "bin"
            / "python3.11"
        )
        self.assertTrue(installed.is_file())
        self.assertFalse(installed.is_symlink())
        self.assertIn("installed_runtime=", result.stdout)
        self.assertNotIn("curl", SCRIPT.read_text(encoding="utf-8"))

        repeat = self._run(
            installer,
            "install",
            str(release_archive),
            str(manifest),
            path="/path-that-does-not-exist",
        )
        self.assertNotEqual(repeat.returncode, 0)
        self.assertIn("refusing to overwrite", repeat.stderr)

    def test_archive_hash_mismatch_fails_before_packaging(self) -> None:
        payload = self._compile_runtime()
        archive = self._archive(payload)
        script = self._patched_script(archive, suffix="-hash")
        with archive.open("ab") as handle:
            handle.write(b"tampered")

        result = self._run(script, "package", str(archive), str(self.root / "out-hash"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse((self.root / "out-hash").exists())

    def test_manifest_provenance_mismatch_fails_before_runtime_parent_creation(self) -> None:
        release_archive, manifest, installer, _script = self._package_valid_fixture()
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("20260924", "20260925", 1), encoding="utf-8")

        result = self._run(installer, "install", str(release_archive), str(manifest))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned provenance contract", result.stderr)
        self.assertFalse((self.home / "Library").exists())

    def test_symlink_in_home_ancestry_fails_before_runtime_parent_creation(self) -> None:
        release_archive, manifest, installer, _script = self._package_valid_fixture()
        linked_root = self.root / "linked"
        linked_root.symlink_to(self.root, target_is_directory=True)

        result = self._run(
            installer,
            "install",
            str(release_archive),
            str(manifest),
            home=linked_root / "home",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink component", result.stderr)
        self.assertFalse((self.home / "Library").exists())

    def test_wrong_architecture_is_rejected(self) -> None:
        payload = self._compile_runtime()
        python_bin = payload / "python" / "bin" / "python3.11"
        python_bin.write_text("#!/bin/sh\necho not-a-mach-o\n", encoding="utf-8")
        python_bin.chmod(0o755)
        archive = self._archive(payload, name="wrong-arch.tar.gz")
        script = self._patched_script(archive, suffix="-arch")

        result = self._run(script, "package", str(archive), str(self.root / "out-arch"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a macOS arm64 Mach-O", result.stderr)

    def test_wrong_version_or_sentinel_is_rejected(self) -> None:
        payload = self._compile_runtime(probe_output="WRONG_VERSION")
        archive = self._archive(payload, name="wrong-version.tar.gz")
        script = self._patched_script(archive, suffix="-version")

        result = self._run(script, "package", str(archive), str(self.root / "out-version"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact CPython 3.11.16 arm64 sentinel", result.stderr)

    def test_parent_traversal_and_escaping_symlink_are_rejected(self) -> None:
        payload = self._compile_runtime()

        traversal = self._archive(payload, name="traversal.tar.gz")
        # gzip tar archives cannot be appended in-place with tarfile, so rebuild
        # the malicious fixture with one explicit parent-traversal member.
        traversal.unlink()
        with tarfile.open(traversal, "w:gz", dereference=False) as handle:
            handle.add(payload / "python", arcname="python", recursive=True)
            info = tarfile.TarInfo("../escape")
            info.size = 1
            handle.addfile(info, fileobj=io.BytesIO(b"x"))
        traversal_script = self._patched_script(traversal, suffix="-traversal")
        traversal_result = self._run(
            traversal_script,
            "package",
            str(traversal),
            str(self.root / "out-traversal"),
        )
        self.assertNotEqual(traversal_result.returncode, 0)
        self.assertIn("unsafe runtime archive path", traversal_result.stderr)

        unsafe_payload = self._compile_runtime()
        unsafe_link = unsafe_payload / "python" / "bin" / "python3"
        unsafe_link.unlink()
        unsafe_link.symlink_to("../../escape")
        symlink_archive = self._archive(unsafe_payload, name="unsafe-link.tar.gz")
        symlink_script = self._patched_script(symlink_archive, suffix="-symlink")
        symlink_result = self._run(
            symlink_script,
            "package",
            str(symlink_archive),
            str(self.root / "out-symlink"),
        )
        self.assertNotEqual(symlink_result.returncode, 0)
        self.assertIn("symlink target is not a local basename", symlink_result.stderr)

if __name__ == "__main__":
    unittest.main()
