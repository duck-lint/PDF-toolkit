"""
Configuration helpers for YAML-backed command options.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency availability
    yaml = None  # type: ignore[assignment]

from .utils import UserError, ensure_file_exists


DEFAULT_PAGE_IMAGES: dict[str, Any] = {
    "glob": "*.png",
    "mode": "auto",
    "split_ratio": 1.25,
    "gutter_search_frac": 0.35,
    "gutter_trim_px": 0,
    "outer_margin_mode": "off",
    "outer_margin_frac": 0.0,
    "outer_margin_auto_max_frac": 0.15,
    "outer_margin_auto_search_frac": 0.18,
    "outer_margin_dark_threshold": 80,
    "outer_margin_dark_frac_cutoff": 0.60,
    "outer_margin_release_frac": 0.35,
    "outer_margin_min_run_px": 12,
    "outer_margin_pad_px": 4,
    "x_step": 2,
    "y_step": 4,
    "crop_threshold": 180,
    "pad_px": 20,
    "edge_inset_px": 0,
    "symmetry_strategy": "independent",
    "min_area_frac": 0.25,
    "debug": False,
    "overwrite": False,
    "inplace": False,
    "dry_run": False,
    "manifest": None,
}


def _require_yaml() -> Any:
    """Return yaml module or raise a user-facing install hint."""

    if yaml is None:
        raise UserError(
            "YAML support requires PyYAML. Install dependencies with "
            "'pip install -r requirements.txt' or install 'PyYAML'."
        )
    return yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dictionary."""

    ensure_file_exists(path, "Config file")
    yaml_mod = _require_yaml()
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml_mod.safe_load(handle)
    except yaml_mod.YAMLError as exc:
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


def dump_default_page_images_yaml() -> str:
    """Serialize wrapped page-images defaults as YAML."""

    yaml_mod = _require_yaml()
    return yaml_mod.safe_dump({"page_images": DEFAULT_PAGE_IMAGES}, sort_keys=False).rstrip()
