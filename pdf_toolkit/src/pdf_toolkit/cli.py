"""
Command-line interface for pdf_toolkit.

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
from .page_images import page_images_in_folder
from .render import render_pdf_to_pngs
from .rotate import rotate_images_in_folder, rotate_pdf_pages
from .split import split_pdf
from .utils import UserError, normalize_path


TOP_LEVEL_EXAMPLES = """Examples:
  python -m pdf_toolkit render --pdf "in.pdf" --out_dir "out\\pages" --dpi 300 --format png --prefix "book1"
  python -m pdf_toolkit split --pdf "in.pdf" --out_dir "out\\splits" --ranges "1-120,121-240" --prefix "book"
  python -m pdf_toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
  python -m pdf_toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages_rot"
  python -m pdf_toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --glob "*.png" --mode auto --debug
"""

RENDER_EXAMPLES = """Examples:
  python -m pdf_toolkit render --pdf "in.pdf" --out_dir "out\\pages" --dpi 300 --format png --prefix "book1"
  python -m pdf_toolkit render --pdf "in.pdf" --out_dir "out\\pages" --pages "1-10,15" --dry-run
"""

SPLIT_EXAMPLES = """Examples:
  python -m pdf_toolkit split --pdf "in.pdf" --out_dir "out\\splits" --ranges "1-120,121-240" --prefix "book"
  python -m pdf_toolkit split --pdf "in.pdf" --out_dir "out\\splits" --pages_per_file 120 --prefix "book"
"""

ROTATE_PDF_EXAMPLES = """Examples:
  python -m pdf_toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
  python -m pdf_toolkit rotate pdf --pdf "in.pdf" --out_pdf "in.pdf" --degrees 180 --pages "1-5" --inplace --overwrite
"""

ROTATE_IMAGES_EXAMPLES = """Examples:
  python -m pdf_toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages_rot"
  python -m pdf_toolkit rotate images --in_dir "out\\pages" --glob "*.png" --degrees 90 --out_dir "out\\pages" --inplace --overwrite
"""

PAGE_IMAGES_EXAMPLES = """Examples:
  python -m pdf_toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --glob "*.png" --mode auto --debug
  python -m pdf_toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --mode split --overwrite
  python -m pdf_toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages" --mode crop --inplace --overwrite
  python -m pdf_toolkit page-images --in_dir "out\\pages" --out_dir "out\\pages_single" --extract_page_numbers --page_num_debug
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf_toolkit",
        description="Local, lightweight PDF tools (render, split, rotate, page-images).",
        epilog=TOP_LEVEL_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)

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
    page_images.add_argument("--in_dir", required=True, help="Input folder of page images.")
    page_images.add_argument(
        "--out_dir",
        required=True,
        help="Output folder for processed images.",
    )
    page_images.add_argument(
        "--glob",
        default="*.png",
        help='Glob pattern for input files (default: "*.png").',
    )
    page_images.add_argument(
        "--mode",
        choices=["auto", "split", "crop"],
        default="auto",
        help="auto=split wides, split=always split, crop=never split.",
    )
    page_images.add_argument(
        "--split_ratio",
        type=float,
        default=1.25,
        help="Aspect ratio threshold for spread detection in auto mode.",
    )
    page_images.add_argument(
        "--gutter_search_frac",
        type=float,
        default=0.35,
        help="Fraction of width to search around center for gutter.",
    )
    page_images.add_argument(
        "--crop_threshold",
        type=int,
        default=180,
        help="Brightness threshold for page crop mask (0-255).",
    )
    page_images.add_argument(
        "--pad_px",
        type=int,
        default=20,
        help="Padding around detected crop box in pixels.",
    )
    page_images.add_argument(
        "--min_area_frac",
        type=float,
        default=0.25,
        help="Minimum crop area fraction before falling back to full image.",
    )
    page_images.add_argument(
        "--x_step",
        type=int,
        default=2,
        help="X stride for gutter search sampling.",
    )
    page_images.add_argument(
        "--y_step",
        type=int,
        default=4,
        help="Y stride for gutter search sampling.",
    )
    page_images.add_argument(
        "--debug",
        action="store_true",
        help="Write debug overlays to out_dir\\_debug\\.",
    )
    page_images.add_argument(
        "--extract_page_numbers",
        action="store_true",
        help="Attempt OCR of printed page numbers near top corners.",
    )
    page_images.add_argument(
        "--page_num_strip_frac",
        type=float,
        default=0.12,
        help="Top strip fraction searched for printed page numbers.",
    )
    page_images.add_argument(
        "--page_num_corner_w_frac",
        type=float,
        default=0.28,
        help="Corner crop width fraction for page-number OCR.",
    )
    page_images.add_argument(
        "--page_num_corner_h_frac",
        type=float,
        default=0.45,
        help="Corner crop height fraction within the top strip.",
    )
    page_images.add_argument(
        "--page_num_psm",
        type=int,
        default=7,
        help="Tesseract page segmentation mode for page-number OCR.",
    )
    page_images.add_argument(
        "--page_num_max",
        type=int,
        default=5000,
        help="Maximum accepted printed page number.",
    )
    page_images.add_argument(
        "--page_num_debug",
        action="store_true",
        help="Write page-number corner crops to out_dir\\_debug\\.",
    )
    page_images.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    page_images.add_argument(
        "--inplace",
        action="store_true",
        help="Allow out_dir to equal in_dir (requires --overwrite).",
    )
    page_images.add_argument("--dry-run", action="store_true", help="Show actions without writing files.")
    page_images.add_argument(
        "--manifest",
        help="Manifest path (default: out_dir\\manifest.json).",
    )

    return parser


def _command_string(argv: list[str]) -> str:
    """Reconstruct a command string for the manifest."""

    # list2cmdline produces a Windows-friendly command representation.
    return subprocess.list2cmdline(argv)


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
        command_string = _command_string(sys.argv)
        options = _options_for_manifest(args)

        if args.command == "render":
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
            in_dir = normalize_path(args.in_dir)
            out_dir = normalize_path(args.out_dir)
            manifest_path = (
                normalize_path(args.manifest) if args.manifest else out_dir / "manifest.json"
            )
            page_images_in_folder(
                in_dir=in_dir,
                out_dir=out_dir,
                pattern=args.glob,
                mode=args.mode,
                split_ratio=args.split_ratio,
                gutter_search_frac=args.gutter_search_frac,
                x_step=args.x_step,
                y_step=args.y_step,
                crop_threshold=args.crop_threshold,
                pad_px=args.pad_px,
                min_area_frac=args.min_area_frac,
                overwrite=args.overwrite,
                inplace=args.inplace,
                dry_run=args.dry_run,
                manifest_path=manifest_path,
                command_string=command_string,
                options=options,
                debug=args.debug,
                extract_page_numbers=args.extract_page_numbers,
                page_num_strip_frac=args.page_num_strip_frac,
                page_num_corner_w_frac=args.page_num_corner_w_frac,
                page_num_corner_h_frac=args.page_num_corner_h_frac,
                page_num_psm=args.page_num_psm,
                page_num_max=args.page_num_max,
                page_num_debug=args.page_num_debug,
            )
            return 0

        raise UserError("Unknown command. Use --help for usage.")
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
