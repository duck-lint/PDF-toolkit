"""
Shared helpers for running pdf-toolkit CLI entrypoints in tests.
"""

from __future__ import annotations

import importlib
import io
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _normalize_exit_code(value: object) -> int:
    """Normalize return values/SystemExit payloads into process-style int codes."""

    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def run_pdf_toolkit_cli(argv: list[str]) -> tuple[int, str, str]:
    """
    Run pdf-toolkit CLI in-process with isolated argv and captured stdio.

    Returns: (exit_code, stdout_text, stderr_text)
    """

    original_argv = list(sys.argv)
    stdout_stream = io.StringIO()
    stderr_stream = io.StringIO()
    exit_code = 0

    try:
        sys.argv = ["pdf-toolkit", *argv]
        with redirect_stdout(stdout_stream), redirect_stderr(stderr_stream):
            cli_mod = importlib.import_module("pdf-toolkit.cli")
            try:
                result = cli_mod.main(argv)
            except SystemExit as exc:
                exit_code = _normalize_exit_code(exc.code)
            else:
                exit_code = _normalize_exit_code(result)
    finally:
        sys.argv = original_argv

    return exit_code, stdout_stream.getvalue(), stderr_stream.getvalue()


@contextmanager
def capture_output() -> tuple[io.StringIO, io.StringIO]:
    """Capture stdout/stderr for test assertions without leaking console noise."""

    stdout_stream = io.StringIO()
    stderr_stream = io.StringIO()
    with redirect_stdout(stdout_stream), redirect_stderr(stderr_stream):
        yield stdout_stream, stderr_stream
