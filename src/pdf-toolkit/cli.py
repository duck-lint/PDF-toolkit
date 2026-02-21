"""
Command-line interface for pdf-toolkit.

This file focuses on parsing arguments and dispatching to the real work.
Keeping this separate makes the code easier to read and test.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .config import (
    DEFAULT_PAGE_IMAGES,
    deep_merge,
    dump_default_page_images_yaml,
    load_yaml,
    validate_keys,
)
from .utils import UserError, normalize_path


TOP_LEVEL_EXAMPLES = """Examples:
  python -m pdf-toolkit render --pdf "in.pdf" --out_dir "out\\pages" --dpi 300 --format png --prefix "book1"
  python -m pdf-toolkit split --pdf "in.pdf" --out_dir "out\\splits" --ranges "1-120,121-240" --prefix "book"
  python -m pdf-toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
  python -m pdf-toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages_rot"
  python -m pdf-toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --glob "*.png" --mode auto --debug
"""

RENDER_EXAMPLES = """Examples:
  python -m pdf-toolkit render --pdf "in.pdf" --out_dir "out\\pages" --dpi 300 --format png --prefix "book1"
  python -m pdf-toolkit render --pdf "in.pdf" --out_dir "out\\pages" --pages "1-10,15" --dry-run
"""

SPLIT_EXAMPLES = """Examples:
  python -m pdf-toolkit split --pdf "in.pdf" --out_dir "out\\splits" --ranges "1-120,121-240" --prefix "book"
  python -m pdf-toolkit split --pdf "in.pdf" --out_dir "out\\splits" --pages_per_file 120 --prefix "book"
"""

ROTATE_PDF_EXAMPLES = """Examples:
  python -m pdf-toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
  python -m pdf-toolkit rotate pdf --pdf "in.pdf" --out_pdf "in.pdf" --degrees 180 --pages "1-5" --inplace --overwrite
"""

ROTATE_IMAGES_EXAMPLES = """Examples:
  python -m pdf-toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages_rot"
  python -m pdf-toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages" --inplace --overwrite
"""

PAGE_IMAGES_EXAMPLES = """Examples:
  python -m pdf-toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --glob "*.png" --mode auto --debug
  python -m pdf-toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --mode split --overwrite
  python -m pdf-toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages" --mode crop --inplace --overwrite
  python -m pdf-toolkit page-images --dump-default-config
  python -m pdf-toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --config "configs\\page_images.default.yaml"
"""

PAGE_IMAGES_TOP_LEVEL_KEYS = set(DEFAULT_PAGE_IMAGES.keys())


def _require_bool(value: Any, key: str) -> bool:
    """Require a strict boolean value from config/CLI merge output."""

    if isinstance(value, bool):
        return value
    raise UserError(f"{key} must be true or false.")


def _extract_page_images_section(loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Support either root config keys or a page_images wrapper."""

    if "page_images" in loaded:
        raw_section = loaded["page_images"]
        if not isinstance(raw_section, dict):
            raise UserError("config.page_images must be a mapping/object.")
        section = raw_section
        validate_keys(section, PAGE_IMAGES_TOP_LEVEL_KEYS, "config.page_images")
    else:
        section = loaded
        validate_keys(section, PAGE_IMAGES_TOP_LEVEL_KEYS, "config")

    return section


def _build_page_images_effective_config(
    args: argparse.Namespace,
) -> tuple[Dict[str, Any], Path | None]:
    """Resolve defaults < YAML config < explicit CLI flags."""

    effective = deep_merge(DEFAULT_PAGE_IMAGES, {})
    config_path: Path | None = None
    if hasattr(args, "config"):
        config_path = normalize_path(args.config)
        loaded = load_yaml(config_path)
        yaml_section = _extract_page_images_section(loaded)
        effective = deep_merge(effective, yaml_section)

    raw_args = vars(args)
    cli_top_overrides: Dict[str, Any] = {}
    for key in PAGE_IMAGES_TOP_LEVEL_KEYS:
        if key in raw_args:
            cli_top_overrides[key] = raw_args[key]

    effective = deep_merge(effective, cli_top_overrides)
    return effective, config_path


def _dump_default_page_images_config() -> None:
    """Print the wrapped default page-images config as YAML."""

    print(dump_default_page_images_yaml())


