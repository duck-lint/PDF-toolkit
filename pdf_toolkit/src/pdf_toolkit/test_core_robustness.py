"""
Extra robustness tests for core helper behavior and manifest structure.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
import unittest
from contextlib import contextmanager
from uuid import uuid4

# Add the repository src/ directory so tests run from a fresh checkout.
SRC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_DIR))

from pdf_toolkit.manifest import ManifestRecorder


@contextmanager
def _workspace_temp_dir():
    root = Path(__file__).resolve().parents[2] / ".tmp_tests"
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"test_core_{uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class RenderHelperTests(unittest.TestCase):
    def test_compute_page_digits(self) -> None:
        try:
            from pdf_toolkit.render import _compute_page_digits
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(_compute_page_digits([]), 4)
        self.assertEqual(_compute_page_digits([1, 2, 3]), 4)
        self.assertEqual(_compute_page_digits([1, 9999, 10000]), 5)


class SplitHelperTests(unittest.TestCase):
    def test_chunk_ranges(self) -> None:
        try:
            from pdf_toolkit.split import _chunk_ranges
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(
            _chunk_ranges(total_pages=10, pages_per_file=3),
            [(0, 2), (3, 5), (6, 8), (9, 9)],
        )

    def test_compute_part_digits(self) -> None:
        try:
            from pdf_toolkit.split import _compute_part_digits
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(_compute_part_digits(1), 2)
        self.assertEqual(_compute_part_digits(99), 2)
        self.assertEqual(_compute_part_digits(100), 3)


class RotateHelperTests(unittest.TestCase):
    def test_collect_image_files_returns_only_files_sorted(self) -> None:
        try:
            from pdf_toolkit.rotate import _collect_image_files
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        with _workspace_temp_dir() as root:
            (root / "b.png").write_bytes(b"x")
            (root / "a.png").write_bytes(b"x")
            (root / "c.png").mkdir()

            files = _collect_image_files(root, "*.png")
            self.assertEqual([path.name for path in files], ["a.png", "b.png"])


class ManifestStructureTests(unittest.TestCase):
    def test_build_manifest_has_expected_shape(self) -> None:
        recorder = ManifestRecorder(
            tool_name="pdf_toolkit",
            tool_version="0.0.0",
            command="pdf_toolkit render --dry-run",
            options={"dry_run": True},
            inputs={"pdf": "in.pdf"},
            outputs={"out_dir": "out"},
            dry_run=True,
        )
        recorder.log("hello")
        recorder.add_action("render_page", "dry-run", page=1, output="out/p1.png")

        manifest = recorder.build_manifest({"pages_selected": 1})
        self.assertEqual(manifest["tool"], "pdf_toolkit")
        self.assertIn("started_at", manifest)
        self.assertIn("ended_at", manifest)
        self.assertIn("logs", manifest)
        self.assertIn("actions", manifest)
        self.assertEqual(manifest["action_counts"].get("dry-run"), 1)
        self.assertEqual(manifest["actions"][0]["page"], 1)

    def test_write_manifest_respects_dry_run(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            out_path = tmpdir / "manifest.json"
            recorder = ManifestRecorder(
                tool_name="pdf_toolkit",
                tool_version="0.0.0",
                command="pdf_toolkit split --dry-run",
                options={},
                inputs={},
                outputs={},
                dry_run=True,
            )
            recorder.write_manifest(out_path, {"ok": True})
            self.assertFalse(out_path.exists())

    def test_write_manifest_writes_json(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            out_path = tmpdir / "manifest.json"
            recorder = ManifestRecorder(
                tool_name="pdf_toolkit",
                tool_version="0.0.0",
                command="pdf_toolkit split",
                options={},
                inputs={},
                outputs={},
                dry_run=False,
            )
            recorder.add_action("split_part", "written", part=1)
            recorder.write_manifest(out_path, {"parts": 1})

            self.assertTrue(out_path.exists())
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["summary"]["parts"], 1)
            self.assertEqual(loaded["action_counts"].get("written"), 1)


if __name__ == "__main__":
    unittest.main()
