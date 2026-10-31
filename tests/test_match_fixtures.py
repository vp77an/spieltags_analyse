import json
from pathlib import Path

import pytest

from match_reconstructor import reconstruct_match
from pdf_score_sheet import PdfScoreSheetParser
import rotation_analysis


FIXTURE_ROOT = Path(__file__).parent
FIXTURES = (
    (
        3,
        [
            {"name": "TV Planegg Krailling II", "setters": []},
            {"name": "TV/DJK Hammelburg", "setters": [4, 22]},
        ]
    ),
    (
        4,
        [
            {"name": "TV Bad Grönenbach", "setters": []},
            {"name": "DJK Augsburg Hochzoll", "setters": [16, 7]},
        ]
    ),
    (
        5,
        [
            {"name": "DJK Augsburg Hochzoll", "setters": [16, 13]},
            {"name": "TSV Ansbach", "setters": [8, 11]},
        ]
    )
)
ADULT_ANALYSIS_JSON_FILES = (
    "rotation_run_distributions.json",
    "service_run_distributions.json",
    "points_by_setter_position.json",
)
U14_ANALYSIS_JSON_FILES = (
    "rotation_run_distributions.json",
    "service_run_distributions.json",
)


@pytest.mark.parametrize(("set_count", "teams"), FIXTURES)
def test_pdf_match_and_reconstruction_match_reference(
    set_count: int,
    teams: list[dict[str, object]]
) -> None:
    fixture_dir = FIXTURE_ROOT / f"{set_count}-set"
    parser = PdfScoreSheetParser()

    parsed_match = parser.parse_match(
        parser.extract_page(fixture_dir / "match.pdf")
    )
    actual_match = json.loads(parsed_match.to_json())
    with (fixture_dir / "match.json").open(encoding="utf-8") as file:
        expected_match = json.load(file)

    assert len(actual_match["sets"]) == set_count
    assert actual_match == expected_match

    actual_reconstruction = reconstruct_match(actual_match)
    with (fixture_dir / "point_by_point.json").open(encoding="utf-8") as file:
        expected_reconstruction = json.load(file)

    assert actual_reconstruction == expected_reconstruction