def _verbosity_from_args(args: argparse.Namespace) -> str:
    """Resolve global verbosity mode from top-level flags."""

    if getattr(args, "quiet", False):
        return "quiet"
    if getattr(args, "verbose", False):
        return "verbose"
    return "normal"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-toolkit",
        description="Local, lightweight PDF tools (render, split, rotate, page-images).",
        epilog=TOP_LEVEL_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error console logs.",
    )
    verbosity_group.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug-level console logs.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render",
        help="Render PDF pages to PNGs.",
        epilog=RENDER_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    render_parser.add_argument("--pdf", required=True, help="Input PDF path.")
    render_parser.add_argument("--out_dir", required=True, help="Output folder for PNGs.")
    render_parser.add_argument("--dpi", type=int, default=300, help="Render DPI (default: 300).")
    render_parser.add_argument(
        "--format",
        default="png",
        help="Image format (only 'png' is supported right now).",
    )
    render_parser.add_argument("--prefix", help="Filename prefix (default: PDF stem).")
    render_parser.add_argument(
        "--pages",
        default="all",
        help="Page selection: all, 1-3,5,7-9 (1-based).",
    )
    render_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    render_parser.add_argument("--dry-run", action="store_true", help="Show actions without writing files.")
    render_parser.add_argument(
        "--manifest",
        help="Manifest path (default: out_dir\\manifest.json).",
    )

    split_parser = subparsers.add_parser(
        "split",
        help="Split a PDF into multiple PDFs.",
        epilog=SPLIT_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    split_parser.add_argument("--pdf", required=True, help="Input PDF path.")
    split_parser.add_argument("--out_dir", required=True, help="Output folder for split PDFs.")
    split_parser.add_argument("--prefix", help="Filename prefix (default: PDF stem).")
    range_group = split_parser.add_mutually_exclusive_group(required=True)
    range_group.add_argument(
        "--ranges",
        help='Explicit ranges like "1-120,121-240".',
    )
    range_group.add_argument(
        "--pages_per_file",
        type=int,
        help="Automatic chunk size (e.g., 120).",
    )
    split_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    split_parser.add_argument("--dry-run", action="store_true", help="Show actions without writing files.")
    split_parser.add_argument(
        "--manifest",
        help="Manifest path (default: out_dir\\manifest.json).",
    )

    rotate_parser = subparsers.add_parser(
        "rotate",
        help="Rotate PDFs or PNGs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rotate_subparsers = rotate_parser.add_subparsers(dest="rotate_target", required=True)

    rotate_pdf = rotate_subparsers.add_parser(
        "pdf",
        help="Rotate pages inside a PDF.",
        epilog=ROTATE_PDF_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rotate_pdf.add_argument("--pdf", required=True, help="Input PDF path.")
    rotate_pdf.add_argument("--out_pdf", required=True, help="Output PDF path.")
    rotate_pdf.add_argument(
        "--degrees",
        type=int,
        required=True,
        help="Clockwise rotation degrees: 90, 180, 270.",
    )
    rotate_pdf.add_argument(
        "--pages",
        default="all",
        help="Page selection: all, 1-3,5,7-9 (1-based).",
    )
    rotate_pdf.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    rotate_pdf.add_argument(
        "--inplace",
        action="store_true",
        help="Allow out_pdf to equal input pdf.",
    )
    rotate_pdf.add_argument("--dry-run", action="store_true", help="Show actions without writing files.")
    rotate_pdf.add_argument(
        "--manifest",
        help="Manifest path (default: out_pdf folder\\manifest.json).",
    )

    rotate_images = rotate_subparsers.add_parser(
        "images",
        help="Rotate PNGs in a folder.",
        epilog=ROTATE_IMAGES_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rotate_images.add_argument("--in_dir", required=True, help="Input folder of PNGs.")
    rotate_images.add_argument("--out_dir", required=True, help="Output folder for rotated PNGs.")
    rotate_images.add_argument(
        "--glob",
        default="*.png",
        help='Glob pattern for input files (default: "*.png").',
    )
    rotate_images.add_argument(
        "--degrees",
        type=int,
        required=True,
        help="Clockwise rotation degrees: 90, 180, 270.",
    )
    rotate_images.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    rotate_images.add_argument(
        "--inplace",
        action="store_true",
        help="Allow out_dir to equal in_dir (requires --overwrite).",
    )
    rotate_images.add_argument("--dry-run", action="store_true", help="Show actions without writing files.")
    rotate_images.add_argument(
        "--manifest",
        help="Manifest path (default: out_dir\\manifest.json).",
    )

    page_images = subparsers.add_parser(
        "page-images",
        help="Split spread scans and crop page bounds in image folders.",
        epilog=PAGE_IMAGES_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    page_images.add_argument(
        "--in_dir",
        default=argparse.SUPPRESS,
        help="Input folder of page images (required unless --dump-default-config).",
    )
    page_images.add_argument(
        "--out_dir",
        default=argparse.SUPPRESS,
        help="Output folder for processed images (required unless --dump-default-config).",
    )
    page_images.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="Optional YAML config for page-images settings.",
    )
    page_images.add_argument(
        "--dump-default-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print default page-images YAML config and exit.",
    )
    page_images.add_argument(
        "--glob",
        default=argparse.SUPPRESS,
        help='Glob pattern for input files (default: "*.png").',
    )
    page_images.add_argument(
        "--mode",
        choices=["auto", "split", "crop"],
        default=argparse.SUPPRESS,
        help="auto=split wides, split=always split, crop=never split.",
    )
    page_images.add_argument(
        "--split_ratio",
        type=float,
        default=argparse.SUPPRESS,
        help="Aspect ratio threshold for spread detection in auto mode.",
    )
    page_images.add_argument(
        "--gutter_search_frac",
        type=float,
        default=argparse.SUPPRESS,
        help="Fraction of width to search around center for gutter.",
    )
    page_images.add_argument(
        "--gutter_trim_px",
        type=int,
        default=argparse.SUPPRESS,
        help="Trim pixels on each side of detected gutter when splitting.",
    )
    page_images.add_argument(
        "--crop_threshold",
        type=int,
        default=argparse.SUPPRESS,
        help="Brightness threshold for page crop mask (0-255).",
    )
    page_images.add_argument(
        "--pad_px",
        type=int,
        default=argparse.SUPPRESS,
        help="Padding around detected crop box in pixels.",
    )
    page_images.add_argument(
        "--edge_inset_px",
        type=int,
        default=argparse.SUPPRESS,
        help="Inset final crop box inward after padding (pixels).",
    )
    page_images.add_argument(
        "--min_area_frac",
        type=float,
        default=argparse.SUPPRESS,
        help="Minimum crop area fraction before falling back to full image.",
    )
    page_images.add_argument(
        "--x_step",
        type=int,
        default=argparse.SUPPRESS,
        help="X stride for gutter search sampling.",
    )
    page_images.add_argument(
        "--y_step",
        type=int,
        default=argparse.SUPPRESS,
        help="Y stride for gutter search sampling.",
    )
    page_images.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Write debug overlays to out_dir\\_debug\\.",
    )
    page_images.add_argument(
        "--overwrite",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Overwrite existing files.",
    )
    page_images.add_argument(
        "--inplace",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow out_dir to equal in_dir (requires --overwrite).",
    )
    page_images.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show actions without writing files.",
    )
    page_images.add_argument(
        "--manifest",
        default=argparse.SUPPRESS,
        help="Manifest path (default: out_dir\\manifest.json).",
    )

    return parser


def _command_string(argv: list[str]) -> str:
    """Reconstruct a command string for the manifest."""

    # list2cmdline produces a Windows-friendly command representation.
    return subprocess.list2cmdline(argv)


def _command_argv_for_manifest(argv: list[str] | None) -> list[str]:
    """Choose argv used to record manifest command faithfully."""

    if argv is None:
        return list(sys.argv)
    return [sys.argv[0], *argv]


def _options_for_manifest(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Build a JSON-friendly options dict.

    Why: argparse Namespace can contain non-serializable objects.
    """

    raw = vars(args)
    options: Dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, Path):
            options[key] = str(value)
        else:
            options[key] = value
    options["version"] = __version__
    return options


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        command_string = _command_string(_command_argv_for_manifest(argv))
        options = _options_for_manifest(args)
        verbosity = _verbosity_from_args(args)
        options["verbosity"] = verbosity

        if args.command == "render":
            from .render import render_pdf_to_pngs

            pdf_path = normalize_path(args.pdf)
            out_dir = normalize_path(args.out_dir)
            manifest_path = (
                normalize_path(args.manifest) if args.manifest else out_dir / "manifest.json"
            )
            prefix = args.prefix or pdf_path.stem
            render_pdf_to_pngs(
                pdf_path=pdf_path,
                out_dir=out_dir,
                dpi=args.dpi,
                pages_spec=args.pages,
                prefix=prefix,
                image_format=args.format,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=options,
            )
            return 0

        if args.command == "split":
            from .split import split_pdf

            pdf_path = normalize_path(args.pdf)
            out_dir = normalize_path(args.out_dir)
            manifest_path = (
                normalize_path(args.manifest) if args.manifest else out_dir / "manifest.json"
            )
            prefix = args.prefix or pdf_path.stem
            split_pdf(
                pdf_path=pdf_path,
                out_dir=out_dir,
                prefix=prefix,
                ranges_spec=args.ranges,
                pages_per_file=args.pages_per_file,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=options,
            )
            return 0

        if args.command == "rotate" and args.rotate_target == "pdf":
            from .rotate import rotate_pdf_pages

            pdf_path = normalize_path(args.pdf)
            out_pdf = normalize_path(args.out_pdf)
            manifest_path = (
                normalize_path(args.manifest)
                if args.manifest
                else out_pdf.parent / "manifest.json"
            )
            rotate_pdf_pages(
                pdf_path=pdf_path,
                out_pdf=out_pdf,
                degrees=args.degrees,
                pages_spec=args.pages,
                overwrite=args.overwrite,
                inplace=args.inplace,
                dry_run=args.dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=options,
            )
            return 0

        if args.command == "rotate" and args.rotate_target == "images":
            from .rotate import rotate_images_in_folder

            in_dir = normalize_path(args.in_dir)
            out_dir = normalize_path(args.out_dir)
            manifest_path = (
                normalize_path(args.manifest) if args.manifest else out_dir / "manifest.json"
            )
            rotate_images_in_folder(
                in_dir=in_dir,
                out_dir=out_dir,
                pattern=args.glob,
                degrees=args.degrees,
                overwrite=args.overwrite,
                inplace=args.inplace,
                dry_run=args.dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=options,
            )
            return 0

        if args.command == "page-images":
            if getattr(args, "dump_default_config", False):
                _dump_default_page_images_config()
                return 0

            if not hasattr(args, "in_dir") or not hasattr(args, "out_dir"):
                raise UserError(
                    "page-images requires --in_dir and --out_dir unless "
                    "--dump-default-config is used."
                )

            effective_cfg, config_path = _build_page_images_effective_config(args)
            in_dir = normalize_path(args.in_dir)
            out_dir = normalize_path(args.out_dir)
            manifest_value = effective_cfg.get("manifest")
            manifest_path = (
                normalize_path(str(manifest_value))
                if manifest_value
                else out_dir / "manifest.json"
            )

            page_options = deep_merge(effective_cfg, {})
            page_options["version"] = __version__
            page_options["verbosity"] = verbosity
            if config_path is not None:
                page_options["config_path"] = str(config_path)

            overwrite = _require_bool(effective_cfg["overwrite"], "config.overwrite")
            inplace = _require_bool(effective_cfg["inplace"], "config.inplace")
            dry_run = _require_bool(effective_cfg["dry_run"], "config.dry_run")
            debug = _require_bool(effective_cfg["debug"], "config.debug")

            from .page_images import page_images_in_folder

            page_images_in_folder(
                in_dir=in_dir,
                out_dir=out_dir,
                pattern=str(effective_cfg["glob"]),
                mode=str(effective_cfg["mode"]),
                split_ratio=float(effective_cfg["split_ratio"]),
                gutter_search_frac=float(effective_cfg["gutter_search_frac"]),
                gutter_trim_px=int(effective_cfg["gutter_trim_px"]),
                x_step=int(effective_cfg["x_step"]),
                y_step=int(effective_cfg["y_step"]),
                crop_threshold=int(effective_cfg["crop_threshold"]),
                pad_px=int(effective_cfg["pad_px"]),
                edge_inset_px=int(effective_cfg["edge_inset_px"]),
                min_area_frac=float(effective_cfg["min_area_frac"]),
                overwrite=overwrite,
                inplace=inplace,
                dry_run=dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=page_options,
                debug=debug,
            )
            return 0

        raise UserError("Unknown command. Use --help for usage.")
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
