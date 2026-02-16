"""
pdf-toolkit package.

Why this file exists:
- It marks this folder as a package so `python -m pdf-toolkit` works after install.
- It keeps import side effects minimal; the CLI lives in cli.py.
"""

__all__ = ["__version__"]

# Keep a simple version string for manifests and debugging.
__version__ = "0.1.2"
