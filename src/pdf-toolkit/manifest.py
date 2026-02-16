"""
Manifest recording and logging.

Why this exists:
- Every command writes a JSON manifest with inputs/outputs and a timeline.
- Logging goes through one place so messages are consistent and captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, TextIO

from .utils import ensure_dir


def _iso_now() -> str:
    """Return an ISO-8601 timestamp in UTC."""

    return datetime.now(timezone.utc).isoformat()


@dataclass
class ManifestRecorder:
    """
    Collect logs and actions, then write a single manifest JSON file.

    This keeps reporting consistent across render/split/rotate.
    """

    tool_name: str
    tool_version: str
    command: str
    options: Dict[str, Any]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    dry_run: bool
    verbosity: str = "normal"
    console_stream: TextIO = field(default_factory=lambda: sys.stderr)
    started_at: str = field(default_factory=_iso_now)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)

    def log(self, message: str, level: str = "info") -> None:
        """Record a log message and also print it to the console."""

        entry = {"timestamp": _iso_now(), "level": level, "message": message}
        self.logs.append(entry)

        should_print = False
        if self.verbosity == "quiet":
            should_print = level == "error"
        elif self.verbosity == "verbose":
            should_print = True
        else:
            should_print = level in {"info", "warning", "error"}

        if should_print:
            rendered = f"[{level}] {message}" if self.verbosity == "verbose" else message
            print(rendered, file=self.console_stream)

    def add_action(self, action: str, status: str, **details: Any) -> None:
        """
        Add an action record.

        Example action types: render_page, split_part, rotate_pdf_page, rotate_image.
        """

        entry: Dict[str, Any] = {
            "timestamp": _iso_now(),
            "action": action,
            "status": status,
        }
        entry.update(details)
        self.actions.append(entry)

    def _summarize_actions(self) -> Dict[str, int]:
        """Count actions by status (written, skipped, dry-run, etc.)."""

        counts: Dict[str, int] = {}
        for action in self.actions:
            status = action.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def build_manifest(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Assemble the final manifest structure."""

        return {
            "tool": self.tool_name,
            "version": self.tool_version,
            "command": self.command,
            "started_at": self.started_at,
            "ended_at": _iso_now(),
            "options": self.options,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "summary": summary,
            "action_counts": self._summarize_actions(),
            "actions": self.actions,
            "logs": self.logs,
        }

    def write_manifest(self, path: Path, summary: Dict[str, Any]) -> None:
        """
        Write the manifest JSON, unless this is a dry-run.

        We treat the manifest itself as output, so dry-run avoids writing it.
        """

        if self.dry_run:
            self.log(f"[dry-run] Would write manifest to {path}")
            return

        ensure_dir(path.parent, dry_run=False)
        manifest = self.build_manifest(summary)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=True)
