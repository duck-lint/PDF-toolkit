"""
CLI sanity checks for deterministic, quiet test behavior.
"""

from __future__ import annotations

import unittest

from helpers_cli import run_pdf_toolkit_cli


class CliSanityTests(unittest.TestCase):
    def test_help_is_clean_and_deterministic(self) -> None:
        exit_code, stdout_text, stderr_text = run_pdf_toolkit_cli(["--help"])
        self.assertEqual(exit_code, 0)
        self.assertIn("usage:", f"{stdout_text}{stderr_text}".lower())
        self.assertNotIn("not allowed with argument", stderr_text)


if __name__ == "__main__":
    unittest.main()
