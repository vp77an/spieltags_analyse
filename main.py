# Requires Poppler's pdftotext utility (poppler-utils on Linux).

import argparse
from collections.abc import Mapping
from pathlib import Path

import yaml

from match_reconstructor import (
    load_match_json,
    reconstruct_match,
    write_reconstruction
)
from pdf_score_sheet import PdfScoreSheetParser
from rotation_analysis import analyze_rotation_runs, load_point_by_point


def main(config_file="config.yaml", load_match=False, analyze_only=False):
    config = _load_config(config_file)
    points_output_file = config["point_by_point_json"]

    if not analyze_only:
        if load_match:
            match_data = load_match_json(config["match_json"])
        else:
            parser = PdfScoreSheetParser()
            page = parser.extract_page(config["pdf_file"])
            match = parser.parse_match(page)
            Path(config["match_json"]).write_text(
                match.to_json() + "\n",
                encoding="utf-8"
            )
            match_data = load_match_json(config["match_json"])
            print(f"Saved match data to {config['match_json']}")

        reconstruction = reconstruct_match(match_data)
        write_reconstruction(reconstruction, points_output_file)
        print(f"Saved point-by-point reconstruction to {points_output_file}")

    point_data = load_point_by_point(points_output_file)
    analysis = analyze_rotation_runs(
        point_data,
        config["teams"],
        config["analysis_output_dir"]
    )
    for team_result in analysis["teams"]:
        print(
            f"Saved rotation-run, service-run, and setter-point analyses for "
            f"{team_result['team']} to {config['analysis_output_dir']}"
        )


def _load_config(config_file):
    with Path(config_file).open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, Mapping):
        raise ValueError("Config YAML must contain a mapping")

    required_paths = (
        "pdf_file",
        "match_json",
        "point_by_point_json",
        "analysis_output_dir"
    )
    for key in required_paths:
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f"Config must define a non-empty {key!r}")

    team_config = config.get("teams")
    if not isinstance(team_config, Mapping) or set(team_config) != {"A", "B"}:
        raise ValueError("Config 'teams' must define exactly teams A and B")

    normalized_teams = {}
    names = set()
    for team_code in ("A", "B"):
        team = team_config[team_code]
        if not isinstance(team, Mapping):
            raise ValueError(f"Config for team {team_code} must be a mapping")
        name = team.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Config for team {team_code} needs a team name")
        if name in names:
            raise ValueError("Team names in config must be unique")
        names.add(name)

        setters = team.get("setters", [])
        if setters is None:
            setters = []
        if not isinstance(setters, list) or any(
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            for number in setters
        ):
            raise ValueError(
                f"Setters for team {team_code} must be a list of jersey numbers"
            )
        if len(set(setters)) != len(setters):
            raise ValueError(f"Setter numbers for team {team_code} must be unique")
        normalized_teams[team_code] = {"name": name, "setters": setters}

    return {
        **config,
        "teams": normalized_teams
    }


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
