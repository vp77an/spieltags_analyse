# Requires Poppler's pdftotext utility (poppler-utils on Linux).

import argparse
import json
from pathlib import Path
from typing import Any

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
from video_scoreboard import (
    ScoreboardCellClassifier,
    ScoreboardCellRecognizer,
    HuggingFaceScoreboardRecognizer,
    ScoreboardChangeRecognizer,
    ScoreboardDigitClassifier,
    ScoreboardDigitRecognizer,
    ScoreProgressionGuide,
    TimeoutBannerDetector,
    TesseractScoreboardRecognizer,
    VideoScoreboardAnalyzer
)


def main(
    config_file=None,
    load_match=False,
    analyze_only=False,
    pdf_file=None,
    setters=None
) -> bool:
    parser = PdfScoreSheetParser()
    if pdf_file is not None:
        pdf_team_order = parser.extract_team_header_order(pdf_file)
        setter_lists = _validate_setter_lists(setters, len(pdf_team_order))
        config = {
            "pdf_file": str(pdf_file),
            "match_json": "match.json",
            "point_by_point_json": "point_by_point.json",
            "analysis_output_dir": "analysis",
            "teams": [
                {"name": name, "setters": team_setters}
                for name, team_setters in zip(pdf_team_order, setter_lists)
            ]
        }
        config_file = _write_generated_config(config, pdf_file)
        print(f"Saved configuration to {config_file}")
    else:
        config = _load_config(config_file or "input/config.yaml")
        pdf_team_order = parser.extract_team_header_order(config["pdf_file"])

    points_output_file = config["point_by_point_json"]
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
        "Saved PDF report to "
        f"{config['analysis_output_dir']}/{report_stem}.pdf"
    )
    if "video_scoreboard" in config:
        return _run_video_scoreboard(
            config["video_scoreboard"],
            point_data
        )
    return True


def _load_config(config_file):
    config_path = Path(config_file)
    with config_path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    schema_path = config_path.with_name("config.schema.json")
    if not schema_path.is_file():
        schema_path = Path(__file__).parent / "input" / "config.schema.json"
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
            f"Invalid config {str(config_path)!r} at {location}: {error.message}"
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
    if "video_scoreboard" in config:
        video_config = config["video_scoreboard"]
        scoreboard_format = video_config.get("scoreboard_format", "bvv")
        training_dir = (
            Path(__file__).parent / "training_data" / "scoreboard"
            / scoreboard_format
        )
        normalized_config["video_scoreboard"] = {
            **video_config,
            "backend": video_config.get("backend", "image_changes"),
            "scoreboard_format": scoreboard_format,
            "timeout_template_file": video_config.get(
                "timeout_template_file",
                str(training_dir / "timeout_banner_template.png")
            ),
            "output_file": video_config.get(
                "output_file",
                "analysis/score_progression.csv"
            ),
            "sample_interval_seconds": video_config.get(
                "sample_interval_seconds",
                0.5
            ),
            "start_time_seconds": video_config.get(
                "start_time_seconds",
                0.0
            ),
            "stable_observations": video_config.get(
                "stable_observations",
                2
            ),
            "crop_region": tuple(
                video_config.get("crop_region", [0, 0, 0.6, 0.35])
            ),
            "scoreboard_region": (
                tuple(video_config["scoreboard_region"])
                if video_config.get("scoreboard_region") is not None
                else None
            )
        }
        if (
            normalized_config["video_scoreboard"]["backend"] == "cell_classifier"
            and "classifier_file" not in video_config
        ):
            normalized_config["video_scoreboard"]["classifier_file"] = str(
                training_dir / "scoreboard_cell_templates.json"
            )
    return normalized_config


