"""
Unit tests for page-images YAML config loading and precedence.
"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path
import unittest
from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from uuid import uuid4

from src.pdf_toolkit.cli import (
    _command_argv_for_manifest,
    _build_page_images_effective_config,
    _build_parser,
    _extract_page_images_section,
    _require_bool,
    main,
)
from src.pdf_toolkit.config import DEFAULT_PAGE_IMAGES, deep_merge
from src.pdf_toolkit.config import yaml as yaml_module
from src.pdf_toolkit.utils import UserError


@contextmanager
def _workspace_temp_dir():
    root = Path(__file__).resolve().parents[1] / ".tmp_tests"
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"test_cfg_{uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
        if yaml_module is None:
            self.skipTest("PyYAML is required for YAML config tests.")
        with _workspace_temp_dir() as tmpdir:
            path = tmpdir / "bad.yaml"
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
        if yaml_module is None:
            self.skipTest("PyYAML is required for YAML config tests.")
        with _workspace_temp_dir() as tmpdir:
            path = tmpdir / "cfg.yaml"
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
        if yaml_module is None:
            self.skipTest("PyYAML is required for YAML config tests.")
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = main(["page-images", "--dump-default-config"])
        self.assertEqual(rc, 0)
        dumped = stream.getvalue()
        self.assertIn("page_images:", dumped)
        self.assertNotIn("page_numbers:", dumped)

    def test_require_bool_accepts_true_false_only(self) -> None:
        self.assertTrue(_require_bool(True, "config.debug"))
        self.assertFalse(_require_bool(False, "config.debug"))
        with self.assertRaises(UserError):
            _require_bool("false", "config.debug")

    def test_command_argv_for_manifest_uses_passed_argv(self) -> None:
        original = sys.argv
        try:
            sys.argv = ["pdf_toolkit_entry"]
            self.assertEqual(
                _command_argv_for_manifest(["page-images", "--dry-run"]),
                ["pdf_toolkit_entry", "page-images", "--dry-run"],
            )
            self.assertEqual(
                _command_argv_for_manifest(None),
                ["pdf_toolkit_entry"],
            )
        finally:
            sys.argv = original

    def test_page_images_invalid_bool_in_config_fails_cleanly(self) -> None:
        if yaml_module is None:
            self.skipTest("PyYAML is required for YAML config tests.")
        with _workspace_temp_dir() as tmpdir:
            path = tmpdir / "cfg.yaml"
            path.write_text(
                "page_images:\n"
                "  overwrite: 'false'\n",
                encoding="utf-8",
            )
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(
                    [
                        "page-images",
                        "--in_dir",
                        "in",
                        "--out_dir",
                        "out",
                        "--config",
                        str(path),
                    ]
                )
            self.assertEqual(rc, 2)
            self.assertIn("config.overwrite must be true or false.", err.getvalue())


if __name__ == "__main__":
    unittest.main()
