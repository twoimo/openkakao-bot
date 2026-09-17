import unittest
from scripts.auto_reply_ondevice import (
    HardwareSpec,
    detect_hardware,
    detect_available_engines,
    recommend_ondevice_setup,
    ondevice_summary_dict,
)

class TestOnDeviceHardware(unittest.TestCase):
    def test_detect_hardware_returns_valid_spec(self):
        hw = detect_hardware()
        self.assertIsInstance(hw.chip, str)
        self.assertGreater(hw.cores, 0)
        self.assertGreater(hw.memory_bytes, 0)
        self.assertGreater(hw.memory_gb, 0)

    def test_recommendation_for_large_apple_silicon(self):
        hw = HardwareSpec(
            chip="Apple M5 Max",
            cores=18,
            memory_bytes=128 * 1024**3,
            memory_gb=128.0,
            is_apple_silicon=True,
        )
        rec = recommend_ondevice_setup(hw)
        self.assertEqual(rec.primary_engine, "mlx")
        self.assertIn("Qwen3.8", rec.recommended_model)
        self.assertIn("128.0GB", rec.reason)

    def test_recommendation_for_smaller_ram(self):
        hw = HardwareSpec(
            chip="Apple M1",
            cores=8,
            memory_bytes=16 * 1024**3,
            memory_gb=16.0,
            is_apple_silicon=True,
        )
        rec = recommend_ondevice_setup(hw)
        self.assertEqual(rec.primary_engine, "mlx")
        self.assertIn("7B", rec.recommended_model)

    def test_ondevice_summary_dict_serializable(self):
        summary = ondevice_summary_dict()
        self.assertIn("hardware", summary)
        self.assertIn("recommendation", summary)
        self.assertIn("primary_engine", summary["recommendation"])

if __name__ == "__main__":
    unittest.main()

