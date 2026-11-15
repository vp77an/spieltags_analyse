# Spieltagsanalyse

Parses match and set information from formatted SAMS volleyball score-sheet PDFs.
The parser reads the PDF's embedded text and its positions; it does not use OCR.
Regular matches are parsed through the set that gives a team its third win;
unplayed set panels are ignored. For a 2-2 match, the parser identifies all
three fifth-set panels from their headers and merges the two side-switch panels
for the same team. U14 and U16/U18/U20 matches are detected from their
age-grade markers in the PDF header and end when a team wins two sets.

The analysis also saves a service-run distribution plot per team, grouped by
that team's setter position, plus `service_run_distributions.json`. A service
run is the number of points won by a team during one uninterrupted turn
serving; a turn that ends on its first rally is recorded as a zero-point run.
Finally, each team gets a grouped bar plot comparing its own points and the
opponent's points by the team's setter position. The underlying totals are
saved in `points_by_setter_position.json`. Points for which no configured
setter is on court are included under `Unknown`.

For U14 matches, opponent scoring runs are grouped by the exact own rotation,
with each player's current position shown in the legend. Player names and last
name initials are included when roster information is available. Own service
runs are shown as an aggregate distribution rather than compared by rotation.
The setter-position chart and `points_by_setter_position.json` are not
generated for U14 matches.

Each analysis run creates a PDF report in the analysis output directory; its
filename includes the two team names and match date, for example
`report_tsv_ansbach_vs_sv_hahnbach_2026-10-17.pdf`. During generation, a
Markdown report and PNG charts are created as intermediates and removed after
the PDF is successfully generated. At the beginning, the report shows the
match result in a table:
team names on the first row, sets won on the second, and all set scores in a
joint third row. The report then shows a caution and table of contents, followed
by a **Set Progression Point-by-Point** overview. It shows each rally in order, the
updated score on the row of the team that won it, and a `T` at each timeout's
recorded score on the row of the team that called it. Timeout scores are
interpreted from that team's perspective. Long sets continue in labeled table
segments. The report cautions that the simplified analyses are not a substitute
for full scouting; for example, points scored during a player's serve are not
necessarily aces by that player.
Its **Performance per Set of Players** section groups both teams' points by
the exact lineup on court and sorts lineups by team-points-to-
opponent-points ratio. Player first names and last-name initials are read from
the score-sheet roster when available; otherwise the report shows jersey
numbers alone. A zero opponent-point count is shown as an infinite ratio.
The **Stats per Player** section lists, for each team, points played
(each rally while the player was on court), points won and lost in those
rallies, and the won/lost ratio, sorted from highest to lowest ratio. A player
with no lost points is shown with an infinite ratio.
The **Stats per Serving Player** section lists, for each player who served,
the number of rallies served, the number of opponent points scored during
those rallies, and the opponent-points-per-serve rate. The serving team's own
points are not included in the opponent-points count; players are sorted from
the lowest rate to the highest.
The **Length of Service Runs** section embeds the generated run and setter-
position plots and summarizes, for each team, the best and worst rotations
for own service runs and opponent scoring runs.

The PDF regression fixtures are in [tests/](./tests/). The `3-set`, `4-set`,
and `5-set` folders contain adult regular-match PDFs; `u14` and `u14-tiebreak`
contain four-player U14 matches, and `u18` and `u18-tiebreak` contain U18
matches, including a third-set tiebreak. The `3-set`, `4-set`, `5-set`, `u14`,
and `u14-tiebreak` directories include reference `match.json` and
`point_by_point.json` outputs, plus references for the generated
`rotation_run_distributions.json`, `service_run_distributions.json`, and
`points_by_setter_position.json` analysis results.

