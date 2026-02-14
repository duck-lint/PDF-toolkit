# PDF Toolkit (Local Windows CLI)

Small, local, lightweight PDF utilities for Windows. Everything runs offline
once dependencies are installed.

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

3) Install this package in editable mode so `python -m pdf_toolkit` works:

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
python -m pdf_toolkit --help
```

### Render PDF to PNG

```powershell
python -m pdf_toolkit render --pdf "in.pdf" --out_dir "out\pages" --dpi 300 --format png --prefix "book1"
```

Dry-run (no files written):

```powershell
python -m pdf_toolkit render --pdf "in.pdf" --out_dir "out\pages" --pages "1-10,15" --dry-run
```

Output naming is predictable:
`book1_p0001.png`, `book1_p0002.png`, etc.

### Split PDF

Explicit ranges:

```powershell
python -m pdf_toolkit split --pdf "in.pdf" --out_dir "out\splits" --ranges "1-120,121-240" --prefix "book"
```

Automatic chunking:

```powershell
python -m pdf_toolkit split --pdf "in.pdf" --out_dir "out\splits" --pages_per_file 120 --prefix "book"
```

Outputs:
`book_part01.pdf`, `book_part02.pdf`, etc.

### Rotate PDF pages

```powershell
python -m pdf_toolkit rotate pdf --pdf "in.pdf" --out_pdf "in_rotated.pdf" --degrees 90 --pages "all"
```

In-place (overwrites input):

```powershell
python -m pdf_toolkit rotate pdf --pdf "in.pdf" --out_pdf "in.pdf" --degrees 180 --pages "1-5" --inplace --overwrite
```

### Rotate PNGs in a folder

```powershell
python -m pdf_toolkit rotate images --in_dir "out\pages" --glob "*.png" --degrees 90 --out_dir "out\pages_rot"
```

In-place (overwrites files):

```powershell
python -m pdf_toolkit rotate images --in_dir "out\pages" --glob "*.png" --degrees 90 --out_dir "out\pages" --inplace --overwrite
```

### Page-images (split spreads + crop)

Auto mode (split if wide enough, otherwise crop-only):

```powershell
python -m pdf_toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --glob "*.png" --mode auto --debug
```

Always split:

```powershell
python -m pdf_toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode split --overwrite
```

Never split (crop-only):

```powershell
python -m pdf_toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode crop
```

Optional printed-page number extraction (tesseract CLI in `PATH`):

```powershell
python -m pdf_toolkit page-images --in_dir "out\pages" --out_dir "out\pages_single" --mode auto --extract_page_numbers --page_num_debug
```

Notes:
- `--extract_page_numbers` is optional. This project does not require `pytesseract`.
- The feature uses the `tesseract` executable via subprocess.
- If `tesseract` is unavailable, processing still succeeds and manifest outputs include `printed_page: null` with `reason: "no_tesseract"`.

Recommended pipeline:
`render -> page-images -> ocr-obsidian`

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

When `page-images` runs with `--extract_page_numbers`, each action output includes OCR metadata:

```json
{
  "path": "out/pages_single/book_p0001_R.png",
  "printed_page": 123,
  "corner": "right",
  "raw_left": "",
  "raw_right": "123",
  "reason": null
}
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
python -m unittest discover -s src/pdf_toolkit -p "test_*.py"
```
