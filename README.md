# Spieltagsanalyse

Parses match and set information from formatted SAMS volleyball score-sheet PDFs.
The parser reads the PDF's embedded text and its positions; it does not use OCR.
Matches are parsed through the winning third set; unplayed set panels are
ignored. For a 2-2 match, it identifies all three fifth-set panels from their
headers and merges the two side-switch panels for the same team.

## Requirements

- Python 3.10 or newer
- Poppler's `pdftotext` command (`poppler-utils` on Linux)

Install the Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Parse the included report:

```bash
python main.py
```

The default [config.yaml](./config.yaml) names the PDF, match JSON, point-by-point
JSON, analysis output directory, team names, and setters. Use `--config` to load
another YAML config. Each team's `setters` is an ordered list of jersey numbers;
`header_order` must list both team names from left to right as they appear in
the PDF match header. The program checks this order before parsing and reports
the configured and detected orders if they differ.
The first listed setter is the primary setter, and an empty list means the
setter is unknown. If multiple listed setters are on court at once, the first
listed is treated as the setter. When no setter is configured, rotation
analysis uses the six jersey numbers on court instead of assigning Laeufer
positions.

Point-by-point reconstruction is also saved as `point_by_point.json` by
default. It is generated from the saved `match.json` data. To rerun only the
reconstruction without reading the PDF, use:

```bash
python main.py --load-match
```

The analysis reads that generated `point_by_point.json`. It groups runs of
consecutive opponent points by the rotation the team remained in, and saves a
PNG distribution plot for each team plus the underlying counts and runs as
`rotation_run_distributions.json` in the configured analysis directory. When a
setter is configured, the legend identifies the active setter's position as
`Laeufer I` through `Laeufer VI`.

To rerun only the analysis from the existing point-by-point JSON, use
`python main.py --analyze-only`.

The analysis also saves a service-run distribution plot per team, grouped by
that team's setter position, plus `service_run_distributions.json`. A service
run is the number of points won by a team during one uninterrupted turn
serving; a turn that ends on its first rally is recorded as a zero-point run.
Finally, each team gets a grouped bar plot comparing its own points and the
opponent's points by the team's setter position. The underlying totals are
saved in `points_by_setter_position.json`. Points for which no configured
setter is on court are included under `Unknown`.

Each analysis run also creates Markdown and PDF reports in the analysis output
directory. Both filenames include the two team names and match date, for
example `report_tsv_ansbach_vs_sv_hahnbach_2026-10-17.md` and the matching
`.pdf`.
Its **Performance per Set of Players** section groups both teams' points by
the exact six-player lineup on court and sorts lineups by team-points-to-
opponent-points ratio. Player first names and last-name initials are read from
the score-sheet roster when available; otherwise the report shows jersey
numbers alone. A zero opponent-point count is shown as an infinite ratio.
The **Performance per Player** section lists, for each team, points played
(each rally while the player was on court), points won and lost in those
rallies, and the won/lost ratio, sorted from highest to lowest ratio. A player
with no lost points is shown with an infinite ratio.
The **Length of Service Runs** section embeds the generated run and setter-
position plots and summarizes, for each team, the best and worst rotations
for own service runs and opponent scoring runs.

## TODO:

- Handling of U14 (4 players)
