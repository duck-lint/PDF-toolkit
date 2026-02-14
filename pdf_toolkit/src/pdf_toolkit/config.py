"""
Configuration helpers for YAML-backed command options.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .utils import UserError, ensure_file_exists


DEFAULT_PAGE_IMAGES: dict[str, Any] = {
    "glob": "*.png",
    "mode": "auto",
    "split_ratio": 1.25,
    "gutter_search_frac": 0.35,
    "x_step": 2,
    "y_step": 4,
    "crop_threshold": 180,
    "pad_px": 20,
    "min_area_frac": 0.25,
    "debug": False,
    "overwrite": False,
    "inplace": False,
    "dry_run": False,
    "manifest": None,
    "page_numbers": {
        "enabled": False,
        "anchors": ["top"],
        "positions": ["right", "left"],
        "allow_positions": ["right", "left"],
        "parser": "auto",
        "roman_whitelist": "IVXLCDMivxlcdm",
        "strip_frac": 0.12,
        "strip_y_offset_px": 0,
        "corner_w_frac": 0.28,
        "corner_h_frac": 0.45,
        "center_w_frac": 0.20,
        "psm_candidates": [7, 8, 6, 11],
        "max_page": 5000,
        "prep_scale": 2,
        "bin_threshold": 160,
        "invert": False,
        "debug_crops": False,
        "dark_bbox": {
            "enabled": False,
            "threshold": 170,
            "pad_px": 2,
            "min_area_frac": 0.005,
        },
    },
}


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dictionary."""

    ensure_file_exists(path, "Config file")
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise UserError(f"Failed to parse YAML config {path}: {exc}") from exc
    except OSError as exc:
        raise UserError(f"Failed to read config {path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise UserError(f"Config {path} must contain a YAML mapping/object at top level.")
    return loaded


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge dictionaries where overlay values win.
    """

    merged = deepcopy(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def validate_keys(cfg: dict[str, Any], allowed: set[str], ctx: str) -> None:
    """
    Validate dictionary keys and fail fast on unknown entries.
    """

    unknown = sorted(key for key in cfg.keys() if key not in allowed)
    if unknown:
        allowed_list = ", ".join(sorted(allowed))
        unknown_list = ", ".join(unknown)
        raise UserError(
            f"Unknown keys in {ctx}: {unknown_list}. Allowed keys: {allowed_list}."
        )
