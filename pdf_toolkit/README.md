# PDF Toolkit (Local Windows CLI)

Small, local, lightweight PDF utilities for Windows. Everything runs offline
once dependencies are installed.

Features:
- Render PDF pages to PNGs (PyMuPDF)
- Split a PDF into multiple PDFs
- Rotate PDF pages or rotate PNGs (Pillow)
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

By default the manifest is written to:
- Render: `out_dir\manifest.json`
- Split: `out_dir\manifest.json`
- Rotate PDF: `out_pdf` folder\manifest.json
- Rotate images: `out_dir\manifest.json`

`--dry-run` skips writing the manifest (it is treated like an output file).

## Testing (lightweight)

Run the minimal unit tests:

```powershell
python -m unittest discover -s tests
```