U14 matches use four players per team, are played as best-of-three sets, and
use a 25-point target for the first two sets and 15 points for a deciding set.
All sets require a two-point lead. The set header's `S`/`R` marks identify the
opening server; the serving team's position-I player serves first. After a
team gains service, it rotates before serving. During a service turn it rotates
after scoring two points; zero or one point ends the turn without that
additional rotation. Position III is treated as the setter position for U14
analysis.

U16, U18, and U20 matches use the regular six-player rotation and rally rules,
but are best of three sets: the first team to win two sets wins the match.
The first two sets use a 25-point target; the third, deciding set uses a
15-point target, with a two-point lead required in every set.

Parse the default input config:

```bash
python main.py
```

To analyze a different score sheet without preparing a YAML config, pass the
PDF directly. Team names are extracted from its header; setter lists must be in
that same order. Use an empty list for a team whose setters are unknown or not
of interest. Keep source score sheets and reusable configs in `input/`, and
include that directory in the command-line path:

```bash
python3 main.py --file input/Spielbericht_ANS_HAHNBACH.pdf \
  --setters '[[8, 11], [2]]'
```

The script saves a reusable `input/config_<PDF-name>.yaml` file. It will not
overwrite an existing config; to repeat an analysis, pass the generated file
with `--config`:

```bash
python3 main.py --config input/config_<PDF-name>.yaml
```

Alternatively, [`input/config.yaml`](./input/config.yaml) or another config in
`input/` can name the PDF and teams. Use `--config input/<config-name>.yaml` to
load a different YAML config. The program identifies each team from the PDF
header and matches it to the configured team by name, so team entries may be
listed in any order. Each team's `setters` is an ordered list of jersey numbers:

```yaml
# yaml-language-server: $schema=./config.schema.json
pdf_file: input/match.pdf
teams:
  - name: TSV Ansbach
    setters: [8, 11]
  - name: DJK Augsburg Hochzoll
    setters: [16, 13]
```

Config files are validated against
[input/config.schema.json](./input/config.schema.json).
The YAML language-server schema directive at the top of each example config
also enables editor validation.
`pdf_file` and `teams` are required; the teams list must contain exactly two
teams. `setters` may be omitted when unknown. The optional `match_json`,
`point_by_point_json`, and `analysis_output_dir` fields default to `match.json`,
`point_by_point.json`, and `analysis`, respectively.
The first listed setter is the primary setter, and an empty list means the
setter is unknown. If multiple listed setters are on court at once, the first
listed is treated as the setter. When no setter is configured, rotation
analysis uses the jersey numbers on court instead of assigning Laeufer
positions. When setters are configured, both reports also list their starting
rotation positions set by set and whether each team starts on its own serve or
reception.

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

## Video scoreboard extraction

The `cell_classifier` backend classifies each of the four visible score cells
using OCR-free, whole-cell image templates trained on labeled video frames.
It reads the score independently, then uses the point-by-point reconstruction
to align readings with the match. After a longer gap it can resynchronize with
a later score; this does not verify the intervening rallies. The scoreboard's
top and bottom teams must appear in the same order as in the PDF match header.
Serving-team names in the CSV come from the match reconstruction, not from
independent recognition of the service icon.

If a non-final set jumps directly from its last-but-one score to the next
set's 0:0, the reset time is used for the set-winning rally, provided the
preceding score and reset are read in consecutive samples. That CSV row uses
the set counters from before the reset and the final points from the report.
If the transition cannot be established, the timestamp remains a placeholder.

Confirmed scores are added to the CSV as they are found. Each unverified
rally gets a row in match order with `hh:mm:ss.ms` in the `timestamp` column
and needs manual review;
they are written when a later score is confirmed or when processing ends.
The cell classifier also uses a placeholder if a score can be read but its
change was not bracketed by readable consecutive samples (for example, when
analysis starts after the score is already displayed). The final status is
failure unless every rally has a verified score and timestamp.
The times are sampled estimates (normally within one sampling interval), and
template classification can still make mistakes; review suspicious results.

