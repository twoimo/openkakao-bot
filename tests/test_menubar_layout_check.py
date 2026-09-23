"""Layout gate boundaries for the retired Swift menu extra.

The check script decides which empty row bands are wasted layout. A window's
own edge padding is now judged by the same allowance as the top/bottom margin
rule, so the two rules cannot disagree about one piece of whitespace. These
tests pin that boundary: a few points of font-metric drift between macOS
versions used to flip the result (2026-09-22).
"""

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CHECK = SCRIPTS / "check-menubar-layout.py"


def load_check():
    spec = importlib.util.spec_from_file_location("check_menubar_layout", CHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["check_menubar_layout"] = module
    spec.loader.exec_module(module)
    return module


class EmptyRowBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.check = load_check()

    def image(self, bands, size=(640, 400), scale=1.0, top=16, bottom=17):
        return {
            "empty_row_bands": list(bands),
            "size": list(size),
            "scale": scale,
            "top_margin": top,
            "bottom_margin": bottom,
            "content_box": [0, 0, size[0] - 1, size[1] - 1 - bottom],
        }

    def problems(self, bands, **kwargs):
        return self.check.empty_row_band_problems("w", self.image(bands, **kwargs))

    def test_trailing_padding_at_the_allowance_passes(self):
        # Sonoma 러너가 실제로 낸 값: 400px 창의 아래 25px.
        self.assertEqual(self.problems([(375, 399, 25)]), [])

    def test_trailing_padding_past_the_allowance_fails(self):
        self.assertEqual(len(self.problems([(359, 399, 41)])), 1)

    def test_leading_padding_is_judged_the_same_way(self):
        self.assertEqual(self.problems([(0, 24, 25)]), [])
        self.assertEqual(len(self.problems([(0, 40, 41)])), 1)

    def test_internal_gap_keeps_the_tight_threshold(self):
        # 내용 사이의 25px 띠는 창 여백이 아니므로 그대로 실패해야 한다.
        self.assertEqual(len(self.problems([(100, 124, 25)])), 1)

    def test_collapsed_content_is_still_caught(self):
        # main에서 잡히던 log 창: 396px 창의 아래 226px가 비어 있었다.
        self.assertEqual(len(self.problems([(170, 395, 226)], size=(640, 396))), 1)

    def test_retina_capture_scales_the_allowance(self):
        self.assertEqual(
            self.problems([(740, 799, 60)], size=(1280, 800), scale=2.0), []
        )
        self.assertEqual(
            len(self.problems([(719, 799, 81)], size=(1280, 800), scale=2.0)), 1
        )

    def test_missing_scale_falls_back_to_one(self):
        image = self.image([(375, 399, 25)])
        image["scale"] = None
        self.assertEqual(self.check.empty_row_band_problems("w", image), [])

    def test_margin_rule_reuses_the_same_allowance(self):
        self.assertEqual(self.check.padding_limit_px(self.image([])), 40.0)
        self.assertEqual(self.check.padding_limit_px(self.image([], scale=2.0)), 80.0)


if __name__ == "__main__":
    unittest.main()
