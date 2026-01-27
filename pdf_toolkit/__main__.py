"""
Module entrypoint.

Why this file exists:
- Allows running the tool with `python -m pdf_toolkit`.
- Keeps the command-line interface in one place (cli.py).
"""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