def _run_video_scoreboard(
    video_config: dict[str, Any],
    point_data: dict[str, Any]
) -> bool:
    backend = video_config["backend"]
    if backend == "image_changes":
        recognizer = ScoreboardChangeRecognizer(
            scoreboard_region=video_config["scoreboard_region"]
        )
    elif backend == "digit_classifier":
        recognizer = ScoreboardDigitRecognizer(
            ScoreboardDigitClassifier.load(video_config["classifier_file"]),
            scoreboard_region=video_config["scoreboard_region"]
        )
    elif backend == "cell_classifier":
        recognizer = ScoreboardCellRecognizer(
            ScoreboardCellClassifier.load(video_config["classifier_file"]),
            scoreboard_region=video_config["scoreboard_region"]
        )
    elif backend == "tesseract":
        recognizer = TesseractScoreboardRecognizer(
            scoreboard_region=video_config["scoreboard_region"] or (
                0.002,
                0.069,
                0.162,
                0.158
            )
        )
    elif backend == "transformers":
        recognizer = HuggingFaceScoreboardRecognizer(
            video_config["model_path"],
            crop_region=video_config["crop_region"]
        )
    else:
        raise ValueError(f"Unsupported video scoreboard backend: {backend}")

    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        recognizer,
        sample_interval_seconds=video_config["sample_interval_seconds"],
        stable_observations=video_config["stable_observations"],
        progression_guide=guide,
        start_time_seconds=video_config["start_time_seconds"],
        timeout_detector=TimeoutBannerDetector(
            video_config["timeout_template_file"],
            video_config["scoreboard_region"]
        )
    )
    print(
        f"Analyzing video from {video_config['start_time_seconds']:.1f}s; "
        f"confirmed events will be written to {video_config['output_file']}.",
        flush=True
    )
    changes = analyzer.analyze(
        video_config["video_file"],
        video_config["output_file"]
    )
    timeout_count = sum(
        event.event_type == "timeout_start"
        for event in analyzer.timeout_events
    )
    uncertain_boundaries = sum(
        event.timestamp_seconds is None
        for event in analyzer.timeout_events
    )
    print(
        f"Timeout banners: {timeout_count} detected; "
        f"{uncertain_boundaries} boundaries need review. "
        "Undetected banners cannot be counted."
    )
    expected_rallies = guide.total_rallies
    if backend == "image_changes":
        print(
            "Video scoreboard analysis: FAILURE - image-change detection "
            f"observed {len(changes)} candidate transitions for "
            f"{expected_rallies} expected rallies, but this backend does not "
            "classify the displayed scores and cannot verify completion. "
            "Use a score-reading backend to confirm the rally sequence."
        )
        return False

    if (
        len(changes) == expected_rallies
        and not guide.unverified_rally_numbers
    ):
        print(
            "Video scoreboard analysis: SUCCESS - matched and timestamped "
            f"all {expected_rallies} expected rallies, including any inferred "
            f"set-reset transitions. Output: {video_config['output_file']}."
        )
        return True

    print(
        "Video scoreboard analysis: FAILURE - matched and timestamped "
        f"{len(changes)} of {expected_rallies} expected rallies, including "
        "any inferred set-reset transitions. "
        f"Results with placeholder timestamps are in {video_config['output_file']}."
    )
    if guide.unverified_rally_numbers:
        unverified_numbers = ", ".join(
            str(number)
            for number in guide.unverified_rally_numbers
        )
        print(
            "The analyzer did not verify the score or timestamp for rally "
            f"number(s): {unverified_numbers}."
        )
    return False


def _validate_setter_lists(setters: Any, team_count: int) -> list[list[int]]:
    if setters is None:
        return [[] for _ in range(team_count)]
    if (
        not isinstance(setters, list)
        or len(setters) != team_count
        or any(not isinstance(team_setters, list) for team_setters in setters)
    ):
        raise ValueError(
            f"Setters must be a list of {team_count} jersey-number lists, "
            "in the order teams appear in the PDF header"
        )
    for team_setters in setters:
        if (
            any(
                not isinstance(jersey, int)
                or isinstance(jersey, bool)
                or jersey < 1
                for jersey in team_setters
            )
            or len(set(team_setters)) != len(team_setters)
        ):
            raise ValueError(
                "Setter jersey numbers must be unique positive integers"
            )
    return setters


def _parse_setters_argument(values: list[str]) -> list[list[int]]:
    try:
        setters = json.loads(" ".join(values))
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(
            "setters must be JSON arrays, for example "
            "'[[16,13], [8,11]]' or '[[], [8,11]]'"
        ) from error
    try:
        return _validate_setter_lists(setters, 2)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _write_generated_config(config: dict[str, Any], pdf_file: str | Path) -> Path:
    input_dir = Path.cwd() / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    config_path = input_dir / f"config_{Path(pdf_file).stem}.yaml"
    try:
        with config_path.open("x", encoding="utf-8") as file:
            yaml.safe_dump(
                config,
                file,
                allow_unicode=True,
                sort_keys=False
            )
    except FileExistsError as error:
        raise FileExistsError(
            f"Refusing to overwrite existing config {config_path}; "
            "use that config with --config to repeat its analysis"
        ) from error
    return config_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--config",
        default="input/config.yaml",
        help="YAML config file containing input, output, and team settings"
    )
    input_group.add_argument(
        "--file",
        help="Score-sheet PDF; team names are read from its header"
    )
    parser.add_argument(
        "--setters",
        nargs="+",
        metavar="JSON",
        help=(
            "Two setter jersey-number lists in PDF team-header order; use [] "
            "for a team with unknown setters"
        )
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

    if args.setters and not args.file:
        parser.error("--setters requires --file")

    raise SystemExit(
        0 if main(
            config_file=args.config,
            load_match=args.load_match,
            analyze_only=args.analyze_only,
            pdf_file=args.file,
            setters=(
                _parse_setters_argument(args.setters)
                if args.setters
                else None
            )
        ) else 1
    )