Each rally row also has `start_timestamp` and `end_timestamp`. The end is the
same scoreboard-change time as `timestamp`, which is retained for compatibility.
The start is an approximation: the preceding
rally's end, or the end of a subsequent timeout. It is not a detected whistle.
The first rally's start and boundaries following an unverified rally or
timeout are placeholders (`hh:mm:ss.ms`).

### Timeout banners

Timeout detection is always enabled and compares a cropped image of the
timeout **label** visually; it does not use OCR. By default it uses the static
[`Auszeit` label template](./training_data/scoreboard/bvv/timeout_banner_template.png)
cropped from the supplied example; it is reused, not extracted from each video.
A different scoreboard style or language may require its own
template. Blue extensions with different labels, such as `Satzball`, are not
timeouts.

The CSV has an `event_type` column: `rally` or `timeout`. Each timeout occupies
one row ordered by its start, with its two boundaries in `start_timestamp` and
`end_timestamp`; its `timestamp` field is empty because it is an interval.
For timeout rows, `timeout_team` identifies the team whose scoreboard row
contains the banner; the score and serving-team columns are empty. Start and
end refer to the banner appearing and disappearing, not necessarily the
precise start and end of the actual stoppage. They have the same sampling
precision as rally timestamps. An unreadable or out-of-video boundary is
written as `hh:mm:ss.ms` and needs review. A timeout currently in progress
also has an `hh:mm:ss.ms` end until the boundary is confirmed.
The detector can miss a banner that is too short, occluded, or unlike the
template; a successful rally analysis does not prove that all timeouts were
detected.

Confirmed rally and timeout events are written during processing, with
approximate rally starts calculated from the events known so far. The CSV is
atomically replaced after each confirmed event; later observations can revise
the ordering or a start boundary. If processing is interrupted, the CSV
contains the events confirmed up to that point, not the full match.
Placeholder rows retain their match order, but their exact position relative
to nearby timed events cannot be established.

Install the optional video dependencies:

```bash
python -m pip install -r requirements-video.txt
```

Train the classifier from labeled samples. Each CSV row identifies a timestamp
and the visible scores in top-to-bottom scoreboard order:

```csv
timestamp_seconds,top_sets,top_points,bottom_sets,bottom_points
422,0,0,0,0
423,0,0,0,1
```

```bash
python train_video_scoreboard_classifier.py \
  --video input/D3_Ansbach_Altdorf/TSV\ Ansbach3_\ TV\ Altdorf3_17.10.2026.mp4 \
  --labels input/D3_Ansbach_Altdorf/scoreboard_cell_training_samples.csv \
  --model training_data/scoreboard/bvv/scoreboard_cell_templates.json \
  --whole-cells
```

For another scoreboard style, provide independently checked examples of the
displayed set and point values, including both rows and multiple appearances
where possible, and train a separate model. The example templates cover
scores seen in the sample frames, not every possible score: missing or
ambiguous values may still need manual review. The detector is designed for
the sample's two-row blue scoreboard in the upper-left; it is not a general
scoreboard locator. It re-detects bounds for each sampled frame, keeps the
crop stable across small detection jitters, ignores a blue timeout extension
on one row, and reuses the last detected crop if detection temporarily
fails. `scoreboard_region` can be set to a tight
normalized `[left, top, right, bottom]` crop if automatic detection fails.
Training includes nearby crop variants so small differences in automatically
detected bounds do not necessarily require a new model.
If a stable crop is unreadable, a nearby detected crop may be used only when
its complete reading agrees with the readable cells in the stable crop and
other complete nearby readings that agree with those cells do not conflict.

The older `digit_classifier` backend remains available with its own digit
model and training command (omit `--whole-cells`). The legacy `image_changes`
backend only detects changes in the expected score
cell. It does not classify the digits and therefore cannot report a verified
success, even if its candidate count happens to equal the rally count. The
legacy `tesseract` and optional `transformers` backends remain available; this
classifier does not use either of them.

