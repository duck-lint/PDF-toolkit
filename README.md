# PDF Toolkit (Local Windows CLI or Obsidian Plugin)

Small, local, lightweight PDF utility CLI for Windows. Everything runs offline
once dependencies are installed.
Front-end through Obsidian plugin availible at https://github.com/duck-lint/pdf-toolkit-obsidian-plugin

Features:
- Render PDF pages to PNGs (PyMuPDF)
- Split a PDF into multiple PDFs
- Rotate PDF pages or rotate PNGs (Pillow)
- Split spread scans into single-page images and crop page bounds (Pillow)
- Safe defaults with `--dry-run` and `--overwrite`
- JSON manifest written for each command

## Install (Windows)

1) Create and activate a virtual environment (optional but recommended):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2) Install dependencies:

```powershell
pip install -r requirements.txt
```

3) Install this package in editable mode so `python -m pdf-toolkit` works:

```powershell
pip install -e .
```

If you prefer not to install it, you can temporarily set `PYTHONPATH`:

```powershell
$env:PYTHONPATH = "src"
```

## Usage

See all commands:

```powershell
python -m pdf-toolkit --help
```

### Render PDF to PNG

```powershell
python -m pdf-toolkit render --pdf "in.pdf" --out_dir "out\pages" --dpi 300 --format png --prefix "book1"
```

Dry-run (no files written):

```powershell
python -m pdf-toolkit render --pdf "in.pdf" --out_dir "out\pages" --pages "1-10,15" --dry-run
```

Output naming is predictable:
`book1_p0001.png`, `book1_p0002.png`, etc.

### Split PDF

Explicit ranges:

```powershell
python -m pdf-toolkit split --pdf "in.pdf" --out_dir "out\splits" --ranges "1-120,121-240" --prefix "book"
```

Automatic chunking:

```powershell
python -m pdf-toolkit split --pdf "in.pdf" --out_dir "out\splits" --pages_per_file 120 --prefix "book"
```

Outputs:
`book_part01.pdf`, `book_part02.pdf`, etc.

### Rotate PDF pages

```powershell
python -m pdf-toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
```

In-place (overwrites input):

```powershell
python -m pdf-toolkit rotate pdf --pdf "in.pdf" --out_pdf "in.pdf" --degrees 180 --pages "1-5" --inplace --overwrite
```

### Rotate PNGs in a folder

```powershell
python -m pdf-toolkit rotate images --in_dir "out\pages" --glob "*.png" --degrees 90 --out_dir "out\pages_rot"
```

In-place (overwrites files):

```powershell
python -m pdf-toolkit rotate images --in_dir "out\pages" --glob "*.png" --degrees 90 --out_dir "out\pages" --inplace --overwrite
```

### Page-images (split spreads + crop)

Auto mode (split if wide enough, otherwise crop-only):

```powershell
python -m pdf-toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --glob "*.png" --mode auto --debug
```

Always split:

```powershell
python -m pdf-toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode split --overwrite
```

Never split (crop-only):

```powershell
python -m pdf-toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode crop
```

Useful page-images tuning flags:
- `--gutter_trim_px`: shave pixels from both sides of the detected gutter when splitting spreads.
- `--edge_inset_px`: inset the final padded crop box inward to remove faint border noise.

Example with both knobs:

```powershell
python -m pdf-toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode split --gutter_trim_px 20 --edge_inset_px 6 --debug
```

### Page-images YAML config

Dump the default YAML config:

```powershell
python -m pdf-toolkit page-images --dump-default-config
```

Use a config file:

```powershell
python -m pdf-toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --config "configs\page_images.default.yaml"
```

Precedence is deterministic:
`built-in defaults < YAML config < explicitly provided CLI flags`.

This means optional CLI defaults do not overwrite YAML values unless the flag is explicitly passed.

Supported YAML shapes:

Root form:

```yaml
mode: auto
split_ratio: 1.25
crop_threshold: 180
pad_px: 20
```

Wrapped form:

```yaml
page_images:
  mode: auto
  split_ratio: 1.25
  gutter_search_frac: 0.35
  crop_threshold: 180
  min_area_frac: 0.25
```

Recommended pipeline:
`render -> page-images`

## Page selection format

Pages are 1-based for user input:
- `all`
- `1-10`
- `1-10,15,20-25`

## Manifest output

Each command writes a JSON manifest describing:
- Inputs, outputs, options
- Actions taken (written, skipped, dry-run)
- Timestamps

`page-images` action outputs list the written files, plus split/crop metadata (`gutter_x`, bboxes, spread detection notes).

```json
["out/pages_single/book_p0001_L.png", "out/pages_single/book_p0001_R.png"]
```

By default the manifest is written to:
- Render: `out_dir\manifest.json`
- Split: `out_dir\manifest.json`
- Rotate PDF: `out_pdf` folder\manifest.json
- Rotate images: `out_dir\manifest.json`
- Page-images: `out_dir\manifest.json`

`--dry-run` skips writing the manifest (it is treated like an output file).

## Testing (lightweight)

Run the minimal unit tests:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## Create a clean zip

Prefer creating release zips from git history:

```powershell
git archive --format=zip --output pdf-toolkit.zip HEAD
```

If you zip manually, delete `__pycache__/` folders and `*.pyc` files first.
