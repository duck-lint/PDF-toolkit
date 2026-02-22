"""
Extra robustness tests for core helper behavior and manifest structure.
"""

from __future__ import annotations

import importlib
import io
import json
import shutil
import sys
from pathlib import Path
import unittest
from contextlib import contextmanager
from uuid import uuid4

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

manifest_mod = importlib.import_module("pdf-toolkit.manifest")
ManifestRecorder = manifest_mod.ManifestRecorder


@contextmanager
def _workspace_temp_dir():
    root = Path(__file__).resolve().parents[1] / ".tmp_tests"
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
            render_mod = importlib.import_module("pdf-toolkit.render")
            _compute_page_digits = render_mod._compute_page_digits
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(_compute_page_digits([]), 4)
        self.assertEqual(_compute_page_digits([1, 2, 3]), 4)
        self.assertEqual(_compute_page_digits([1, 9999, 10000]), 5)


class SplitHelperTests(unittest.TestCase):
    def test_chunk_ranges(self) -> None:
        try:
            split_mod = importlib.import_module("pdf-toolkit.split")
            _chunk_ranges = split_mod._chunk_ranges
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(
            _chunk_ranges(total_pages=10, pages_per_file=3),
            [(0, 2), (3, 5), (6, 8), (9, 9)],
        )

    def test_compute_part_digits(self) -> None:
        try:
            split_mod = importlib.import_module("pdf-toolkit.split")
            _compute_part_digits = split_mod._compute_part_digits
        except ModuleNotFoundError as exc:
            self.skipTest(f"Missing optional dependency: {exc}")

        self.assertEqual(_compute_part_digits(1), 2)
        self.assertEqual(_compute_part_digits(99), 2)
        self.assertEqual(_compute_part_digits(100), 3)


class RotateHelperTests(unittest.TestCase):
    def test_collect_image_files_returns_only_files_sorted(self) -> None:
        try:
            rotate_mod = importlib.import_module("pdf-toolkit.rotate")
            _collect_image_files = rotate_mod._collect_image_files
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
            tool_name="pdf-toolkit",
            tool_version="0.0.0",
            command="pdf-toolkit render --dry-run",
            options={"dry_run": True},
            inputs={"pdf": "in.pdf"},
            outputs={"out_dir": "out"},
            dry_run=True,
            console_stream=io.StringIO(),
        )
        recorder.log("hello")
        recorder.add_action("render_page", "dry-run", page=1, output="out/p1.png")

        manifest = recorder.build_manifest({"pages_selected": 1})
        self.assertEqual(manifest["tool"], "pdf-toolkit")
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
                tool_name="pdf-toolkit",
                tool_version="0.0.0",
                command="pdf-toolkit split --dry-run",
                options={},
                inputs={},
                outputs={},
                dry_run=True,
                console_stream=io.StringIO(),
            )
            recorder.write_manifest(out_path, {"ok": True})
            self.assertFalse(out_path.exists())

    def test_write_manifest_writes_json(self) -> None:
        with _workspace_temp_dir() as tmpdir:
            out_path = tmpdir / "manifest.json"
            recorder = ManifestRecorder(
                tool_name="pdf-toolkit",
                tool_version="0.0.0",
                command="pdf-toolkit split",
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


class ManifestVerbosityTests(unittest.TestCase):
    def _recorder(self, verbosity: str, stream: io.StringIO) -> ManifestRecorder:
        return ManifestRecorder(
            tool_name="pdf-toolkit",
            tool_version="0.0.0",
            command="pdf-toolkit",
            options={},
            inputs={},
            outputs={},
            dry_run=True,
            verbosity=verbosity,
            console_stream=stream,
        )

    def test_quiet_suppresses_info_but_prints_error(self) -> None:
        stream = io.StringIO()
        recorder = self._recorder("quiet", stream)
        recorder.log("hello-info")
        recorder.log("hello-error", level="error")
        output = stream.getvalue()
        self.assertNotIn("hello-info", output)
        self.assertIn("hello-error", output)
        self.assertEqual(len(recorder.logs), 2)

    def test_normal_prints_info_but_not_debug(self) -> None:
        stream = io.StringIO()
        recorder = self._recorder("normal", stream)
        recorder.log("hello-info")
        recorder.log("hello-debug", level="debug")
        output = stream.getvalue()
        self.assertIn("hello-info", output)
        self.assertNotIn("hello-debug", output)

    def test_verbose_prints_debug_with_level_prefix(self) -> None:
        stream = io.StringIO()
        recorder = self._recorder("verbose", stream)
        recorder.log("hello-debug", level="debug")
        self.assertIn("[debug] hello-debug", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
