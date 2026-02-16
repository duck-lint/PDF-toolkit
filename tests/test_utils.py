"""
Lightweight unit tests for parsing and validation helpers.

These are intentionally small, but they cover the most error-prone bits.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

utils_mod = importlib.import_module("pdf-toolkit.utils")
UserError = utils_mod.UserError
parse_page_ranges = utils_mod.parse_page_ranges
parse_page_spec = utils_mod.parse_page_spec
validate_degrees = utils_mod.validate_degrees


class ParsePageSpecTests(unittest.TestCase):
    def test_all(self) -> None:
        self.assertEqual(parse_page_spec("all", total_pages=3), [0, 1, 2])

    def test_ranges_and_singles(self) -> None:
        result = parse_page_spec("1-3,5,7-8", total_pages=10)
        self.assertEqual(result, [0, 1, 2, 4, 6, 7])

    def test_out_of_range(self) -> None:
        with self.assertRaises(UserError):
            parse_page_spec("1-5", total_pages=4)

    def test_duplicate_pages(self) -> None:
        with self.assertRaises(UserError):
            parse_page_spec("1-3,3-4", total_pages=10)

    def test_invalid_token(self) -> None:
        with self.assertRaises(UserError):
            parse_page_spec("1-a", total_pages=10)


class ParsePageRangesTests(unittest.TestCase):
    def test_ranges(self) -> None:
        result = parse_page_ranges("1-2,3,5-6", total_pages=6)
        self.assertEqual(result, [(0, 1), (2, 2), (4, 5)])

    def test_overlap(self) -> None:
        with self.assertRaises(UserError):
            parse_page_ranges("1-3,3-4", total_pages=10)

    def test_invalid(self) -> None:
        with self.assertRaises(UserError):
            parse_page_ranges("all", total_pages=5)


class ValidateDegreesTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(validate_degrees(90), 90)
        self.assertEqual(validate_degrees(180), 180)
        self.assertEqual(validate_degrees(270), 270)

    def test_invalid(self) -> None:
        with self.assertRaises(UserError):
            validate_degrees(45)


if __name__ == "__main__":
    unittest.main()
