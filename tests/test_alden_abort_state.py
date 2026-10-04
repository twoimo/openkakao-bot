from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from alden_abort import (
    ABORT_LOCK_NAME,
    ABORT_MAX_EPOCH,
    ABORT_STATE_NAME,
    AbortController,
    AbortStateError,
    read_abort_state,
)


def _write_payload(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _valid_payload(*, epoch: int = 1, latched: bool = True, reason: str = "stop") -> dict[str, object]:
    return {
        "schema_version": 1,
        "epoch": epoch,
        "latched": latched,
        "reason": reason,
    }


class AldenAbortStateReaderTests(unittest.TestCase):
    def test_missing_file_is_the_only_implicit_initial_state(self):
        with TemporaryDirectory() as temp_dir:
            state = read_abort_state(Path(temp_dir) / ABORT_STATE_NAME)
        self.assertEqual((state.epoch, state.latched, state.reason, state.error), (0, False, "", None))

    def test_schema_is_exact_and_scalar_types_are_strict(self):
        cases = {
            "malformed": b"{",
            "unknown_schema": json.dumps({**_valid_payload(), "schema_version": 2}).encode(),
            "schema_bool": json.dumps({**_valid_payload(), "schema_version": True}).encode(),
            "extra_field": json.dumps({**_valid_payload(), "extra": 1}).encode(),
            "missing_field": json.dumps({"schema_version": 1, "epoch": 1, "latched": True}).encode(),
            "epoch_bool": json.dumps({**_valid_payload(), "epoch": True}).encode(),
            "epoch_negative": json.dumps({**_valid_payload(), "epoch": -1}).encode(),
            "epoch_over_max": json.dumps({**_valid_payload(), "epoch": ABORT_MAX_EPOCH + 1}).encode(),
            "latched_int": json.dumps({**_valid_payload(), "latched": 1}).encode(),
            "latched_empty_reason": json.dumps({**_valid_payload(), "reason": ""}).encode(),
            "reason_control": json.dumps({**_valid_payload(), "reason": "stop\nnow"}).encode(),
            "reason_surrogate": b'{"schema_version":1,"epoch":1,"latched":true,"reason":"\\ud800"}',
            "reason_too_long": json.dumps({**_valid_payload(), "reason": "x" * 97}).encode(),
            "bad_unlatched_reason": json.dumps(
                _valid_payload(epoch=2, latched=False, reason="automatic_resume")
            ).encode(),
            "duplicate_key": (
                b'{"schema_version":1,"epoch":1,"epoch":2,"latched":true,"reason":"stop"}'
            ),
        }
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ABORT_STATE_NAME
            for name, raw in cases.items():
                with self.subTest(name=name):
                    path.write_bytes(raw)
                    path.chmod(0o600)
                    state = read_abort_state(path)
                    self.assertTrue(state.latched)
                    self.assertTrue(state.is_error)

    def test_valid_initial_resume_and_custom_abort_reason_are_accepted(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ABORT_STATE_NAME
            for payload in (
                _valid_payload(epoch=0, latched=False, reason=""),
                _valid_payload(epoch=9, latched=False, reason="human_resume"),
                _valid_payload(epoch=10, latched=True, reason="controller custom reason"),
            ):
                with self.subTest(payload=payload):
                    _write_payload(path, payload)
                    state = read_abort_state(path)
                    self.assertFalse(state.is_error)
                    self.assertEqual(state.epoch, payload["epoch"])
                    self.assertEqual(state.latched, payload["latched"])
                    self.assertEqual(state.reason, payload["reason"])

    def test_mode_owner_link_symlink_directory_and_size_fail_closed(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ABORT_STATE_NAME

            _write_payload(path, _valid_payload())
            path.chmod(0o644)
            self.assertEqual(read_abort_state(path).error, "state_mode")

            _write_payload(path, _valid_payload())
            uid = root.stat().st_uid
            with mock.patch("alden_abort.os.geteuid", side_effect=[uid, uid + 1]):
                self.assertEqual(read_abort_state(path).error, "state_owner")

            _write_payload(path, _valid_payload())
            hardlink = root / "hardlink.json"
            os.link(path, hardlink)
            self.assertEqual(read_abort_state(path).error, "state_link_count")
            hardlink.unlink()

            path.unlink()
            target = root / "target.json"
            _write_payload(target, _valid_payload())
            path.symlink_to(target)
            self.assertTrue(read_abort_state(path).is_error)
            path.unlink()

            path.symlink_to(root / "missing-target.json")
            self.assertTrue(read_abort_state(path).is_error)
            path.unlink()

            path.mkdir(mode=0o700)
            self.assertEqual(read_abort_state(path).error, "state_type")
            path.rmdir()

            path.write_bytes(b"x" * 4097)
            path.chmod(0o600)
            self.assertEqual(read_abort_state(path).error, "state_too_large")

    def test_fifo_reader_is_nonblocking_and_fail_closed(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ABORT_STATE_NAME
            os.mkfifo(path, 0o600)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
            probe = (
                "from pathlib import Path; "
                "from alden_abort import read_abort_state; "
                "import sys; "
                "s=read_abort_state(Path(sys.argv[1])); "
                "print(int(s.latched), s.error)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", probe, str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
                env=env,
            )
            self.assertTrue(completed.stdout.startswith("1 state_type"))

    def test_symlinked_or_non_private_state_root_fails_closed(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            actual = base / "actual"
            actual.mkdir(mode=0o700)
            _write_payload(actual / ABORT_STATE_NAME, _valid_payload())

            alias = base / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            self.assertTrue(read_abort_state(alias / ABORT_STATE_NAME).is_error)

            actual.chmod(0o755)
            self.assertEqual(read_abort_state(actual / ABORT_STATE_NAME).error, "state_root_mode")


class AldenAbortStateWriterTests(unittest.TestCase):
    def test_busy_cross_process_lock_has_a_bounded_non_mutating_timeout(self):
        import fcntl
        import time

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)
            controller.abort("stop")
            before = (root / ABORT_STATE_NAME).read_bytes()
            fd = os.open(root / ABORT_LOCK_NAME, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                started = time.monotonic()
                with self.assertRaisesRegex(AbortStateError, "lock_timeout"):
                    controller.resume_after_human_action()
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual((root / ABORT_STATE_NAME).read_bytes(), before)
            finally:
                os.close(fd)

    def test_abort_resume_and_tokens_are_monotonic_and_irreversible(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller = AbortController(root)
            old_token = controller.token()
            self.assertFalse(old_token.is_cancelled())

            first = controller.abort("operator requested stop")
            self.assertEqual((first.epoch, first.latched, first.reason), (1, True, "operator requested stop"))
            self.assertTrue(old_token.is_cancelled())
            latched_token = controller.token()
            self.assertTrue(latched_token.is_cancelled())

            resumed = controller.resume_after_human_action()
            self.assertEqual((resumed.epoch, resumed.latched, resumed.reason), (2, False, "human_resume"))
            self.assertTrue(old_token.is_cancelled())
            self.assertTrue(latched_token.is_cancelled())

            fresh = controller.token()
            self.assertFalse(fresh.is_cancelled())
            second = controller.abort("second stop")
            self.assertEqual(second.epoch, 3)
            self.assertTrue(fresh.is_cancelled())

            with self.assertRaisesRegex(AbortStateError, "resume_requires_latched"):
                AbortController(Path(temp_dir) / "other").resume_after_human_action()

    def test_writer_refuses_invalid_or_unsafe_existing_state(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ABORT_STATE_NAME
            path.write_bytes(b"{malformed")
            path.chmod(0o600)
            before = path.read_bytes()
            with self.assertRaisesRegex(AbortStateError, "state_unsafe"):
                AbortController(root).abort("stop")
            self.assertEqual(path.read_bytes(), before)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ABORT_STATE_NAME
            _write_payload(path, _valid_payload())
            path.chmod(0o644)
            before = path.read_bytes()
            with self.assertRaisesRegex(AbortStateError, "state_unsafe"):
                AbortController(root).abort("stop")
            self.assertEqual(path.read_bytes(), before)

    def test_lock_file_must_also_be_private_regular_and_owned(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "lock-target"
            target.write_text("x", encoding="utf-8")
            target.chmod(0o600)
            (root / ABORT_LOCK_NAME).symlink_to(target)
            with self.assertRaisesRegex(AbortStateError, "lock_open|lock_unsafe"):
                AbortController(root).abort("stop")
            self.assertFalse((root / ABORT_STATE_NAME).exists())

    def test_epoch_overflow_is_a_clear_non_mutating_error(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ABORT_STATE_NAME
            _write_payload(path, _valid_payload(epoch=ABORT_MAX_EPOCH, latched=True, reason="stop"))
            before = path.read_bytes()
            controller = AbortController(root)
            with self.assertRaisesRegex(AbortStateError, "epoch_overflow"):
                controller.abort("again")
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(AbortStateError, "epoch_overflow"):
                controller.resume_after_human_action()
            self.assertEqual(path.read_bytes(), before)

    def test_created_files_are_private_and_temps_are_cleaned(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "state"
            state = AbortController(root).abort("stop")
            self.assertEqual(state.epoch, 1)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / ABORT_STATE_NAME).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((root / ABORT_LOCK_NAME).stat().st_mode), 0o600)
            self.assertEqual(list(root.glob(f".{ABORT_STATE_NAME}.*.tmp")), [])

    def test_concurrent_process_aborts_produce_unique_monotonic_epochs(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SCRIPTS) + os.pathsep + env.get("PYTHONPATH", "")
            worker = (
                "from pathlib import Path; "
                "from alden_abort import AbortController; "
                "import sys; "
                "print(AbortController(Path(sys.argv[1])).abort(sys.argv[2]).epoch)"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", worker, str(root), f"process-{index}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                for index in range(12)
            ]
            # Keep the directory alive until every owned child is reaped. An
            # early assertion used to remove it while other writers still ran,
            # hiding the original failure behind ENOENT and leaked processes.
            outputs = []
            try:
                for process in processes:
                    stdout, stderr = process.communicate(timeout=5)
                    outputs.append((process.returncode, stdout, stderr))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.communicate(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate(timeout=1)
            epochs: list[int] = []
            for returncode, stdout, stderr in outputs:
                self.assertEqual(returncode, 0, stderr)
                epochs.append(int(stdout.strip()))

            self.assertEqual(sorted(epochs), list(range(1, 13)))
            final = read_abort_state(root / ABORT_STATE_NAME)
            self.assertEqual(final.epoch, 12)
            self.assertTrue(final.latched)
            self.assertFalse(final.is_error)


if __name__ == "__main__":
    unittest.main()
