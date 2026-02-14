"""
Unit tests for spread split and crop helpers.

These tests build synthetic images in memory, so they are fast and do not
require filesystem fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

# Allow tests to import from src/ without installing the package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdf_toolkit.page_images import detect_gutter_x, find_crop_bbox, split_spread_image


def _make_synthetic_spread() -> Image.Image:
    """Create a synthetic spread scan: dark background, bright pages, dark gutter."""

    image = Image.new("L", (400, 200), color=20)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 15, 175, 185), fill=245)
    draw.rectangle((225, 15, 380, 185), fill=245)
    draw.rectangle((195, 0, 205, 199), fill=5)
    return image.convert("RGB")


class PageImageHeuristicsTests(unittest.TestCase):
    def test_detect_gutter_near_expected_center(self) -> None:
        spread = _make_synthetic_spread()
        gray = spread.convert("L")
        gutter_x, used_fallback = detect_gutter_x(
            gray=gray,
            gutter_search_frac=0.35,
            x_step=2,
            y_step=2,
        )
        self.assertFalse(used_fallback)
        self.assertLessEqual(abs(gutter_x - 200), 8)

    def test_split_produces_left_and_right_halves(self) -> None:
        spread = _make_synthetic_spread()
        left, right = split_spread_image(spread, gutter_x=200)
        self.assertEqual(left.size[1], spread.size[1])
        self.assertEqual(right.size[1], spread.size[1])
        self.assertEqual(left.size[0] + right.size[0], spread.size[0])

    def test_crop_bbox_reduces_background(self) -> None:
        spread = _make_synthetic_spread()
        left, _ = split_spread_image(spread, gutter_x=200)
        bbox, used_fallback, note = find_crop_bbox(
            image=left,
            crop_threshold=180,
            pad_px=5,
            min_area_frac=0.25,
        )
        self.assertFalse(used_fallback)
        self.assertIsNone(note)
        self.assertNotEqual(bbox, (0, 0, left.width, left.height))

    def test_crop_bbox_fallback_to_full_image_when_empty_or_tiny(self) -> None:
        dark = Image.new("L", (200, 100), color=10).convert("RGB")
        bbox, used_fallback, note = find_crop_bbox(
            image=dark,
            crop_threshold=180,
            pad_px=5,
            min_area_frac=0.25,
        )
        self.assertEqual(bbox, (0, 0, 200, 100))
        self.assertTrue(used_fallback)
        self.assertIsNotNone(note)


if __name__ == "__main__":
    unittest.main()
