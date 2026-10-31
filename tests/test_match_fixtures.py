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
ANALYSIS_JSON_FILES = (
    "rotation_run_distributions.json",
    "service_run_distributions.json",
    "points_by_setter_position.json",
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

    for filename in ANALYSIS_JSON_FILES:
        with (tmp_path / filename).open(encoding="utf-8") as actual_file:
            actual = json.load(actual_file)
        with (fixture_dir / filename).open(encoding="utf-8") as expected_file:
            expected = json.load(expected_file)
        assert actual == expected, filename
