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

The parsed match is saved as `match.json` by default. Choose another output
path with `--output`. The extracted positioned text is cached in `pdf_text.json`
by default. Load a previously saved cache without reparsing the PDF with:

```bash
python main.py --load-cache --output match.json
```

Choose another cache path with `--cache-file`.

Point-by-point reconstruction is also saved as `point_by_point.json` by
default. It is generated from the saved `match.json` data. To rerun only the
reconstruction without reading the PDF, use:

```bash
python main.py --load-match
```

Use `--points-output` to select another reconstruction file. Each point
records both teams' rotations in positions I through VI at the start of the
rally, the serving and scoring teams, and the score after that point.
Substitutions are applied at their recorded score, interpreted from the
perspective of the team making the substitution.

## TODO:

- Handling of 5th set
- Handling of U14 (4 players)