Video analysis is optional. Add a `video_scoreboard` section to a normal
match config to run it after the required PDF has been parsed and its
point-by-point reconstruction generated:

```yaml
pdf_file: input/configured_match.pdf
teams:
  - name: TSV Ansbach III
  - name: TV Altdorf III

video_scoreboard:
  backend: cell_classifier
  video_file: input/D3_Ansbach_Altdorf/TSV Ansbach3_ TV Altdorf3_17.10.2026.mp4
  scoreboard_format: bvv
  output_file: analysis/score_progression.csv
  start_time_seconds: 422
  sample_interval_seconds: 1.0
  stable_observations: 2
```

`scoreboard_format` defaults to `bvv` and selects assets in
`training_data/scoreboard/<format>/`: `scoreboard_cell_templates.json` for
the cell classifier and `timeout_banner_template.png` for timeout detection.
The supplied BVV assets were trained or cropped from the example scoreboard;
they may not work for every BVV overlay. For another format, provide both
assets in its folder, or override their paths with `classifier_file` and
`timeout_template_file`. Missing assets produce an error. Format names may
contain only letters, digits, underscores, and hyphens.

`start_time_seconds` skips stream preroll before the match starts and defaults
to `0`. It does not shift the timestamps in the CSV; they remain relative to
the beginning of the stream. The sample config starts at 422 seconds (07:02).
The CSV header is created when analysis begins and confirmed events are
written as processing continues, so the file can be monitored during the run.

The point-by-point reconstruction guides the optional video pass: accepted
scores must occur in the expected match sequence and use the reconstruction's
team names and ordering. The PDF report is still generated without video when
this section is omitted.

## Requirements

- Python 3.10 or newer
- Poppler's `pdftotext` command (`poppler-utils` on Linux)

Install the Python dependencies with:

```bash
python -m pip install -r requirements.txt
```

Install test dependencies and run the test suite with:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Desktop application

Start the native desktop interface with:

```bash
python desktop_app.py
```

Choose a score-sheet PDF, optionally enter each team's setter jersey numbers
in the order the teams appear in the PDF header, and start the analysis. The
application asks where to save the generated report. Leave setter fields empty
when the setters are unknown or for U14 matches. PDF processing requires
Poppler's `pdftotext` command to be installed and available on `PATH`.

### Build the Windows executable

Build on Windows itself (not in a Linux/WSL terminal), from the repository root.
You need:

- 64-bit Python 3.12 installed for Windows, including Tcl/Tk and the `py`
  launcher.
