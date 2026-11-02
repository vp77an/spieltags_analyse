# Spieltagsanalyse

Parses match and set information from formatted SAMS volleyball score-sheet PDFs.
The parser reads the PDF's embedded text and its positions; it does not use OCR.
Regular matches are parsed through the set that gives a team its third win;
unplayed set panels are ignored. For a 2-2 match, the parser identifies all
three fifth-set panels from their headers and merges the two side-switch panels
for the same team. U14 and U16/U18/U20 matches are detected from their
age-grade markers in the PDF header and end when a team wins two sets.

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

Parse the included report:

```bash
python main.py
```

To analyze a different score sheet without preparing a YAML config, pass the
PDF directly. Team names are extracted from its header; setter lists must be in
that same order. Use an empty list for a team whose setters are unknown or not
of interest:

```bash
python3 main.py --file Spielbericht_ANS_HAHNBACH.pdf \
  --setters '[[8, 11], [2]]'
```

The script saves a reusable `config_<PDF-name>.yaml` file in the current
directory along with the analysis. It will not overwrite an existing config;
to repeat an analysis, pass the generated file with `--config`.

Alternatively, the default [config.yaml](./config.yaml) or another config can
name the PDF and teams. Use `--config` to load a different YAML config. The
program identifies each team from the PDF header and matches it to the
configured team by name, so team entries may be listed in any order. Each
team's `setters` is an ordered list of jersey numbers:

```yaml
# yaml-language-server: $schema=./config.schema.json
pdf_file: match.pdf
teams:
  - name: TSV Ansbach
    setters: [8, 11]
  - name: DJK Augsburg Hochzoll
    setters: [16, 13]
```

Config files are validated against [config.schema.json](./config.schema.json).
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

Each analysis run also creates Markdown and PDF reports in the analysis output
directory. Both filenames include the two team names and match date, for
example `report_tsv_ansbach_vs_sv_hahnbach_2026-10-17.md` and the matching
`.pdf`. At the beginning, both reports show the match result in a table:
team names on the first row, sets won on the second, and all set scores in a
joint third row. They also caution that the
simplified analyses are not a substitute for full scouting; for example,
points scored during a player's serve are not necessarily aces by that player.
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
