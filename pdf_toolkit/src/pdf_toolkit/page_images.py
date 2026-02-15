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
) -> None:
    """
    Process page images by optional spread split + page crop.

    Modes:
    - auto: split only when aspect ratio indicates a spread
    - split: always split then crop each half
    - crop: never split, only crop
    """

    recorder = ManifestRecorder(
        tool_name="pdf_toolkit",
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
            x_step=x_step,
            y_step=y_step,
            crop_threshold=crop_threshold,
            pad_px=pad_px,
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

            action_details = {
                "input": str(in_path),
                "outputs": [str(path) for path in output_paths],
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