@pytest.mark.parametrize(("set_count", "teams"), FIXTURES)
def test_analysis_json_matches_reference(
    set_count: int,
    teams: list[dict[str, object]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir = FIXTURE_ROOT / f"{set_count}-set"
    with (fixture_dir / "point_by_point.json").open(encoding="utf-8") as file:
        point_data = json.load(file)

    monkeypatch.setattr(
        rotation_analysis,
        "_plot_team_distribution",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_plot_service_run_distribution",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_plot_points_by_setter_position",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_write_markdown_report",
        lambda *args, **kwargs: None
    )

    rotation_analysis.analyze_rotation_runs(
        point_data,
        teams,
        tmp_path
    )

    for filename in ADULT_ANALYSIS_JSON_FILES:
        with (tmp_path / filename).open(encoding="utf-8") as actual_file:
            actual = json.load(actual_file)
        with (fixture_dir / filename).open(encoding="utf-8") as expected_file:
            expected = json.load(expected_file)
        assert actual == expected, filename


def test_u14_pdf_match_and_reconstruction_match_reference() -> None:
    fixture_dir = FIXTURE_ROOT / "u14"
    parser = PdfScoreSheetParser()

    parsed_match = parser.parse_match(
        parser.extract_page(fixture_dir / "match.pdf")
    )
    actual_match = json.loads(parsed_match.to_json())
    with (fixture_dir / "match.json").open(encoding="utf-8") as file:
        expected_match = json.load(file)

    assert actual_match["match_type"] == "U14"
    assert len(actual_match["sets"]) == 2
    assert all(
        len(rotation) == 4
        for set_data in actual_match["sets"]
        for rotation in set_data["start_rotation"].values()
    )
    assert [
        set_data["first_serving_team"]
        for set_data in actual_match["sets"]
    ] == ["TV Altdorf", "TSV Ansbach"]
    assert actual_match["sets"][0]["serve_rounds"] == {
        "TV Altdorf": {
            "10": [1, 6, 11, 18, 23],
            "12": [2, 7, 13, 20, 24],
            "7": [5, 8, 14, 20],
            "1": [5, 10, 15, 22]
        },
        "TSV Ansbach": {
            "8": [6, 12, 18, 24],
            "14": [3, 7, 14, 21, 26],
            "5": [4, 9, 15, 21],
            "2": [5, 10, 16, 23]
        }
    }
    assert actual_match == expected_match

    actual_reconstruction = reconstruct_match(actual_match)
    with (fixture_dir / "point_by_point.json").open(encoding="utf-8") as file:
        expected_reconstruction = json.load(file)

    assert actual_reconstruction == expected_reconstruction
    first_set_points = actual_reconstruction["sets"][0]["points"]
    altdorf_at_18 = next(
        point for point in first_set_points
        if point["score_after"] == {"TV Altdorf": 18, "TSV Ansbach": 18}
    )
    altdorf_at_20 = next(
        point for point in first_set_points
        if point["score_after"] == {"TV Altdorf": 20, "TSV Ansbach": 18}
    )
    assert altdorf_at_18["rotations"]["TV Altdorf"][0] == 10
    assert altdorf_at_20["rotations"]["TV Altdorf"][0] == 12


def test_u14_deciding_set_uses_15_point_target() -> None:
    teams = ("TSV A", "TSV B")
    match = {
        "match_type": "U14",
        "sets": [
            {
                "set_no": 3,
                "first_serving_team": teams[0],
                "start_rotation": {
                    teams[0]: [1, 2, 3, 4],
                    teams[1]: [5, 6, 7, 8]
                },
                "serve_rounds": {
                    teams[0]: {
                        "1": [2, 10],
                        "2": [4, 12],
                        "3": [6, 14],
                        "4": [8, 15]
                    },
                    teams[1]: {
                        "5": [],
                        "6": [],
                        "7": [],
                        "8": []
                    }
                },
                "substitutions": {teams[0]: [], teams[1]: []},
                "points": {teams[0]: 15, teams[1]: 0}
            }
        ]
    }

    reconstructed = reconstruct_match(match)

    assert len(reconstructed["sets"][0]["points"]) == 15
    assert reconstructed["sets"][0]["points"][-1]["score_after"] == {
        teams[0]: 15,
        teams[1]: 0
    }


def test_u14_analysis_json_matches_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_dir = FIXTURE_ROOT / "u14"
    with (fixture_dir / "point_by_point.json").open(encoding="utf-8") as file:
        point_data = json.load(file)
    teams = [
        {"name": name, "setters": []}
        for name in point_data["player_names"]
    ]

    monkeypatch.setattr(
        rotation_analysis,
        "_plot_team_distribution",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_plot_service_run_distribution",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_plot_points_by_setter_position",
        lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        rotation_analysis,
        "_write_pdf_report",
        lambda *args, **kwargs: None
    )

    (tmp_path / "points_by_setter_position.json").write_text(
        "stale", encoding="utf-8"
    )
    for name in point_data["player_names"]:
        (tmp_path / f"{rotation_analysis._filename_slug(name)}"
         "_points_by_setter_position.png").write_text(
            "stale", encoding="utf-8"
        )

    rotation_analysis.analyze_rotation_runs(point_data, teams, tmp_path)

    for filename in U14_ANALYSIS_JSON_FILES:
        with (tmp_path / filename).open(encoding="utf-8") as actual_file:
            actual = json.load(actual_file)
        with (fixture_dir / filename).open(encoding="utf-8") as expected_file:
            expected = json.load(expected_file)
        assert actual == expected, filename

    with (tmp_path / "rotation_run_distributions.json").open(
        encoding="utf-8"
    ) as file:
        opponent_runs = json.load(file)
    for team in opponent_runs["teams"]:
        labels = [rotation["label"] for rotation in team["rotations"]]
        assert len(labels) > 1
        assert all("Laeufer" not in label for label in labels)
        assert all("I: #" in label and "III: #" in label for label in labels)
        if team["team"] == "TSV Ansbach":
            assert all("(" in label and ")" in label for label in labels)
            assert all(
                name in " | ".join(labels)
                for name in (
                    "Eva, L.",
                    "Frida, H.",
                    "Lea, G.",
                    "Malena, W."
                )
            )

    with (tmp_path / "service_run_distributions.json").open(
        encoding="utf-8"
    ) as file:
        service_runs = json.load(file)
    for team in service_runs["teams"]:
        assert len(team["rotations"]) == 1
        assert team["rotations"][0]["label"] == "All serving rotations"

    assert not (tmp_path / "points_by_setter_position.json").exists()
    assert all(
        not (tmp_path / f"{rotation_analysis._filename_slug(name)}"
             "_points_by_setter_position.png").exists()
        for name in point_data["player_names"]
    )
    report_file = next(tmp_path.glob("report_*.md"))
    report_text = report_file.read_text(encoding="utf-8")
    assert report_text.startswith("# TV Altdorf vs TSV Ansbach\n")
    assert "## Contents" in report_text
    assert "Points by setter position" not in report_text
    assert "Own service runs are shown as an overall distribution" in report_text
    assert "opponent scoring runs by own rotation" in report_text.lower()
    assert "Laeufer III" not in report_text
    assert "\n- Opponent scoring runs by own rotation (" in report_text
    assert "\n  - Best rotations:\n    - " in report_text
    assert "\n  - Worst rotations:\n    - " in report_text
    service_run_section = report_text.split("## Length of Service Runs", 1)[1]
    ansbach_summary = service_run_section.split("### TSV Ansbach", 1)[1]
    assert all(
        name in ansbach_summary
        for name in ("Eva, L.", "Frida, H.", "Lea, G.", "Malena, W.")
    )