- A **standalone** Poppler for Windows build, which provides `pdftotext.exe`.
  The MiKTeX version is not suitable: it depends on an initialized MiKTeX
  installation on the recipient's machine. Download a packaged Windows build
  from [Poppler for Windows releases](https://github.com/oschwartz10612/poppler-windows/releases/latest),
  extract it, and set `POPPLER_BIN` to its `Library\bin` folder as shown below.
- An internet connection for the first build, so pip can install the build
  dependencies.

Open **PowerShell** and switch to the repository folder (adjust the path if
yours is elsewhere):

```powershell
cd C:\Users\volke\code\spieltags_analyse
py -3.12 --version
py -3.12 -c "import tkinter; print('Tkinter is available')"
```

Set the extracted standalone Poppler `bin` folder for the current PowerShell
window (replace the example path with the actual extracted folder):

```powershell
$env:POPPLER_BIN = "C:\path\to\poppler\Library\bin"
Test-Path "$env:POPPLER_BIN\pdftotext.exe"
```

The check should print `True`. If `POPPLER_BIN` is not set, the script uses
`pdftotext.exe` from `PATH`; it rejects MiKTeX-linked versions instead of
building an executable that depends on the recipient's MiKTeX setup.

Then build:

```powershell
.\build_windows.ps1
```

If PowerShell blocks the script, allow it just for the current PowerShell
window and run it again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\build_windows.ps1
```

The script creates a local `.build-venv` the first time, installs the
application and packaging dependencies, and builds the single-file GUI
executable. It bundles `pdftotext.exe` and its required DLLs. After making
changes to the application, run the same `.\build_windows.ps1` command again;
the build environment is reused and required dependencies are checked and
installed. The finished file is `dist\Spieltaganalyse.exe`. Copy that
executable to distribute it; recipients do not need Python, Tkinter, or
Poppler installed.
Close any running copy of Spieltaganalyse before rebuilding so Windows can
replace the existing executable.

For an existing executable that reports a MiKTeX setup error, rebuild it with
standalone Poppler and redistribute the new executable. Installing MiKTeX on
the recipient's computer is not required.

The build is not code-signed, so Windows SmartScreen may show a warning when
someone runs a newly built copy.


## Web upload service (Google Cloud Run)

The optional web service accepts a score-sheet PDF and setter jersey numbers,
then returns the generated report PDF. Each upload is processed in its own
temporary directory; uploaded files and generated intermediates are not
persisted. The upload limit defaults to 15 MiB.

Before deployment, create two Google Secret Manager secrets named
`spieltaganalyse-username` and `spieltaganalyse-password` and set their values
in the Cloud Console. Do not put credentials in the repository or deployment
command. Create a dedicated runtime service account and grant it access to
both secrets:

```bash
gcloud iam service-accounts create spieltaganalyse-runtime

gcloud secrets add-iam-policy-binding spieltaganalyse-username \
  --member="serviceAccount:spieltaganalyse-runtime@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding spieltaganalyse-password \
  --member="serviceAccount:spieltaganalyse-runtime@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Replace `PROJECT_ID` with the Google Cloud project ID. Build and deploy the
container from the repository root (requires an enabled Google Cloud project,
billing, and the `gcloud` CLI):

```bash
gcloud run deploy spieltaganalyse \
  --source . \
  --region europe-west3 \
  --service-account spieltaganalyse-runtime@PROJECT_ID.iam.gserviceaccount.com \
  --set-secrets AUTH_USERNAME=spieltaganalyse-username:latest,AUTH_PASSWORD=spieltaganalyse-password:latest \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 1 \
  --max 2 \
  --timeout 300
```

The service URL printed after deployment is used as the form action. In
WordPress, create or edit the page at
`https://www.tsv1860ansbach.de/volleyball/spieltagsanalyse`, add a **Custom
HTML** block, and paste in
[wordpress/spieltagsanalyse-form.html](./wordpress/spieltagsanalyse-form.html).
Replace `https://REPLACE-WITH-CLOUD-RUN-URL/process` with the deployed service
URL followed by `/process`. This native multipart form posts directly to
Cloud Run and downloads either the analysis report or a PDF containing the
error explanation; it does not depend on an iframe, JavaScript, or a WordPress
plugin. The service uses HTTP Basic authentication over HTTPS; the browser
prompts for the shared username and password when the form is submitted. The
service also hosts the same form at its `/` URL for direct access.

Setter fields correspond to the first and second teams named in the PDF header.
Enter jersey numbers separated by commas (for example `4, 12`), or leave a
field empty if the setter is unknown or for U14. The service responds with an
error PDF when the input is invalid, the match cannot be reconstructed, or an
unexpected processing error occurs.

Cloud Run allows unauthenticated invocation so the browser can reach the
application-level login challenge; the form and processing endpoint both
require valid credentials, and the service returns `503` if the secrets are
not configured. The health endpoint exposes only service readiness. The
shared credentials should be distributed only to intended users and rotated
by adding new Secret Manager versions and deploying a new Cloud Run revision.
Use a strong, unique password. Basic authentication is a simple
shared gate, not per-user access control or rate limiting: anyone with the
credentials can use the tool, so keep the upload size, single-request
concurrency, and maximum instance count bounded and monitor Cloud Run usage
and costs.
