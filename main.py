# Requires Poppler's pdftotext utility (poppler-utils on Linux).

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml

from match_reconstructor import (
    load_match_json,
    reconstruct_match,
    write_reconstruction
)
from pdf_score_sheet import PdfScoreSheetParser
from rotation_analysis import (
    _report_stem,
    analyze_rotation_runs,
    load_point_by_point
)


def main(config_file="config.yaml", load_match=False, analyze_only=False):
    config = _load_config(config_file)
    points_output_file = config["point_by_point_json"]
    parser = PdfScoreSheetParser()
    pdf_team_order = parser.extract_team_header_order(config["pdf_file"])
    teams_by_name = {
        team["name"]: team
        for team in config["teams"]
    }
    configured_team_names = set(teams_by_name)
    pdf_team_names = set(pdf_team_order)
    if pdf_team_names != configured_team_names:
        missing_teams = sorted(pdf_team_names - configured_team_names)
        unexpected_teams = sorted(configured_team_names - pdf_team_names)
        raise ValueError(
            "The configured teams do not match the teams in the PDF header. "
            f"Missing from config: {missing_teams or 'none'}; "
            f"not found in PDF: {unexpected_teams or 'none'}."
        )
    teams_in_pdf_order = [
        teams_by_name[team_name]
        for team_name in pdf_team_order
    ]

    if not analyze_only:
        if load_match:
            match_data = load_match_json(config["match_json"])
        else:
            page = parser.extract_page(config["pdf_file"])
            match = parser.parse_match(page)
            Path(config["match_json"]).write_text(
                match.to_json() + "\n",
                encoding="utf-8"
            )
            match_data = load_match_json(config["match_json"])
            print(f"Saved match data to {config['match_json']}")

        if not match_data.get("match_date"):
            match_data["match_date"] = PdfScoreSheetParser().extract_match_date(
                config["pdf_file"]
            )
        reconstruction = reconstruct_match(match_data)
        write_reconstruction(reconstruction, points_output_file)
        print(f"Saved point-by-point reconstruction to {points_output_file}")

    point_data = load_point_by_point(points_output_file)
    if (
        not point_data.get("player_names")
        and Path(config["pdf_file"]).is_file()
    ):
        point_data["player_names"] = PdfScoreSheetParser().extract_player_names(
            config["pdf_file"]
        )
    if not point_data.get("match_date"):
        point_data["match_date"] = PdfScoreSheetParser().extract_match_date(
            config["pdf_file"]
        )
    analysis = analyze_rotation_runs(
        point_data,
        teams_in_pdf_order,
        config["analysis_output_dir"]
    )
    for team_result in analysis["teams"]:
        print(
            f"Saved rotation-run, service-run, and setter-point analyses for "
            f"{team_result['team']} to {config['analysis_output_dir']}"
        )
    report_stem = _report_stem(
        pdf_team_order,
        point_data["match_date"]
    )
    print(
        "Saved Markdown report to "
        f"{config['analysis_output_dir']}/{report_stem}.md"
    )
    print(
        "Saved PDF report to "
        f"{config['analysis_output_dir']}/{report_stem}.pdf"
    )


def _load_config(config_file):
    with Path(config_file).open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    schema_path = Path(__file__).with_name("config.schema.json")
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(config),
        key=lambda error: tuple(str(part) for part in error.absolute_path)
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(
            f"Invalid config {config_file!r} at {location}: {error.message}"
        )

    teams = config["teams"]
    team_names = [team["name"] for team in teams]
    if len(set(team_names)) != len(team_names):
        raise ValueError("Team names in config must be unique")

    normalized_config = {
        **config,
        "match_json": config.get("match_json", "match.json"),
        "point_by_point_json": config.get(
            "point_by_point_json",
            "point_by_point.json"
        ),
        "analysis_output_dir": config.get("analysis_output_dir", "analysis"),
        "teams": [
            {
                "name": team["name"],
                "setters": team.get("setters", [])
            }
            for team in teams
        ]
    }
    return normalized_config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="YAML config file containing input, output, and team settings"
    )
    parser.add_argument(
        "--load-match",
        action="store_true",
        help="Reconstruct points from the existing match JSON without parsing a PDF"
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Analyze the existing point-by-point JSON without parsing or replaying"
    )
    args = parser.parse_args()

    main(
        config_file=args.config,
        load_match=args.load_match,
        analyze_only=args.analyze_only
    )
