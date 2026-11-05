import json
from pathlib import Path
import subprocess
from typing import Any

from match_reconstructor import reconstruct_match
from pdf_score_sheet import PdfScoreSheetParser
from rotation_analysis import analyze_rotation_runs


def parse_setter_numbers(value: str) -> list[int]:
    if not value.strip():
        return []
    if len(value) > 120:
        raise ValueError(
            "Bitte geben Sie nur die Setter-Trikotnummern an, durch Kommas "
            "getrennt, zum Beispiel 4, 12."
        )

    setters = []
    for item in value.split(","):
        jersey_text = item.strip()
        if not jersey_text.isdecimal():
            raise ValueError(
                "Bitte geben Sie die Trikotnummern als durch Kommas "
                "getrennte positive Zahlen ein, zum Beispiel 4, 12. Das Feld "
                "kann leer bleiben, wenn die Setter unbekannt sind."
            )
        jersey = int(jersey_text)
        if jersey < 1:
            raise ValueError(
                "Setter-Trikotnummern müssen positive Zahlen sein."
            )
        if jersey in setters:
            raise ValueError(
                f"Die Setter-Trikotnummer {jersey} wurde mehrfach angegeben."
            )
        setters.append(jersey)
    return setters


def extract_team_header_order(pdf_file: str | Path) -> list[str]:
    pdf_path = Path(pdf_file)
    if not pdf_path.is_file():
        raise ValueError("Die ausgewählte PDF-Datei wurde nicht gefunden.")
    with pdf_path.open("rb") as pdf_file:
        prefix = pdf_file.read(1024)
    if b"%PDF-" not in prefix:
        raise ValueError(
            "Die Datei ist kein lesbares PDF. Bitte wählen Sie den "
            "Spielbericht als PDF-Datei aus."
        )

    team_order = PdfScoreSheetParser().extract_team_header_order(pdf_path)
    if len(team_order) != 2:
        raise ValueError(
            "Im PDF konnten nicht genau zwei Teams erkannt werden."
        )
    return team_order


def process_match_pdf(
    pdf_file: str | Path,
    setters_by_header_order: list[list[int]],
    output_dir: str | Path
) -> Path:
    if (
        not isinstance(setters_by_header_order, list)
        or len(setters_by_header_order) != 2
        or any(
            not isinstance(setters, list)
            for setters in setters_by_header_order
        )
    ):
        raise ValueError(
            "Bitte geben Sie die Setter-Nummern für beide Teams separat an."
        )

    for setters in setters_by_header_order:
        if (
            any(
                not isinstance(jersey, int)
                or isinstance(jersey, bool)
                or jersey < 1
                for jersey in setters
            )
            or len(set(setters)) != len(setters)
        ):
            raise ValueError(
                "Setter-Trikotnummern müssen eindeutige positive Zahlen sein."
            )

    pdf_path = Path(pdf_file)
    team_order = extract_team_header_order(pdf_path)
    parser = PdfScoreSheetParser()
    parsed_match = parser.parse_match(parser.extract_page(pdf_path))
    match_data = json.loads(parsed_match.to_json())
    point_data = reconstruct_match(match_data)
    teams: list[dict[str, Any]] = [
        {"name": team_name, "setters": setters}
        for team_name, setters in zip(
            team_order,
            setters_by_header_order,
            strict=True
        )
    ]
    output_path = Path(output_dir)
    analyze_rotation_runs(point_data, teams, output_path)

    reports = list(output_path.glob("report_*.pdf"))
    if len(reports) != 1:
        raise RuntimeError(
            "Expected the analysis to create exactly one PDF report"
        )
    return reports[0]


def pdf_processing_error(error: Exception) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return (
            "Das PDF konnte innerhalb der verfügbaren Zeit nicht gelesen "
            "werden. Bitte prüfen Sie, ob es sich um einen gültigen, "
            "ungeschützten Spielbericht handelt."
        )
    if isinstance(error, subprocess.CalledProcessError):
        message = (
            "Das PDF konnte nicht gelesen werden. Bitte verwenden Sie einen "
            "gültigen, ungeschützten Spielbericht im PDF-Format."
        )
        details = error.stderr or error.stdout
        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="replace")
        if details:
            return (
                f"{message}\n\nTechnische Details "
                f"(pdftotext, Exit-Code {error.returncode}):\n"
                f"{details.strip()}"
            )
        return (
            f"{message}\n\nTechnische Details: pdftotext wurde mit "
            f"Exit-Code {error.returncode} beendet. {error}"
        )
    if isinstance(error, FileNotFoundError):
        if error.filename != "pdftotext":
            return f"Datei konnte nicht geöffnet oder gespeichert werden: {error}"
        return (
            "Das Programm pdftotext wurde nicht gefunden. Bitte installieren "
            "Sie Poppler und stellen Sie sicher, dass pdftotext im PATH liegt."
        )
    return str(error)
