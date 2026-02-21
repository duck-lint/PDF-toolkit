"""
Unit tests for spread split and crop helpers.

These tests build synthetic images in memory, so they are fast and do not
require filesystem fixtures.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

try:
    from PIL import Image, ImageDraw
except ModuleNotFoundError:  # pragma: no cover - optional dependency for local test runs
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    page_images_mod = importlib.import_module("pdf-toolkit.page_images")
    apply_split_symmetry_strategy = page_images_mod._apply_split_symmetry_strategy
    detect_gutter_x = page_images_mod.detect_gutter_x
    find_crop_bbox = page_images_mod.find_crop_bbox
    split_spread_image = page_images_mod.split_spread_image
except ModuleNotFoundError:  # pragma: no cover - optional dependency for local test runs
    apply_split_symmetry_strategy = None  # type: ignore[assignment]
    detect_gutter_x = None  # type: ignore[assignment]
    find_crop_bbox = None  # type: ignore[assignment]
    split_spread_image = None  # type: ignore[assignment]


def _make_synthetic_spread() -> Image.Image:
    """Create a synthetic spread scan: dark background, bright pages, dark gutter."""

    image = Image.new("L", (400, 200), color=20)
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 15, 175, 185), fill=245)
    draw.rectangle((225, 15, 380, 185), fill=245)
    draw.rectangle((195, 0, 205, 199), fill=5)
    return image.convert("RGB")


@unittest.skipIf(
    Image is None or detect_gutter_x is None or apply_split_symmetry_strategy is None,
    "Pillow is required for page-images tests.",
)
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

    def test_split_with_gutter_trim_reduces_total_width(self) -> None:
        spread = _make_synthetic_spread()
        left_no_trim, right_no_trim = split_spread_image(spread, gutter_x=200, gutter_trim_px=0)
        left_trimmed, right_trimmed = split_spread_image(spread, gutter_x=200, gutter_trim_px=10)

        total_no_trim = left_no_trim.size[0] + right_no_trim.size[0]
        total_trimmed = left_trimmed.size[0] + right_trimmed.size[0]
        self.assertEqual(total_no_trim, spread.size[0])
        self.assertEqual(total_no_trim - total_trimmed, 20)

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

    def test_crop_bbox_edge_inset_shrinks_bbox(self) -> None:
        spread = _make_synthetic_spread()
        left, _ = split_spread_image(spread, gutter_x=200)
        bbox_no_inset, used_fallback_no_inset, _ = find_crop_bbox(
            image=left,
            crop_threshold=180,
            pad_px=5,
            min_area_frac=0.25,
            edge_inset_px=0,
        )
        bbox_inset, used_fallback_inset, _ = find_crop_bbox(
            image=left,
            crop_threshold=180,
            pad_px=5,
            min_area_frac=0.25,
            edge_inset_px=5,
        )

        self.assertFalse(used_fallback_no_inset)
        self.assertFalse(used_fallback_inset)
        self.assertGreater(bbox_inset[0], bbox_no_inset[0])
        self.assertGreater(bbox_inset[1], bbox_no_inset[1])
        self.assertLess(bbox_inset[2], bbox_no_inset[2])
        self.assertLess(bbox_inset[3], bbox_no_inset[3])

    def test_outer_margin_frac_clamps_left_boundary(self) -> None:
        spread = _make_synthetic_spread()
        left, _ = split_spread_image(spread, gutter_x=200)
        bbox, used_fallback, _ = find_crop_bbox(
            image=left,
            crop_threshold=180,
            pad_px=5,
            min_area_frac=0.25,
            edge_inset_px=0,
            outer_margin_frac=0.2,
            is_left_page=True,
        )
        self.assertFalse(used_fallback)
        self.assertGreaterEqual(bbox[0], int(left.width * 0.2))

    def test_symmetry_match_max_width_equalizes_widths(self) -> None:
        left_bbox, right_bbox, note = apply_split_symmetry_strategy(
            left_bbox=(20, 10, 150, 190),   # width 130
            right_bbox=(20, 10, 170, 190),  # width 150
            left_image_width=200,
            right_image_width=200,
            gutter_x=200,
            right_offset_x=200,
            strategy="match_max_width",
        )
        self.assertIsNone(note)
        self.assertEqual(left_bbox[2] - left_bbox[0], right_bbox[2] - right_bbox[0])

    def test_symmetry_mirror_from_gutter_mirrors_distances(self) -> None:
        left_bbox, right_bbox, note = apply_split_symmetry_strategy(
            left_bbox=(20, 10, 180, 190),
            right_bbox=(30, 10, 180, 190),
            left_image_width=200,
            right_image_width=200,
            gutter_x=200,
            right_offset_x=200,
            strategy="mirror_from_gutter",
        )
        self.assertIsNone(note)
        left_distance = 200 - left_bbox[2]
        right_distance = (200 + right_bbox[0]) - 200
        self.assertEqual(left_distance, right_distance)

    def test_symmetry_independent_preserves_original_bboxes(self) -> None:
        left_in = (30, 12, 170, 188)
        right_in = (18, 12, 175, 188)
        left_out, right_out, note = apply_split_symmetry_strategy(
            left_bbox=left_in,
            right_bbox=right_in,
            left_image_width=200,
            right_image_width=200,
            gutter_x=200,
            right_offset_x=200,
            strategy="independent",
        )
        self.assertIsNone(note)
        self.assertEqual(left_out, left_in)
        self.assertEqual(right_out, right_in)

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
