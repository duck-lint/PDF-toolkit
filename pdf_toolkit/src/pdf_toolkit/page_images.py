"""
Split spread scans into single-page images and crop page bounds.

Why this module exists:
- Spread scans often have dark backgrounds with bright page regions.
- We can detect a dark center gutter, split into left/right pages, then crop
  each page to the bright region using simple Pillow-only heuristics.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

from .manifest import ManifestRecorder
from .utils import UserError, ensure_dir, ensure_dir_path


BBox = Tuple[int, int, int, int]
PAGE_NUMBER_REGEX = re.compile(r"\d{1,4}")
ROMAN_CANONICAL_REGEX = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
)
VALID_PAGE_NUM_ANCHORS = {"top", "bottom"}
VALID_PAGE_NUM_POSITIONS = {"left", "center", "right"}
VALID_PAGE_NUM_PARSERS = {"auto", "arabic", "roman"}


def which_tesseract() -> Optional[str]:
    """Return the tesseract executable path if available in PATH."""

    return shutil.which("tesseract")


def ocr_text_tesseract(
    image: Image.Image,
    psm: int,
    tesseract_exe: str | Path,
    whitelist: str,
) -> str:
    """Run tesseract CLI with a custom character whitelist and return stdout text."""

    ocr_text_tesseract.last_error = None  # type: ignore[attr-defined]
    tmp_path: Optional[Path] = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            tmp_path = Path(handle.name)
            image.save(handle, format="PNG")

        cmd = [
            str(tesseract_exe),
            str(tmp_path),
            "stdout",
            "--psm",
            str(psm),
            "-l",
            "eng",
            "-c",
            f"tessedit_char_whitelist={whitelist}",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            details = stderr or stdout or f"exit code {result.returncode}"
            ocr_text_tesseract.last_error = f"tesseract failed: {details}"  # type: ignore[attr-defined]
            return ""
        return result.stdout.strip()
    except Exception as exc:  # pragma: no cover - subprocess OS errors
        ocr_text_tesseract.last_error = f"tesseract invocation error: {exc}"  # type: ignore[attr-defined]
        return ""
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


ocr_text_tesseract.last_error = None  # type: ignore[attr-defined]


def _prepare_for_ocr(
    crop_image: Image.Image,
    prep_scale: int,
    bin_threshold: int,
    invert: bool,
) -> Image.Image:
    """Normalize a crop for digits-only OCR using configurable Pillow operations."""

    gray = crop_image.convert("L")
    gray = ImageOps.autocontrast(gray)
    scale = max(1, int(prep_scale))
    if scale > 1:
        gray = gray.resize(
            (max(1, gray.width * scale), max(1, gray.height * scale)),
            Image.Resampling.LANCZOS,
        )
    if invert:
        gray = ImageOps.invert(gray)
    threshold = int(bin_threshold)
    return gray.point(lambda value: 255 if value >= threshold else 0)


def build_page_num_regions(width: int, height: int, cfg: Dict[str, Any]) -> List[Tuple[str, BBox]]:
    """
    Build named OCR regions from anchor/position configuration.

    Region names follow: <anchor>_<position>, such as top_left or bottom_center.
    """

    anchors = list(cfg["anchors"])
    positions = list(cfg.get("allow_positions", cfg["positions"]))

    strip_h = max(1, int(height * float(cfg["strip_frac"])))
    region_h = max(1, int(strip_h * float(cfg["corner_h_frac"])))
    region_h = min(height, region_h)
    strip_y_offset_px = max(0, int(cfg.get("strip_y_offset_px", 0)))

    corner_w = max(1, int(width * float(cfg["corner_w_frac"])))
    corner_w = min(width, corner_w)
    center_w = max(1, int(width * float(cfg["center_w_frac"])))
    center_w = min(width, center_w)

    regions: List[Tuple[str, BBox]] = []
    for anchor in anchors:
        if anchor == "top":
            strip_y0 = min(max(0, strip_y_offset_px), max(0, height - 1))
        else:
            strip_y0 = max(0, height - strip_h)
        y0 = strip_y0
        y1 = min(height, y0 + region_h)
        if y1 <= y0:
            y1 = min(height, y0 + 1)

        for position in positions:
            if position == "left":
                x0, x1 = 0, corner_w
            elif position == "right":
                x0, x1 = max(0, width - corner_w), width
            else:
                x0 = max(0, (width - center_w) // 2)
                x1 = min(width, x0 + center_w)
            regions.append((f"{anchor}_{position}", (x0, y0, x1, y1)))
    return regions


def _extract_candidate(raw_text: str) -> Optional[int]:
    """Extract the first 1-4 digit token from OCR output."""

    match = PAGE_NUMBER_REGEX.search(raw_text)
    if match is None:
        return None
    return int(match.group(0))


def _extract_roman_letters(raw_text: str, whitelist: str) -> str:
    """Keep only roman numeral letters present in whitelist, preserving case."""

    allowed = set(whitelist)
    return "".join(char for char in raw_text if char in allowed)


def parse_roman_numeral(value: str) -> Optional[int]:
    """Parse a strict Roman numeral (canonical subtractive notation)."""

    roman = value.strip().upper()
    if not roman:
        return None
    if not ROMAN_CANONICAL_REGEX.fullmatch(roman):
        return None

    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    idx = 0
    while idx < len(roman):
        current = table[roman[idx]]
        if idx + 1 < len(roman):
            nxt = table[roman[idx + 1]]
            if current < nxt:
                total += nxt - current
                idx += 2
                continue
        total += current
        idx += 1
    return total


def _tighten_to_dark_bbox(crop: Image.Image, dark_bbox_cfg: Dict[str, Any]) -> Image.Image:
    """Optionally tighten OCR region to dark-pixel bbox to avoid header noise."""

    if not dark_bbox_cfg.get("enabled", False):
        return crop

    gray = crop.convert("L")
    threshold = int(dark_bbox_cfg["threshold"])
    mask = gray.point(lambda value: 255 if value <= threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return crop

    left, top, right, bottom = bbox
    bbox_area = max(0, right - left) * max(0, bottom - top)
    crop_area = max(1, crop.width * crop.height)
    min_area_frac = float(dark_bbox_cfg["min_area_frac"])
    if bbox_area < int(min_area_frac * crop_area):
        return crop

    pad_px = int(dark_bbox_cfg["pad_px"])
    left = max(0, left - pad_px)
    top = max(0, top - pad_px)
    right = min(crop.width, right + pad_px)
    bottom = min(crop.height, bottom + pad_px)
    if right <= left or bottom <= top:
        return crop
    return crop.crop((left, top, right, bottom))


def extract_printed_page_number(
    page_img: Image.Image,
    cfg: Dict[str, Any],
    tesseract_exe: str | Path | None = None,
) -> Dict[str, Any]:
    """
    Attempt to extract printed page number from configurable anchor/position regions.

    Returns fields for manifest recording:
    - printed_page: int | None
    - region_used: str | None
    - psm_used: int | None
    - raw_by_region: dict[str, str]
    - legacy compatibility fields (corner, corner_used, raw_left, raw_right)
    - reason: nullable failure reason
    """

    result: Dict[str, Any] = {
        "printed_page": None,
        "printed_page_text": None,
        "printed_page_kind": None,
        "region_used": None,
        "psm_used": None,
        "raw_by_region": {},
        "corner": None,
        "corner_used": None,
        "raw_left": "",
        "raw_right": "",
        "reason": None,
    }

    regions = build_page_num_regions(page_img.width, page_img.height, cfg)
    debug_dir = cfg.get("debug_dir")
    debug_base = cfg.get("debug_base")
    write_debug = bool(cfg.get("debug_crops"))
    if write_debug and isinstance(debug_dir, Path) and isinstance(debug_base, str):
        for region_name, bbox in regions:
            page_img.crop(bbox).save(debug_dir / f"{debug_base}__{region_name}.png")

    resolved_tesseract = tesseract_exe if tesseract_exe is not None else which_tesseract()
    if not resolved_tesseract:
        result["reason"] = "no_tesseract"
        return result

    psm_candidates = [int(value) for value in cfg["psm_candidates"]]
    max_page = int(cfg["max_page"])
    parser_mode = str(cfg.get("parser", "auto")).lower()
    roman_whitelist = str(cfg.get("roman_whitelist", "IVXLCDMivxlcdm"))
    prep_scale = int(cfg["prep_scale"])
    bin_threshold = int(cfg["bin_threshold"])
    invert = bool(cfg["invert"])
    dark_bbox_cfg = dict(cfg.get("dark_bbox", {}))

    saw_out_of_range = False
    error_messages: List[str] = []

    for region_name, bbox in regions:
        crop = _tighten_to_dark_bbox(page_img.crop(bbox), dark_bbox_cfg)
        last_raw = ""
        for psm in psm_candidates:
            prepped = _prepare_for_ocr(crop, prep_scale, bin_threshold, invert)

            if parser_mode in {"auto", "arabic"}:
                arabic_raw = ocr_text_tesseract(
                    prepped,
                    psm,
                    resolved_tesseract,
                    "0123456789",
                )
                last_raw = arabic_raw
                run_error = getattr(ocr_text_tesseract, "last_error", None)
                if run_error:
                    error_messages.append(str(run_error))

                arabic_candidate = _extract_candidate(arabic_raw)
                if arabic_candidate is not None:
                    if 1 <= arabic_candidate <= max_page:
                        corner: Optional[str] = None
                        if region_name.endswith("_left"):
                            corner = "left"
                        elif region_name.endswith("_right"):
                            corner = "right"

                        result["printed_page"] = arabic_candidate
                        result["printed_page_text"] = str(arabic_candidate)
                        result["printed_page_kind"] = "arabic"
                        result["region_used"] = region_name
                        result["psm_used"] = psm
                        result["corner"] = corner
                        result["corner_used"] = corner
                        result["raw_by_region"][region_name] = arabic_raw
                        result["raw_left"] = str(result["raw_by_region"].get("top_left", ""))
                        result["raw_right"] = str(result["raw_by_region"].get("top_right", ""))
                        return result
                    saw_out_of_range = True

            if parser_mode in {"auto", "roman"}:
                roman_raw = ocr_text_tesseract(
                    prepped,
                    psm,
                    resolved_tesseract,
                    roman_whitelist,
                )
                last_raw = roman_raw
                run_error = getattr(ocr_text_tesseract, "last_error", None)
                if run_error:
                    error_messages.append(str(run_error))

                roman_text = _extract_roman_letters(roman_raw, roman_whitelist)
                roman_candidate = parse_roman_numeral(roman_text)
                if roman_candidate is not None:
                    if 1 <= roman_candidate <= max_page:
                        corner = None
                        if region_name.endswith("_left"):
                            corner = "left"
                        elif region_name.endswith("_right"):
                            corner = "right"

                        result["printed_page"] = roman_candidate
                        result["printed_page_text"] = roman_text
                        result["printed_page_kind"] = "roman"
                        result["region_used"] = region_name
                        result["psm_used"] = psm
                        result["corner"] = corner
                        result["corner_used"] = corner
                        result["raw_by_region"][region_name] = roman_raw
                        result["raw_left"] = str(result["raw_by_region"].get("top_left", ""))
                        result["raw_right"] = str(result["raw_by_region"].get("top_right", ""))
                        return result
                    saw_out_of_range = True

        result["raw_by_region"][region_name] = last_raw

    result["raw_left"] = str(result["raw_by_region"].get("top_left", ""))
    result["raw_right"] = str(result["raw_by_region"].get("top_right", ""))

    if saw_out_of_range:
        result["reason"] = "out_of_range"
    elif error_messages:
        result["reason"] = "tesseract_failed"
        result["tesseract_error"] = "; ".join(error_messages)
    else:
        result["reason"] = "no_digits"
    return result


def _collect_image_files(in_dir: Path, pattern: str) -> List[Path]:
    """Return matching files in stable order."""

    return sorted(path for path in in_dir.glob(pattern) if path.is_file())


def _validate_options(
    mode: str,
    split_ratio: float,
    gutter_search_frac: float,
    x_step: int,
    y_step: int,
    crop_threshold: int,
    pad_px: int,
    min_area_frac: float,
    page_num_enabled: bool,
    page_num_anchors: List[str],
    page_num_positions: List[str],
    page_num_parser: str,
    page_num_roman_whitelist: str,
    page_num_strip_frac: float,
    page_num_strip_y_offset_px: int,
    page_num_corner_w_frac: float,
    page_num_corner_h_frac: float,
    page_num_center_w_frac: float,
    page_num_psm_candidates: List[int],
    page_num_max: int,
    page_num_prep_scale: int,
    page_num_bin_threshold: int,
    page_num_invert: bool,
    page_num_dark_bbox: Dict[str, Any],
    page_num_debug_crops: bool,
) -> None:
    """Validate user-facing options and raise clear errors."""

    if mode not in {"auto", "split", "crop"}:
        raise UserError("Mode must be one of: auto, split, crop.")
    if split_ratio <= 0:
        raise UserError("--split_ratio must be > 0.")
    if gutter_search_frac <= 0 or gutter_search_frac > 1:
        raise UserError("--gutter_search_frac must be in the range (0, 1].")
    if x_step <= 0:
        raise UserError("--x_step must be a positive integer.")
    if y_step <= 0:
        raise UserError("--y_step must be a positive integer.")
    if crop_threshold < 0 or crop_threshold > 255:
        raise UserError("--crop_threshold must be in the range [0, 255].")
    if pad_px < 0:
        raise UserError("--pad_px must be >= 0.")
    if min_area_frac <= 0 or min_area_frac > 1:
        raise UserError("--min_area_frac must be in the range (0, 1].")
    if not isinstance(page_num_enabled, bool):
        raise UserError("page_numbers.enabled must be true or false.")
    if not page_num_anchors:
        raise UserError("page_numbers.anchors must be a non-empty list.")
    invalid_anchors = [anchor for anchor in page_num_anchors if anchor not in VALID_PAGE_NUM_ANCHORS]
    if invalid_anchors:
        raise UserError(
            f"page_numbers.anchors contains invalid value(s): {invalid_anchors}. "
            f"Allowed: {sorted(VALID_PAGE_NUM_ANCHORS)}."
        )
    if not page_num_positions:
        raise UserError("page_numbers.positions must be a non-empty list.")
    invalid_positions = [
        position for position in page_num_positions if position not in VALID_PAGE_NUM_POSITIONS
    ]
    if invalid_positions:
        raise UserError(
            f"page_numbers.positions contains invalid value(s): {invalid_positions}. "
            f"Allowed: {sorted(VALID_PAGE_NUM_POSITIONS)}."
        )
    if page_num_parser not in VALID_PAGE_NUM_PARSERS:
        raise UserError(
            f"page_numbers.parser must be one of: {sorted(VALID_PAGE_NUM_PARSERS)}."
        )
    if not page_num_roman_whitelist:
        raise UserError("page_numbers.roman_whitelist must not be empty.")
    if page_num_strip_frac <= 0 or page_num_strip_frac > 1:
        raise UserError("--page_num_strip_frac must be in the range (0, 1].")
    if page_num_strip_y_offset_px < 0:
        raise UserError("page_numbers.strip_y_offset_px must be >= 0.")
    if page_num_corner_w_frac <= 0 or page_num_corner_w_frac > 1:
        raise UserError("--page_num_corner_w_frac must be in the range (0, 1].")
    if page_num_corner_h_frac <= 0 or page_num_corner_h_frac > 1:
        raise UserError("--page_num_corner_h_frac must be in the range (0, 1].")
    if page_num_center_w_frac <= 0 or page_num_center_w_frac > 1:
        raise UserError("page_numbers.center_w_frac must be in the range (0, 1].")
    if not page_num_psm_candidates:
        raise UserError("page_numbers.psm_candidates must be a non-empty list.")
    if any(int(psm) <= 0 for psm in page_num_psm_candidates):
        raise UserError("page_numbers.psm_candidates values must all be > 0.")
    if page_num_max <= 0:
        raise UserError("--page_num_max must be > 0.")
    if page_num_prep_scale <= 0:
        raise UserError("page_numbers.prep_scale must be > 0.")
    if page_num_bin_threshold < 0 or page_num_bin_threshold > 255:
        raise UserError("page_numbers.bin_threshold must be in the range [0, 255].")
    if not isinstance(page_num_invert, bool):
        raise UserError("page_numbers.invert must be true or false.")
    if not isinstance(page_num_dark_bbox, dict):
        raise UserError("page_numbers.dark_bbox must be a mapping/object.")
    if not isinstance(page_num_dark_bbox.get("enabled", False), bool):
        raise UserError("page_numbers.dark_bbox.enabled must be true or false.")
    dark_threshold = int(page_num_dark_bbox.get("threshold", 170))
    if dark_threshold < 0 or dark_threshold > 255:
        raise UserError("page_numbers.dark_bbox.threshold must be in the range [0, 255].")
    dark_pad = int(page_num_dark_bbox.get("pad_px", 0))
    if dark_pad < 0:
        raise UserError("page_numbers.dark_bbox.pad_px must be >= 0.")
    dark_min_area_frac = float(page_num_dark_bbox.get("min_area_frac", 0.0))
    if dark_min_area_frac <= 0 or dark_min_area_frac > 1:
        raise UserError(
            "page_numbers.dark_bbox.min_area_frac must be in the range (0, 1]."
        )
    if not isinstance(page_num_debug_crops, bool):
        raise UserError("page_numbers.debug_crops must be true or false.")


def detect_spread(width: int, height: int, split_ratio: float) -> bool:
    """Detect whether an image likely contains two facing pages."""

    if height <= 0:
        return False
    return (width / height) >= split_ratio


def detect_gutter_x(
    gray: Image.Image,
    gutter_search_frac: float,
    x_step: int,
    y_step: int,
) -> Tuple[int, bool]:
    """
    Find the likely gutter by searching for the darkest center column.

    Scan heuristics are intentionally simple:
    - pages are often brighter than the background
    - the gutter is often a darker vertical band near the center
    """

    if gray.mode != "L":
        gray = gray.convert("L")

    width, height = gray.size
    center_x = width // 2
    half_window = max(1, int((gutter_search_frac * width) / 2))
    start_x = max(0, center_x - half_window)
    end_x = min(width - 1, center_x + half_window)

    pixels = gray.load()
    best_x = center_x
    best_score: Optional[int] = None

    for x in range(start_x, end_x + 1, x_step):
        score = 0
        for y in range(0, height, y_step):
            score += int(pixels[x, y])
        if best_score is None or score < best_score:
            best_score = score
            best_x = x

    fallback_to_center = False
    min_x = int(0.2 * width)
    max_x = int(0.8 * width)
    if not (min_x < best_x < max_x):
        best_x = center_x
        fallback_to_center = True

    if width >= 2:
        best_x = max(1, min(width - 1, best_x))
    else:
        best_x = 0
    return best_x, fallback_to_center


def split_spread_image(image: Image.Image, gutter_x: int) -> Tuple[Image.Image, Image.Image]:
    """Split a spread image into left and right halves at gutter_x."""

    width, height = image.size
    if width < 2:
        raise UserError("Image is too narrow to split into two pages.")
    safe_gutter_x = max(1, min(width - 1, gutter_x))
    left = image.crop((0, 0, safe_gutter_x, height))
    right = image.crop((safe_gutter_x, 0, width, height))
    return left, right


def find_crop_bbox(
    image: Image.Image,
    crop_threshold: int,
    pad_px: int,
    min_area_frac: float,
) -> Tuple[BBox, bool, Optional[str]]:
    """Find a bright-region page bbox, with safe fallback to full image."""

    width, height = image.size
    full_bbox: BBox = (0, 0, width, height)

    gray = image.convert("L")
    mask = gray.point(lambda p: 255 if p >= crop_threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return full_bbox, True, "No bright page region found; used full image."

    left, top, right, bottom = bbox
    bbox_area = (right - left) * (bottom - top)
    image_area = width * height
    if bbox_area < int(min_area_frac * image_area):
        return full_bbox, True, "Detected page area too small; used full image."

    left = max(0, left - pad_px)
    top = max(0, top - pad_px)
    right = min(width, right + pad_px)
    bottom = min(height, bottom + pad_px)

    if right <= left or bottom <= top:
        return full_bbox, True, "Invalid crop bounds after padding; used full image."

    return (left, top, right, bottom), False, None


def _crop_page_image(
    image: Image.Image,
    crop_threshold: int,
    pad_px: int,
    min_area_frac: float,
) -> Tuple[Image.Image, BBox, List[str]]:
    """Crop a page image and return cropped image + bbox + notes."""

    bbox, used_fallback, note = find_crop_bbox(
        image=image,
        crop_threshold=crop_threshold,
        pad_px=pad_px,
        min_area_frac=min_area_frac,
    )
    cropped = image.crop(bbox)
    cropped.load()

    notes: List[str] = []
    if used_fallback and note:
        notes.append(note)
    return cropped, bbox, notes


def _draw_debug_overlay(
    source_image: Image.Image,
    mode_used: str,
    gutter_x: Optional[int],
    left_bbox: Optional[BBox],
    right_bbox: Optional[BBox],
    crop_bbox: Optional[BBox],
) -> Image.Image:
    """Create a single debug image that visualizes split/crop decisions."""

    debug_image = source_image.convert("RGB")
    draw = ImageDraw.Draw(debug_image)

    if gutter_x is not None:
        draw.line(
            [(gutter_x, 0), (gutter_x, max(0, source_image.height - 1))],
            fill=(255, 0, 0),
            width=2,
        )

    if mode_used == "split":
        if left_bbox is not None:
            draw.rectangle(left_bbox, outline=(0, 255, 0), width=2)
        if right_bbox is not None and gutter_x is not None:
            shifted = (
                right_bbox[0] + gutter_x,
                right_bbox[1],
                right_bbox[2] + gutter_x,
                right_bbox[3],
            )
            draw.rectangle(shifted, outline=(0, 255, 0), width=2)
    elif crop_bbox is not None:
        draw.rectangle(crop_bbox, outline=(0, 255, 0), width=2)

    return debug_image


def page_images_in_folder(
    in_dir: Path,
    out_dir: Path,
    pattern: str,
    mode: str,
    split_ratio: float,
    gutter_search_frac: float,
    x_step: int,
    y_step: int,
    crop_threshold: int,
    pad_px: int,
    min_area_frac: float,
    overwrite: bool,
    inplace: bool,
    dry_run: bool,
    manifest_path: Path,
    command_string: str,
    options: Dict[str, object],
    debug: bool,
    extract_page_numbers: bool = False,
    page_num_anchors: Optional[List[str]] = None,
    page_num_positions: Optional[List[str]] = None,
    page_num_parser: str = "auto",
    page_num_roman_whitelist: str = "IVXLCDMivxlcdm",
    page_num_strip_frac: float = 0.12,
    page_num_strip_y_offset_px: int = 0,
    page_num_corner_w_frac: float = 0.28,
    page_num_corner_h_frac: float = 0.45,
    page_num_center_w_frac: float = 0.20,
    page_num_psm_candidates: Optional[List[int]] = None,
    page_num_psm: int = 7,
    page_num_max: int = 5000,
    page_num_prep_scale: int = 2,
    page_num_bin_threshold: int = 160,
    page_num_invert: bool = False,
    page_num_dark_bbox: Optional[Dict[str, Any]] = None,
    page_num_debug_crops: bool = False,
    page_num_debug: bool = False,
) -> None:
    """
    Process page images by optional spread split + page crop.

    Modes:
    - auto: split only when aspect ratio indicates a spread
    - split: always split then crop each half
    - crop: never split, only crop
    """

    if not in_dir.exists() or not in_dir.is_dir():
        raise UserError(f"Input directory not found: {in_dir}")
    ensure_dir_path(out_dir, "Output directory")
    resolved_anchors = list(page_num_anchors) if page_num_anchors is not None else ["top"]
    resolved_positions = (
        list(page_num_positions) if page_num_positions is not None else ["right", "left"]
    )
    resolved_psm_candidates = (
        list(page_num_psm_candidates)
        if page_num_psm_candidates is not None
        else [int(page_num_psm)]
    )
    resolved_parser = str(page_num_parser).lower()
    resolved_dark_bbox = (
        dict(page_num_dark_bbox)
        if page_num_dark_bbox is not None
        else {"enabled": False, "threshold": 170, "pad_px": 2, "min_area_frac": 0.005}
    )
    use_page_num_debug_crops = bool(page_num_debug_crops or page_num_debug)

    _validate_options(
        mode=mode,
        split_ratio=split_ratio,
        gutter_search_frac=gutter_search_frac,
        x_step=x_step,
        y_step=y_step,
        crop_threshold=crop_threshold,
        pad_px=pad_px,
        min_area_frac=min_area_frac,
        page_num_enabled=extract_page_numbers,
        page_num_anchors=resolved_anchors,
        page_num_positions=resolved_positions,
        page_num_parser=resolved_parser,
        page_num_roman_whitelist=page_num_roman_whitelist,
        page_num_strip_frac=page_num_strip_frac,
        page_num_strip_y_offset_px=page_num_strip_y_offset_px,
        page_num_corner_w_frac=page_num_corner_w_frac,
        page_num_corner_h_frac=page_num_corner_h_frac,
        page_num_center_w_frac=page_num_center_w_frac,
        page_num_psm_candidates=resolved_psm_candidates,
        page_num_max=page_num_max,
        page_num_prep_scale=page_num_prep_scale,
        page_num_bin_threshold=page_num_bin_threshold,
        page_num_invert=page_num_invert,
        page_num_dark_bbox=resolved_dark_bbox,
        page_num_debug_crops=use_page_num_debug_crops,
    )

    if out_dir.resolve() == in_dir.resolve():
        if not inplace:
            raise UserError(
                "Output directory is the same as input. Use --inplace to allow this."
            )
        if not overwrite:
            raise UserError(
                "In-place page-images processing overwrites files. Use --overwrite to proceed."
            )

    recorder = ManifestRecorder(
        tool_name="pdf_toolkit",
        tool_version=options.get("version", "0.0.0"),
        command=command_string,
        options=options,
        inputs={"in_dir": str(in_dir), "glob": pattern, "mode": mode},
        outputs={"out_dir": str(out_dir), "manifest": str(manifest_path)},
        dry_run=dry_run,
    )

    files = _collect_image_files(in_dir, pattern)
    recorder.inputs["files_found"] = len(files)

    if not files:
        recorder.log(f"No files matched {pattern} in {in_dir}")
        recorder.write_manifest(
            manifest_path,
            summary={"status": "no-matches", "files_found": 0},
        )
        return

    recorder.log(f"Processing {len(files)} image(s) with mode={mode}.")

    debug_dir = out_dir / "_debug"
    if not dry_run:
        ensure_dir(out_dir, dry_run=False)
        if debug or use_page_num_debug_crops:
            ensure_dir(debug_dir, dry_run=False)

    processed = 0
    split_count = 0
    crop_only_count = 0
    skipped = 0
    tesseract_exe = which_tesseract() if extract_page_numbers else None
    if extract_page_numbers and tesseract_exe is None:
        print(
            "WARN: --extract_page_numbers enabled but 'tesseract' not found in PATH; "
            "printed_page will be null. Install Tesseract and ensure it is on PATH.",
            file=sys.stderr,
        )

    for position, in_path in enumerate(files, start=1):
        try:
            with Image.open(in_path) as opened:
                source_image = opened.copy()
        except Exception as exc:  # pragma: no cover - codec/file errors
            raise UserError(f"Failed to read image {in_path}: {exc}") from exc

        width, height = source_image.size
        detected_spread = detect_spread(width, height, split_ratio)
        should_split = mode == "split" or (mode == "auto" and detected_spread)
        mode_used = "split" if should_split else "crop"
        notes: List[str] = []

        if mode == "split" and not detected_spread:
            notes.append("Forced split because mode=split.")
        if mode == "crop" and detected_spread:
            notes.append("Forced crop-only because mode=crop.")

        if should_split and width < 2:
            should_split = False
            mode_used = "crop"
            notes.append("Image too narrow to split; used crop-only.")

        if should_split:
            output_paths = [
                out_dir / f"{in_path.stem}_L{in_path.suffix}",
                out_dir / f"{in_path.stem}_R{in_path.suffix}",
            ]
        else:
            output_paths = [out_dir / in_path.name]

        if any(path.exists() for path in output_paths) and not overwrite:
            skipped += 1
            recorder.log(f"Skipping existing output(s) for {in_path.name}")
            skipped_outputs: List[object]
            if extract_page_numbers:
                skipped_outputs = [
                    {
                        "path": str(path),
                        "printed_page": None,
                        "printed_page_text": None,
                        "printed_page_kind": None,
                        "region_used": None,
                        "psm_used": None,
                        "corner": None,
                        "raw_left": "",
                        "raw_right": "",
                        "raw_by_region": {},
                        "reason": "skipped_existing_output",
                    }
                    for path in output_paths
                ]
            else:
                skipped_outputs = [str(path) for path in output_paths]
            recorder.add_action(
                action="page_images",
                status="skipped",
                input=str(in_path),
                outputs=skipped_outputs,
                mode_used=mode_used,
                detected_spread=detected_spread,
                notes=notes + ["One or more outputs already exist."],
            )
            continue

        gutter_x: Optional[int] = None
        left_bbox: Optional[BBox] = None
        right_bbox: Optional[BBox] = None
        crop_bbox: Optional[BBox] = None
        produced_images: List[Image.Image]

        if should_split:
            gray = source_image.convert("L")
            gutter_x, gutter_fallback = detect_gutter_x(
                gray=gray,
                gutter_search_frac=gutter_search_frac,
                x_step=x_step,
                y_step=y_step,
            )
            if gutter_fallback:
                notes.append("Gutter candidate near edge; fell back to center.")

            left_half, right_half = split_spread_image(source_image, gutter_x)
            left_cropped, left_bbox, left_notes = _crop_page_image(
                image=left_half,
                crop_threshold=crop_threshold,
                pad_px=pad_px,
                min_area_frac=min_area_frac,
            )
            right_cropped, right_bbox, right_notes = _crop_page_image(
                image=right_half,
                crop_threshold=crop_threshold,
                pad_px=pad_px,
                min_area_frac=min_area_frac,
            )
            notes.extend([f"left: {note}" for note in left_notes])
            notes.extend([f"right: {note}" for note in right_notes])
            produced_images = [left_cropped, right_cropped]
        else:
            cropped, crop_bbox, crop_notes = _crop_page_image(
                image=source_image,
                crop_threshold=crop_threshold,
                pad_px=pad_px,
                min_area_frac=min_area_frac,
            )
            notes.extend(crop_notes)
            produced_images = [cropped]

        status = "dry-run" if dry_run else "written"
        if dry_run:
            recorder.log(
                f"[dry-run] Would process {in_path.name} ({position}/{len(files)})"
            )
        else:
            try:
                for produced, out_path in zip(produced_images, output_paths):
                    produced.save(out_path)
            except Exception as exc:  # pragma: no cover - codec/file errors
                raise UserError(f"Failed to write output for {in_path}: {exc}") from exc
            recorder.log(
                f"Processed {in_path.name} ({position}/{len(files)}) -> "
                f"{', '.join(str(path) for path in output_paths)}"
            )

        if debug:
            debug_path = debug_dir / f"{in_path.stem}_debug.png"
            if dry_run:
                recorder.log(f"[dry-run] Would write debug image: {debug_path}")
            elif debug_path.exists() and not overwrite:
                recorder.log(f"Skipping existing debug image: {debug_path}")
            else:
                debug_image = _draw_debug_overlay(
                    source_image=source_image,
                    mode_used=mode_used,
                    gutter_x=gutter_x,
                    left_bbox=left_bbox,
                    right_bbox=right_bbox,
                    crop_bbox=crop_bbox,
                )
                debug_image.save(debug_path)

        processed += 1
        if mode_used == "split":
            split_count += 1
        else:
            crop_only_count += 1

        output_entries: List[object]
        if extract_page_numbers:
            output_entries = []
            for produced, out_path in zip(produced_images, output_paths):
                debug_base = out_path.stem
                if use_page_num_debug_crops and dry_run:
                    debug_regions = build_page_num_regions(
                        produced.width,
                        produced.height,
                        {
                            "anchors": resolved_anchors,
                            "allow_positions": resolved_positions,
                            "strip_frac": page_num_strip_frac,
                            "strip_y_offset_px": page_num_strip_y_offset_px,
                            "corner_w_frac": page_num_corner_w_frac,
                            "corner_h_frac": page_num_corner_h_frac,
                            "center_w_frac": page_num_center_w_frac,
                        },
                    )
                    planned_debug = [
                        str(debug_dir / f"{debug_base}__{name}.png") for name, _ in debug_regions
                    ]
                    recorder.log(
                        f"[dry-run] Would write page-number crops: {', '.join(planned_debug)}"
                    )
                extraction = extract_printed_page_number(
                    produced,
                    {
                        "anchors": resolved_anchors,
                        "allow_positions": resolved_positions,
                        "positions": resolved_positions,
                        "parser": resolved_parser,
                        "roman_whitelist": page_num_roman_whitelist,
                        "strip_frac": page_num_strip_frac,
                        "strip_y_offset_px": page_num_strip_y_offset_px,
                        "corner_w_frac": page_num_corner_w_frac,
                        "corner_h_frac": page_num_corner_h_frac,
                        "center_w_frac": page_num_center_w_frac,
                        "psm_candidates": resolved_psm_candidates,
                        "max_page": page_num_max,
                        "prep_scale": page_num_prep_scale,
                        "bin_threshold": page_num_bin_threshold,
                        "invert": page_num_invert,
                        "dark_bbox": resolved_dark_bbox,
                        "debug_crops": use_page_num_debug_crops and not dry_run,
                        "debug_dir": debug_dir,
                        "debug_base": debug_base,
                    },
                    tesseract_exe=tesseract_exe,
                )
                output_record: Dict[str, object] = {
                    "path": str(out_path),
                    "printed_page": extraction["printed_page"],
                    "printed_page_text": extraction["printed_page_text"],
                    "printed_page_kind": extraction["printed_page_kind"],
                    "corner": extraction["corner"],
                    "region_used": extraction["region_used"],
                    "psm_used": extraction["psm_used"],
                    "raw_left": extraction["raw_left"],
                    "raw_right": extraction["raw_right"],
                    "raw_by_region": extraction["raw_by_region"],
                    "reason": extraction["reason"],
                }
                if extraction.get("tesseract_error"):
                    output_record["tesseract_error"] = extraction["tesseract_error"]
                    notes.append(
                        f"OCR warning for {out_path.name}: {extraction['tesseract_error']}"
                    )
                output_entries.append(output_record)
        else:
            output_entries = [str(path) for path in output_paths]

        action_details = {
            "input": str(in_path),
            "outputs": output_entries,
            "mode_used": mode_used,
            "detected_spread": detected_spread,
            "notes": notes,
        }
        if gutter_x is not None:
            action_details["gutter_x"] = gutter_x
        if left_bbox is not None:
            action_details["left_bbox"] = left_bbox
        if right_bbox is not None:
            action_details["right_bbox"] = right_bbox
        if crop_bbox is not None:
            action_details["crop_bbox"] = crop_bbox

        recorder.add_action(action="page_images", status=status, **action_details)

    summary = {
        "files_found": len(files),
        "processed": processed,
        "split_count": split_count,
        "crop_only_count": crop_only_count,
        "skipped": skipped,
        "output_dir": str(out_dir),
    }
    recorder.write_manifest(manifest_path, summary)
