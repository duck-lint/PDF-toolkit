"""
Unit tests for page-images YAML config loading and precedence.
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stdout

# Add the repository src/ directory so tests run from a fresh checkout.
SRC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_DIR))

from pdf_toolkit.cli import (
    _build_page_images_effective_config,
    _build_parser,
    _extract_page_images_section,
    main,
)
from pdf_toolkit.config import DEFAULT_PAGE_IMAGES, deep_merge
from pdf_toolkit.utils import UserError


class PageImagesConfigTests(unittest.TestCase):
    def test_deep_merge_nested_overlay_wins(self) -> None:
        merged = deep_merge(
            {"a": 1, "nested": {"x": 1, "y": 2}},
            {"nested": {"y": 20, "z": 30}},
        )
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["nested"]["x"], 1)
        self.assertEqual(merged["nested"]["y"], 20)
        self.assertEqual(merged["nested"]["z"], 30)

    def test_wrapper_form_ignores_root_siblings(self) -> None:
        section = _extract_page_images_section(
            {"mode": "crop", "page_images": {"mode": "split", "split_ratio": 2.0}}
        )
        self.assertEqual(section["mode"], "split")
        self.assertEqual(section["split_ratio"], 2.0)

    def test_unknown_nested_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.yaml"
            path.write_text(
                "page_images:\n"
                "  mode: auto\n"
                "  bad_key: 1\n",
                encoding="utf-8",
            )
            args = _build_parser().parse_args(
                ["page-images", "--in_dir", "in", "--out_dir", "out", "--config", str(path)]
            )
            with self.assertRaises(UserError):
                _build_page_images_effective_config(args)

    def test_precedence_defaults_then_yaml_then_explicit_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cfg.yaml"
            path.write_text(
                "page_images:\n"
                "  mode: split\n"
                "  split_ratio: 2.5\n"
                "  glob: '*.jpg'\n",
                encoding="utf-8",
            )
            args = _build_parser().parse_args(
                [
                    "page-images",
                    "--in_dir",
                    "in",
                    "--out_dir",
                    "out",
                    "--config",
                    str(path),
                    "--mode",
                    "crop",
                ]
            )
            effective, _ = _build_page_images_effective_config(args)
            self.assertEqual(effective["mode"], "crop")
            self.assertEqual(effective["split_ratio"], 2.5)
            self.assertEqual(effective["glob"], "*.jpg")
            self.assertEqual(effective["pad_px"], DEFAULT_PAGE_IMAGES["pad_px"])

    def test_dump_default_config_without_paths(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = main(["page-images", "--dump-default-config"])
        self.assertEqual(rc, 0)
        dumped = stream.getvalue()
        self.assertIn("page_images:", dumped)
        self.assertNotIn("page_numbers:", dumped)


if __name__ == "__main__":
    unittest.main()
