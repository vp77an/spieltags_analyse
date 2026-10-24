# Spieltagsanalyse

Parses match and set information from formatted SAMS volleyball score-sheet PDFs.
The parser reads the PDF's embedded text and its positions; it does not use OCR.

## Requirements

- Python 3.10 or newer
- Poppler's `pdftotext` command (`poppler-utils` on Linux)

Install the Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Parse the included report:

```bash
python main.py Spielbericht_ANS_HAHNBACH.pdf
```

The extracted positioned text is cached in `pdf_text.json` by default. Load a
previously saved cache without reparsing the PDF with:

```bash
python main.py --load-cache
```

Choose another cache path with `--cache-file`.

## TODO:

- Handling of 5th set
- Handling of U14 (4 players)
