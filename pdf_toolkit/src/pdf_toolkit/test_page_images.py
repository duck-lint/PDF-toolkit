"""
Unit tests for spread split and crop helpers.

These tests build synthetic images in memory, so they are fast and do not
require filesystem fixtures.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

# Add the repository src/ directory so tests run from a fresh checkout.
SRC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_DIR))

from pdf_toolkit.page_images import (
    _page_number_crop_boxes,
    detect_gutter_x,
    extract_printed_page_number,
    find_crop_bbox,
    split_spread_image,
    which_tesseract,
)


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

    def test_extract_printed_page_number_no_tesseract(self) -> None:
        page = Image.new("RGB", (1200, 1800), color="white")
        with patch("pdf_toolkit.page_images.which_tesseract", return_value=None):
            extracted = extract_printed_page_number(
                page,
                {
                    "page_num_strip_frac": 0.12,
                    "page_num_corner_w_frac": 0.28,
                    "page_num_corner_h_frac": 0.45,
                    "page_num_psm": 7,
                    "page_num_max": 5000,
                    "page_num_debug": False,
                },
            )
        self.assertIsNone(extracted["printed_page"])
        self.assertEqual(extracted["reason"], "no_tesseract")

    def test_page_number_corner_region_bounds(self) -> None:
        left, right = _page_number_crop_boxes(
            1000,
            1500,
            {
                "page_num_strip_frac": 0.12,
                "page_num_corner_w_frac": 0.28,
                "page_num_corner_h_frac": 0.45,
            },
        )
        self.assertEqual(left, (0, 0, 280, 81))
        self.assertEqual(right, (720, 0, 1000, 81))
        self.assertLess(left[0], left[2])
        self.assertLess(left[1], left[3])
        self.assertLess(right[0], right[2])
        self.assertLess(right[1], right[3])

    @unittest.skipUnless(
        os.environ.get("PDFTK_RUN_TESSERACT_TESTS") == "1",
        "Set PDFTK_RUN_TESSERACT_TESTS=1 to run OCR integration checks.",
    )
    def test_extract_printed_page_number_with_tesseract(self) -> None:
        tesseract_exe = which_tesseract()
        if tesseract_exe is None:
            self.skipTest("tesseract executable not found in PATH")

        page_num_max = 5000
        page = Image.new("RGB", (1600, 2200), color="white")
        draw = ImageDraw.Draw(page)
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 140)
        except OSError:
            font = ImageFont.load_default()
        draw.text((1240, 20), "123", fill="black", font=font)

        extracted = extract_printed_page_number(
            page,
            {
                "page_num_strip_frac": 0.12,
                "page_num_corner_w_frac": 0.28,
                "page_num_corner_h_frac": 0.45,
                "page_num_psm": 7,
                "page_num_max": page_num_max,
                "page_num_debug": False,
            },
            tesseract_exe=tesseract_exe,
        )
        print(
            f"OCR integration raw_left={extracted['raw_left']!r} "
            f"raw_right={extracted['raw_right']!r}"
        )

        printed_page = extracted["printed_page"]
        if printed_page is not None:
            self.assertIsInstance(printed_page, int)
            self.assertGreaterEqual(printed_page, 1)
            self.assertLessEqual(printed_page, page_num_max)
        else:
            self.assertIn(
                extracted["reason"],
                {"no_digits", "out_of_range", "tesseract_failed"},
            )


if __name__ == "__main__":
    unittest.main()
