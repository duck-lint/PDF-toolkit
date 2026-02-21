"""
Split spread scans into single-page images and crop page bounds.

Why this module exists:
- Spread scans often have dark backgrounds with bright page regions.
- We can detect a dark center gutter, split into left/right pages, then crop
  each page to the bright region using simple Pillow-only heuristics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .manifest import ManifestRecorder
from .utils import UserError, ensure_dir, ensure_dir_path


BBox = Tuple[int, int, int, int]
SYMMETRY_STRATEGIES = {"independent", "match_max_width", "mirror_from_gutter"}


def _collect_image_files(in_dir: Path, pattern: str) -> List[Path]:
    """Return matching files in stable order."""

    return sorted(path for path in in_dir.glob(pattern) if path.is_file())


def _validate_options(
    mode: str,
    split_ratio: float,
    gutter_search_frac: float,
    gutter_trim_px: int,
    outer_margin_mode: str,
    outer_margin_frac: float,
    outer_margin_auto_max_frac: float,
    outer_margin_auto_search_frac: float,
    outer_margin_dark_threshold: int,
    outer_margin_dark_frac_cutoff: float,
    outer_margin_release_frac: float,
    outer_margin_min_run_px: int,
    outer_margin_pad_px: int,
    x_step: int,
    y_step: int,
    crop_threshold: int,
    pad_px: int,
    edge_inset_px: int,
    symmetry_strategy: str,
    min_area_frac: float,
) -> None:
    """Validate user-facing options and raise clear errors."""

    if mode not in {"auto", "split", "crop"}:
        raise UserError("Mode must be one of: auto, split, crop.")
    if split_ratio <= 0:
        raise UserError("--split_ratio must be > 0.")
    if gutter_search_frac <= 0 or gutter_search_frac > 1:
        raise UserError("--gutter_search_frac must be in the range (0, 1].")
    if gutter_trim_px < 0:
        raise UserError("--gutter_trim_px must be >= 0.")
    if outer_margin_mode not in {"off", "fixed", "auto"}:
        raise UserError("--outer_margin_mode must be one of: off, fixed, auto.")
    if outer_margin_mode == "fixed" and (outer_margin_frac < 0 or outer_margin_frac > 0.25):
        raise UserError("--outer_margin_frac must be in the range [0, 0.25] for fixed mode.")
    if outer_margin_mode == "auto" and (
        outer_margin_auto_max_frac < 0 or outer_margin_auto_max_frac > 0.25
    ):
        raise UserError(
            "--outer_margin_auto_max_frac must be in the range [0, 0.25] for auto mode."
        )
    if outer_margin_auto_search_frac <= 0 or outer_margin_auto_search_frac > 0.5:
        raise UserError("--outer_margin_auto_search_frac must be in the range (0, 0.5].")
    if outer_margin_dark_threshold < 0 or outer_margin_dark_threshold > 255:
        raise UserError("--outer_margin_dark_threshold must be in the range [0, 255].")
    if outer_margin_dark_frac_cutoff < 0 or outer_margin_dark_frac_cutoff > 1:
        raise UserError("--outer_margin_dark_frac_cutoff must be in the range [0, 1].")
    if outer_margin_release_frac < 0 or outer_margin_release_frac > 1:
        raise UserError("--outer_margin_release_frac must be in the range [0, 1].")
    if outer_margin_release_frac >= outer_margin_dark_frac_cutoff:
        raise UserError("--outer_margin_release_frac must be < --outer_margin_dark_frac_cutoff.")
    if outer_margin_min_run_px < 1:
        raise UserError("--outer_margin_min_run_px must be >= 1.")
    if outer_margin_pad_px < 0:
        raise UserError("--outer_margin_pad_px must be >= 0.")
    if x_step <= 0:
        raise UserError("--x_step must be a positive integer.")
    if y_step <= 0:
        raise UserError("--y_step must be a positive integer.")
    if crop_threshold < 0 or crop_threshold > 255:
        raise UserError("--crop_threshold must be in the range [0, 255].")
    if pad_px < 0:
        raise UserError("--pad_px must be >= 0.")
    if edge_inset_px < 0:
        raise UserError("--edge_inset_px must be >= 0.")
    if symmetry_strategy not in SYMMETRY_STRATEGIES:
        raise UserError(
            "--symmetry_strategy must be one of: "
            "independent, match_max_width, mirror_from_gutter."
        )
    if min_area_frac <= 0 or min_area_frac > 1:
        raise UserError("--min_area_frac must be in the range (0, 1].")


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


def split_spread_image(
    image: Image.Image, gutter_x: int, gutter_trim_px: int = 0
) -> Tuple[Image.Image, Image.Image]:
    """Split a spread image into left and right halves at gutter_x."""

    width, height = image.size
    if width < 2:
        raise UserError("Image is too narrow to split into two pages.")
    safe_gutter_x = max(1, min(width - 1, gutter_x))
    trim = max(0, gutter_trim_px)

    left_right = max(1, safe_gutter_x - trim)
    right_left = min(width - 1, safe_gutter_x + trim)

    if left_right <= 0:
        left_right = 1
    if right_left >= width:
        right_left = width - 1
    if right_left < left_right:
        mid = safe_gutter_x
        left_right = max(1, min(width - 1, mid))
        right_left = max(left_right + 1, min(width - 1, mid + 1))
        if right_left > width - 1:
            right_left = width - 1

    left = image.crop((0, 0, left_right, height))
    right = image.crop((right_left, 0, width, height))
    return left, right


def detect_outer_black_bar_px(
    image: Image.Image,
    *,
    side: str,
    search_frac: float,
    dark_threshold: int,
    dark_frac_cutoff: float,
    release_frac: float,
    min_run_px: int,
) -> int:
    """
    Detect dark outer-edge bar width in pixels.

    Returns 0 when no stable outer bar is detected.
    """

    if side not in {"left", "right"}:
        raise UserError("side must be 'left' or 'right' for outer bar detection.")

    gray = image.convert("L") if image.mode != "L" else image
    width, height = gray.size
    if width <= 0 or height <= 0:
        return 0

    search_width = max(1, min(width, int(width * search_frac)))
    pixels = gray.load()
    saw_bar = False
    consecutive_release = 0

    for idx in range(search_width):
        x = idx if side == "left" else (width - 1 - idx)
        dark_count = 0
        for y in range(height):
            if int(pixels[x, y]) < dark_threshold:
                dark_count += 1
        dark_fraction = dark_count / height

        if dark_fraction >= dark_frac_cutoff:
            saw_bar = True
            consecutive_release = 0
            continue

        if saw_bar and dark_fraction <= release_frac:
            consecutive_release += 1
            if consecutive_release >= min_run_px:
                bar_width = idx - consecutive_release + 1
                return max(0, bar_width)
        elif saw_bar:
            consecutive_release = 0

    if saw_bar:
        return search_width
    return 0


def _resolve_outer_clamp_px(
    image: Image.Image,
    *,
    outer_margin_mode: str,
    outer_margin_frac: float,
    outer_margin_auto_max_frac: float,
    outer_margin_auto_search_frac: float,
    outer_margin_dark_threshold: int,
    outer_margin_dark_frac_cutoff: float,
    outer_margin_release_frac: float,
    outer_margin_min_run_px: int,
    outer_margin_pad_px: int,
    is_left_page: bool,
) -> Tuple[int, int]:
    """Resolve detected bar width and applied outer clamp width."""

    width, _ = image.size
    if outer_margin_mode == "off":
        return 0, 0
    if outer_margin_mode == "fixed":
        return 0, max(0, int(width * outer_margin_frac))

    side = "left" if is_left_page else "right"
    detected_bar_px = detect_outer_black_bar_px(
        image,
        side=side,
        search_frac=outer_margin_auto_search_frac,
        dark_threshold=outer_margin_dark_threshold,
        dark_frac_cutoff=outer_margin_dark_frac_cutoff,
        release_frac=outer_margin_release_frac,
        min_run_px=outer_margin_min_run_px,
    )
    max_clamp_px = max(0, int(width * outer_margin_auto_max_frac))
    if detected_bar_px <= 0:
        return 0, 0
    applied_clamp_px = min(detected_bar_px + outer_margin_pad_px, max_clamp_px)
    return detected_bar_px, max(0, applied_clamp_px)


def find_crop_bbox(
    image: Image.Image,
    crop_threshold: int,
    pad_px: int,
    min_area_frac: float,
    edge_inset_px: int = 0,
    outer_margin_mode: str = "off",
    outer_margin_frac: float = 0.0,
    outer_margin_auto_max_frac: float = 0.15,
    outer_margin_auto_search_frac: float = 0.18,
    outer_margin_dark_threshold: int = 80,
    outer_margin_dark_frac_cutoff: float = 0.60,
    outer_margin_release_frac: float = 0.35,
    outer_margin_min_run_px: int = 12,
    outer_margin_pad_px: int = 4,
    is_left_page: bool = True,
    outer_clamp_debug: Optional[Dict[str, int | str]] = None,
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

    inset = max(0, edge_inset_px)
    if inset > 0:
        left = min(right - 1, left + inset)
        top = min(bottom - 1, top + inset)
        right = max(left + 1, right - inset)
        bottom = max(top + 1, bottom - inset)

    if right <= left or bottom <= top:
        return full_bbox, True, "Invalid crop bounds after edge inset; used full image."

    detected_bar_px, clamp_px = _resolve_outer_clamp_px(
        image=image,
        outer_margin_mode=outer_margin_mode,
        outer_margin_frac=outer_margin_frac,
        outer_margin_auto_max_frac=outer_margin_auto_max_frac,
        outer_margin_auto_search_frac=outer_margin_auto_search_frac,
        outer_margin_dark_threshold=outer_margin_dark_threshold,
        outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
        outer_margin_release_frac=outer_margin_release_frac,
        outer_margin_min_run_px=outer_margin_min_run_px,
        outer_margin_pad_px=outer_margin_pad_px,
        is_left_page=is_left_page,
    )
    if outer_clamp_debug is not None:
        outer_clamp_debug["mode"] = outer_margin_mode
        outer_clamp_debug["detected_bar_px"] = int(detected_bar_px)
        outer_clamp_debug["applied_clamp_px"] = int(clamp_px)

    if clamp_px > 0:
        if is_left_page:
            left = max(left, clamp_px)
        else:
            right = min(right, width - clamp_px)

    if right <= left or bottom <= top:
        return full_bbox, True, "Invalid crop bounds after outer margin clamp; used full image."

    return (left, top, right, bottom), False, None


def _crop_page_image(
    image: Image.Image,
    crop_threshold: int,
    pad_px: int,
    edge_inset_px: int,
    outer_margin_mode: str,
    outer_margin_frac: float,
    outer_margin_auto_max_frac: float,
    outer_margin_auto_search_frac: float,
    outer_margin_dark_threshold: int,
    outer_margin_dark_frac_cutoff: float,
    outer_margin_release_frac: float,
    outer_margin_min_run_px: int,
    outer_margin_pad_px: int,
    is_left_page: bool,
    min_area_frac: float,
) -> Tuple[Image.Image, BBox, List[str], Dict[str, int | str]]:
    """Crop a page image and return cropped image + bbox + notes."""

    outer_clamp_debug: Dict[str, int | str] = {}
    bbox, used_fallback, note = find_crop_bbox(
        image=image,
        crop_threshold=crop_threshold,
        pad_px=pad_px,
        edge_inset_px=edge_inset_px,
        outer_margin_mode=outer_margin_mode,
        outer_margin_frac=outer_margin_frac,
        outer_margin_auto_max_frac=outer_margin_auto_max_frac,
        outer_margin_auto_search_frac=outer_margin_auto_search_frac,
        outer_margin_dark_threshold=outer_margin_dark_threshold,
        outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
        outer_margin_release_frac=outer_margin_release_frac,
        outer_margin_min_run_px=outer_margin_min_run_px,
        outer_margin_pad_px=outer_margin_pad_px,
        is_left_page=is_left_page,
        outer_clamp_debug=outer_clamp_debug,
        min_area_frac=min_area_frac,
    )
    cropped = image.crop(bbox)
    cropped.load()

    notes: List[str] = []
    if used_fallback and note:
        notes.append(note)
    return cropped, bbox, notes, outer_clamp_debug


def _bbox_width(bbox: BBox) -> int:
    """Return bbox width in pixels."""

    return bbox[2] - bbox[0]


def _apply_split_symmetry_strategy(
    left_bbox: BBox,
    right_bbox: BBox,
    left_image_width: int,
    right_image_width: int,
    gutter_x: int,
    right_offset_x: int,
    strategy: str,
    gutter_trim_px: int = 0,
    left_outer_clamp_px: int = 0,
    right_outer_clamp_px: int = 0,
) -> Tuple[BBox, BBox, Optional[str]]:
    """
    Apply a split-page symmetry strategy.

    Returns (left_bbox, right_bbox, note) where note is set when strategy
    falls back to independent behavior.
    """

    if strategy == "independent":
        return left_bbox, right_bbox, None

    original_left = left_bbox
    original_right = right_bbox
    left_l, left_t, left_r, left_b = left_bbox
    right_l, right_t, right_r, right_b = right_bbox

    # Split boundaries already remove the gutter trim band, so these are hard bounds.
    _ = gutter_trim_px
    left_min_left = max(0, left_outer_clamp_px)
    left_max_right = left_image_width
    right_min_left = 0
    right_max_right = max(1, right_image_width - max(0, right_outer_clamp_px))

    if strategy == "match_max_width":
        left_width = left_r - left_l
        right_width = right_r - right_l
        max_width = max(left_width, right_width)

        if left_width < max_width:
            left_r = min(left_max_right, left_l + max_width)
        if right_width < max_width:
            right_l = max(right_min_left, right_r - max_width)
    elif strategy == "mirror_from_gutter":
        right_global_left = right_offset_x + right_l
        left_gap = max(0, gutter_x - left_r)
        right_gap = max(0, right_global_left - gutter_x)
        target_gap = max(left_gap, right_gap)

        left_r = min(left_max_right, max(left_l + 1, gutter_x - target_gap))
        mirrored_right_global_left = gutter_x + target_gap
        mirrored_right_local_left = mirrored_right_global_left - right_offset_x
        right_l = max(right_min_left, min(right_r - 1, mirrored_right_local_left))
    else:  # defensive fallback; _validate_options should prevent this.
        return original_left, original_right, "Unknown symmetry strategy; used independent."

    left_l = max(left_l, left_min_left)
    right_r = min(right_r, right_max_right)
    left_r = min(left_r, left_max_right)
    right_l = max(right_l, right_min_left)

    candidate_left: BBox = (left_l, left_t, left_r, left_b)
    candidate_right: BBox = (right_l, right_t, right_r, right_b)
    if candidate_left[2] <= candidate_left[0] or candidate_right[2] <= candidate_right[0]:
        if strategy == "mirror_from_gutter":
            return (
                original_left,
                original_right,
                "Mirror symmetry could not be satisfied safely; used independent.",
            )
        return (
            original_left,
            original_right,
            f"Invalid symmetry bounds for strategy={strategy}; used independent.",
        )

    return candidate_left, candidate_right, None


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
    gutter_trim_px: int = 0,
    edge_inset_px: int = 0,
    outer_margin_mode: str = "off",
    outer_margin_frac: float = 0.0,
    outer_margin_auto_max_frac: float = 0.15,
    outer_margin_auto_search_frac: float = 0.18,
    outer_margin_dark_threshold: int = 80,
    outer_margin_dark_frac_cutoff: float = 0.60,
    outer_margin_release_frac: float = 0.35,
    outer_margin_min_run_px: int = 12,
    outer_margin_pad_px: int = 4,
    symmetry_strategy: str = "independent",
) -> None:
    """
    Process page images by optional spread split + page crop.

    Modes:
    - auto: split only when aspect ratio indicates a spread
    - split: always split then crop each half
    - crop: never split, only crop
    """

    recorder = ManifestRecorder(
        tool_name="pdf-toolkit",
        tool_version=str(options.get("version", "0.0.0")),
        command=command_string,
        options=options,
        inputs={"in_dir": str(in_dir), "glob": pattern, "mode": mode},
        outputs={"out_dir": str(out_dir), "manifest": str(manifest_path)},
        dry_run=dry_run,
        verbosity=str(options.get("verbosity", "normal")),
    )

    files: List[Path] = []
    processed = 0
    split_count = 0
    crop_only_count = 0
    skipped = 0
    error_message: str | None = None
    summary: Dict[str, object] = {
        "files_found": 0,
        "processed": 0,
        "split_count": 0,
        "crop_only_count": 0,
        "skipped": 0,
        "output_dir": str(out_dir),
    }

    try:
        if not in_dir.exists() or not in_dir.is_dir():
            raise UserError(f"Input directory not found: {in_dir}")
        ensure_dir_path(out_dir, "Output directory")

        _validate_options(
            mode=mode,
            split_ratio=split_ratio,
            gutter_search_frac=gutter_search_frac,
            gutter_trim_px=gutter_trim_px,
            outer_margin_mode=outer_margin_mode,
            outer_margin_frac=outer_margin_frac,
            outer_margin_auto_max_frac=outer_margin_auto_max_frac,
            outer_margin_auto_search_frac=outer_margin_auto_search_frac,
            outer_margin_dark_threshold=outer_margin_dark_threshold,
            outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
            outer_margin_release_frac=outer_margin_release_frac,
            outer_margin_min_run_px=outer_margin_min_run_px,
            outer_margin_pad_px=outer_margin_pad_px,
            x_step=x_step,
            y_step=y_step,
            crop_threshold=crop_threshold,
            pad_px=pad_px,
            edge_inset_px=edge_inset_px,
            symmetry_strategy=symmetry_strategy,
            min_area_frac=min_area_frac,
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

        files = _collect_image_files(in_dir, pattern)
        recorder.inputs["files_found"] = len(files)

        if not files:
            recorder.log(f"No files matched {pattern} in {in_dir}")
            summary["status"] = "no-matches"
            summary["files_found"] = 0
            return

        recorder.log(f"Processing {len(files)} image(s) with mode={mode}.")

        debug_dir = out_dir / "_debug"
        if not dry_run:
            ensure_dir(out_dir, dry_run=False)
            if debug:
                ensure_dir(debug_dir, dry_run=False)

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
                recorder.add_action(
                    action="page_images",
                    status="skipped",
                    input=str(in_path),
                    outputs=[str(path) for path in output_paths],
                    mode_used=mode_used,
                    detected_spread=detected_spread,
                    notes=notes + ["One or more outputs already exist."],
                )
                continue

            gutter_x: Optional[int] = None
            left_bbox: Optional[BBox] = None
            right_bbox: Optional[BBox] = None
            crop_bbox: Optional[BBox] = None
            bbox_delta_width: Optional[int] = None
            left_outer_info: Dict[str, int | str] | None = None
            right_outer_info: Dict[str, int | str] | None = None
            crop_outer_info: Dict[str, int | str] | None = None
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

                left_half, right_half = split_spread_image(
                    source_image,
                    gutter_x,
                    gutter_trim_px=gutter_trim_px,
                )
                left_cropped, left_bbox, left_notes, left_outer_info = _crop_page_image(
                    image=left_half,
                    crop_threshold=crop_threshold,
                    pad_px=pad_px,
                    edge_inset_px=edge_inset_px,
                    outer_margin_mode=outer_margin_mode,
                    outer_margin_frac=outer_margin_frac,
                    outer_margin_auto_max_frac=outer_margin_auto_max_frac,
                    outer_margin_auto_search_frac=outer_margin_auto_search_frac,
                    outer_margin_dark_threshold=outer_margin_dark_threshold,
                    outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
                    outer_margin_release_frac=outer_margin_release_frac,
                    outer_margin_min_run_px=outer_margin_min_run_px,
                    outer_margin_pad_px=outer_margin_pad_px,
                    is_left_page=True,
                    min_area_frac=min_area_frac,
                )
                right_cropped, right_bbox, right_notes, right_outer_info = _crop_page_image(
                    image=right_half,
                    crop_threshold=crop_threshold,
                    pad_px=pad_px,
                    edge_inset_px=edge_inset_px,
                    outer_margin_mode=outer_margin_mode,
                    outer_margin_frac=outer_margin_frac,
                    outer_margin_auto_max_frac=outer_margin_auto_max_frac,
                    outer_margin_auto_search_frac=outer_margin_auto_search_frac,
                    outer_margin_dark_threshold=outer_margin_dark_threshold,
                    outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
                    outer_margin_release_frac=outer_margin_release_frac,
                    outer_margin_min_run_px=outer_margin_min_run_px,
                    outer_margin_pad_px=outer_margin_pad_px,
                    is_left_page=False,
                    min_area_frac=min_area_frac,
                )
                notes.extend([f"left: {note}" for note in left_notes])
                notes.extend([f"right: {note}" for note in right_notes])

                right_offset_x = source_image.width - right_half.width
                original_left_bbox = left_bbox
                original_right_bbox = right_bbox
                left_bbox, right_bbox, symmetry_note = _apply_split_symmetry_strategy(
                    left_bbox=left_bbox,
                    right_bbox=right_bbox,
                    left_image_width=left_half.width,
                    right_image_width=right_half.width,
                    gutter_x=gutter_x,
                    right_offset_x=right_offset_x,
                    strategy=symmetry_strategy,
                    gutter_trim_px=gutter_trim_px,
                    left_outer_clamp_px=int(left_outer_info.get("applied_clamp_px", 0))
                    if left_outer_info
                    else 0,
                    right_outer_clamp_px=int(right_outer_info.get("applied_clamp_px", 0))
                    if right_outer_info
                    else 0,
                )
                if symmetry_note:
                    notes.append(symmetry_note)

                if left_bbox != original_left_bbox:
                    left_cropped = left_half.crop(left_bbox)
                    left_cropped.load()
                if right_bbox != original_right_bbox:
                    right_cropped = right_half.crop(right_bbox)
                    right_cropped.load()

                left_width = _bbox_width(left_bbox)
                right_width = _bbox_width(right_bbox)
                bbox_delta_width = abs(left_width - right_width)
                if debug:
                    if left_outer_info is not None:
                        print(
                            f"[DEBUG] outer_clamp side=left mode={left_outer_info.get('mode', 'off')} "
                            f"detected_bar_px={left_outer_info.get('detected_bar_px', 0)} "
                            f"applied_clamp_px={left_outer_info.get('applied_clamp_px', 0)}"
                        )
                    if right_outer_info is not None:
                        print(
                            f"[DEBUG] outer_clamp side=right mode={right_outer_info.get('mode', 'off')} "
                            f"detected_bar_px={right_outer_info.get('detected_bar_px', 0)} "
                            f"applied_clamp_px={right_outer_info.get('applied_clamp_px', 0)}"
                        )
                    print(
                        f"[DEBUG] bbox_delta_width={bbox_delta_width} "
                        f"left_width={left_width} right_width={right_width} "
                        f"strategy={symmetry_strategy}"
                    )
                produced_images = [left_cropped, right_cropped]
            else:
                cropped, crop_bbox, crop_notes, crop_outer_info = _crop_page_image(
                    image=source_image,
                    crop_threshold=crop_threshold,
                    pad_px=pad_px,
                    edge_inset_px=edge_inset_px,
                    outer_margin_mode="off",
                    outer_margin_frac=0.0,
                    outer_margin_auto_max_frac=outer_margin_auto_max_frac,
                    outer_margin_auto_search_frac=outer_margin_auto_search_frac,
                    outer_margin_dark_threshold=outer_margin_dark_threshold,
                    outer_margin_dark_frac_cutoff=outer_margin_dark_frac_cutoff,
                    outer_margin_release_frac=outer_margin_release_frac,
                    outer_margin_min_run_px=outer_margin_min_run_px,
                    outer_margin_pad_px=outer_margin_pad_px,
                    is_left_page=True,
                    min_area_frac=min_area_frac,
                )
                notes.extend(crop_notes)
                produced_images = [cropped]
                if debug and crop_outer_info is not None:
                    print(
                        f"[DEBUG] outer_clamp side=single mode={crop_outer_info.get('mode', 'off')} "
                        f"detected_bar_px={crop_outer_info.get('detected_bar_px', 0)} "
                        f"applied_clamp_px={crop_outer_info.get('applied_clamp_px', 0)}"
                    )

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

            action_details = {
                "input": str(in_path),
                "outputs": [str(path) for path in output_paths],
                "mode_used": mode_used,
                "detected_spread": detected_spread,
                "notes": notes,
            }
            action_details["outer_clamp_mode"] = (
                str(left_outer_info.get("mode", outer_margin_mode))
                if left_outer_info is not None
                else str(crop_outer_info.get("mode", "off"))
                if crop_outer_info is not None
                else "off"
            )
            if gutter_x is not None:
                action_details["gutter_x"] = gutter_x
            if left_bbox is not None:
                action_details["left_bbox"] = left_bbox
            if right_bbox is not None:
                action_details["right_bbox"] = right_bbox
            if bbox_delta_width is not None:
                action_details["bbox_delta_width"] = bbox_delta_width
            if left_outer_info is not None and right_outer_info is not None:
                action_details["outer_detected_bar_px"] = {
                    "left": int(left_outer_info.get("detected_bar_px", 0)),
                    "right": int(right_outer_info.get("detected_bar_px", 0)),
                }
                action_details["outer_applied_clamp_px"] = {
                    "left": int(left_outer_info.get("applied_clamp_px", 0)),
                    "right": int(right_outer_info.get("applied_clamp_px", 0)),
                }
            elif crop_outer_info is not None:
                action_details["outer_detected_bar_px"] = int(
                    crop_outer_info.get("detected_bar_px", 0)
                )
                action_details["outer_applied_clamp_px"] = int(
                    crop_outer_info.get("applied_clamp_px", 0)
                )
            if crop_bbox is not None:
                action_details["crop_bbox"] = crop_bbox

            recorder.add_action(action="page_images", status=status, **action_details)
    except Exception as exc:  # pragma: no cover - includes validation and runtime errors
        if isinstance(exc, UserError):
            error_message = str(exc)
        else:
            error_message = f"Failed to process page-images in {in_dir}: {exc}"
        recorder.log(error_message, level="error")
        recorder.add_action(action="page_images", status="error", error=error_message)
        if isinstance(exc, UserError):
            raise
        raise UserError(error_message) from exc
    finally:
        summary["files_found"] = len(files)
        summary["processed"] = processed
        summary["split_count"] = split_count
        summary["crop_only_count"] = crop_only_count
        summary["skipped"] = skipped
        summary["status"] = summary.get("status", "error" if error_message else "ok")
        if error_message is not None:
            summary["error"] = error_message
        recorder.write_manifest(manifest_path, summary)
