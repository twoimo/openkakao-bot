from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.local_mlx_gateway import (
    MLX_MODEL_SWAP_LOCK_NAME,
    MlxModelSwapLeaseBusy,
    MlxRequestAdmissionClosed,
    mlx_model_request_lease,
    mlx_model_swap_lease,
    resolve_mlx_state_root,
)


class MlxModelRequestLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temp_dir.name) / "state"
        self.state_root.mkdir()
        self.lock_path = self.state_root / MLX_MODEL_SWAP_LOCK_NAME

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_request_lease_blocks_exclusive_swap_until_request_finishes(self) -> None:
        with mlx_model_request_lease(self.state_root):
            descriptor = os.open(self.lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)

    def test_exclusive_swap_rejects_request_before_it_can_reach_gateway(self) -> None:
        descriptor = os.open(
            self.lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(MlxRequestAdmissionClosed) as raised:
                with mlx_model_request_lease(self.state_root):
                    self.fail("request body ran while swap owned the exclusive lease")
            self.assertEqual(raised.exception.code, "model_swap_in_progress")
        finally:
            os.close(descriptor)

    def test_lease_releases_after_request_exception(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "synthetic request failure"):
            with mlx_model_request_lease(self.state_root):
                raise RuntimeError("synthetic request failure")

        descriptor = os.open(self.lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)

    def test_symlink_lock_file_fails_closed(self) -> None:
        target = Path(self.temp_dir.name) / "outside-lock"
        target.write_bytes(b"preserve")
        self.lock_path.symlink_to(target)

        with self.assertRaises(MlxRequestAdmissionClosed) as raised:
            with mlx_model_request_lease(self.state_root):
                self.fail("symlink lock must never reach the request body")

        self.assertEqual(raised.exception.code, "mlx_request_gate_unavailable")
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_state_root_override_precedes_default(self) -> None:
        override = Path(self.temp_dir.name) / "override"
        with patch.dict(os.environ, {"OPENKAKAO_STATE_ROOT": str(override)}):
            self.assertEqual(resolve_mlx_state_root(), override)

    def test_exclusive_swap_lease_rejects_active_request_without_waiting(self) -> None:
        with mlx_model_request_lease(self.state_root):
            with self.assertRaises(MlxModelSwapLeaseBusy):
                with mlx_model_swap_lease(self.state_root):
                    self.fail("swap entered while an inference request was active")


if __name__ == "__main__":
    unittest.main()
