import json
from pathlib import Path
import subprocess

import pytest
import yaml

import main
from match_reconstructor import reconstruct_match
from pdf_score_sheet import (
    PdfPage,
    PdfScoreSheetParser,
    PositionedWord,
    SetPanel,
    SetJson
)
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


def _capture_markdown_report(
    monkeypatch: pytest.MonkeyPatch,
    generate_pdf: bool = True
) -> dict[str, str]:
    report = {}
    write_pdf_report = rotation_analysis._write_pdf_report

    def capture(*args, **kwargs) -> None:
        markdown_file = args[1]
        report["text"] = Path(markdown_file).read_text(encoding="utf-8")
        if generate_pdf:
            write_pdf_report(*args, **kwargs)

    monkeypatch.setattr(rotation_analysis, "_write_pdf_report", capture)
    return report


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


def test_stacked_serve_marks_are_split_using_their_vertical_rows() -> None:
    parser = PdfScoreSheetParser()
    pdf_file = (
        FIXTURE_ROOT.parent
        / "input"
        / "Spielbericht_Kleinaitingen_Bad_Groenenbach.pdf"
    )
    parsed_match = parser.parse_match(parser.extract_page(pdf_file))
    match = json.loads(parsed_match.to_json())

    assert match["sets"][0]["serve_rounds"]["TV Bad Grönenbach"]["11"] == [
        0, 13
    ]
    assert match["sets"][1]["serve_rounds"]["FC Kleinaitingen"]["11"] == [
        0, 12, 26
    ]
    assert match["sets"][2]["serve_rounds"]["TV Bad Grönenbach"]["11"] == [
        1, 9
    ]

    reconstruction = reconstruct_match(match)
    assert [
        set_data["points"][-1]["score_after"]
        for set_data in reconstruction["sets"]
    ] == [
        {"TV Bad Grönenbach": 20, "FC Kleinaitingen": 25},
        {"FC Kleinaitingen": 26, "TV Bad Grönenbach": 24},
        {"TV Bad Grönenbach": 16, "FC Kleinaitingen": 25}
    ]


def test_config_processes_stacked_serve_marks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = (
        FIXTURE_ROOT.parent
        / "input"
        / "config_Kleinaitingen_Bad_Groenenbach.yaml"
    )
    load_config = main._load_config

    def load_config_with_temp_outputs(path: str | Path) -> dict[str, object]:
        config = load_config(path)
        config.update({
            "pdf_file": str(
                FIXTURE_ROOT.parent / str(config["pdf_file"])
            ),
            "match_json": str(tmp_path / "match.json"),
            "point_by_point_json": str(tmp_path / "point_by_point.json"),
            "analysis_output_dir": str(tmp_path / "analysis")
        })
        return config

    monkeypatch.setattr(main, "_load_config", load_config_with_temp_outputs)
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
    monkeypatch.setattr(
        rotation_analysis,
        "_write_pdf_report",
        lambda *args, **kwargs: None
    )

    main.main(config_file=str(config_file))

    with (tmp_path / "match.json").open(encoding="utf-8") as file:
        match = json.load(file)
    with (tmp_path / "point_by_point.json").open(encoding="utf-8") as file:
        reconstruction = json.load(file)

    assert len(match["sets"]) == 3
    assert [
        set_data["points"][-1]["score_after"]
        for set_data in reconstruction["sets"]
    ] == [
        {"FC Kleinaitingen": 25, "TV Bad Grönenbach": 20},
        {"FC Kleinaitingen": 26, "TV Bad Grönenbach": 24},
        {"FC Kleinaitingen": 25, "TV Bad Grönenbach": 16}
    ]


def test_file_argument_extracts_teams_and_saves_reusable_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_file = FIXTURE_ROOT / "3-set" / "match.pdf"
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.setattr(
        rotation_analysis,
        "_write_pdf_report",
        lambda *args, **kwargs: None
    )

    main.main(
        pdf_file=pdf_file,
        setters=[[4, 22], []]
    )

    config_path = tmp_path / "input" / "config_match.yaml"
    with config_path.open(encoding="utf-8") as file:
        generated_config = yaml.safe_load(file)
    assert generated_config["pdf_file"] == str(pdf_file)
    assert generated_config["teams"] == [
        {"name": "TV Planegg Krailling II", "setters": [4, 22]},
        {"name": "TV/DJK Hammelburg", "setters": []}
    ]
    assert main._load_config(config_path)["teams"] == generated_config["teams"]


def test_cli_setter_parser_accepts_shell_split_json_values() -> None:
    assert main._parse_setters_argument(
        ["[[16,13],", "[8,", "11]]"]
    ) == [[16, 13], [8, 11]]
    assert main._parse_setters_argument(["[[],", "[8,11]]"]) == [[], [8, 11]]


@pytest.mark.parametrize(
    "setters",
    [
        [[1], [1, 1]],
        [[0], []],
        [[True], []],
        [[1], None],
        [[1]]
    ]
)
def test_setter_lists_reject_invalid_values(setters: object) -> None:
    with pytest.raises(ValueError):
        main._validate_setter_lists(setters, 2)


@pytest.mark.parametrize(
    ("fixture_name", "expected_set_scores"),
    [
        pytest.param(
            "u18-tiebreak",
            [
                {
                    "TV Altdorf": 26,
                    "VSG TV Bad Windsheim / SB Uffenheim": 28
                },
                {
                    "VSG TV Bad Windsheim / SB Uffenheim": 12,
                    "TV Altdorf": 25
                },
                {
                    "VSG TV Bad Windsheim / SB Uffenheim": 13,
                    "TV Altdorf": 15
                }
            ],
            id="u18-tiebreak"
        ),
        pytest.param(
            "u18",
            [
                {"TV Altdorf": 17, "TSV Ansbach": 25},
                {"TSV Ansbach": 25, "TV Altdorf": 23}
            ],
            id="u18"
        )
    ]
)
def test_u18_pdf_match_and_reconstruction(
    fixture_name: str,
    expected_set_scores: list[dict[str, int]]
) -> None:
    parser = PdfScoreSheetParser()
    parsed_match = parser.parse_match(
        parser.extract_page(FIXTURE_ROOT / fixture_name / "match.pdf")
    )
    actual_match = json.loads(parsed_match.to_json())

    assert actual_match["match_type"] == "U18"
    assert [set_data["points"] for set_data in actual_match["sets"]] == (
        expected_set_scores
    )

    reconstruction = reconstruct_match(actual_match)
    assert [
        set_data["points"][-1]["score_after"]
        for set_data in reconstruction["sets"]
    ] == expected_set_scores

    if fixture_name == "u18-tiebreak":
        deciding_set = actual_match["sets"][-1]
        bad_windsheim = "VSG TV Bad Windsheim / SB Uffenheim"
        assert deciding_set["start_rotation"][bad_windsheim] == [
            12, 8, 5, 2, 4, 16
        ]
        assert deciding_set["serve_rounds"][bad_windsheim] == {
            "12": [0, 13],
            "8": [1],
            "5": [5],
            "2": [8],
            "4": [10],
            "16": [12]
        }


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


def test_u14_tiebreak_pdf_match_and_reconstruction_match_reference() -> None:
    fixture_dir = FIXTURE_ROOT / "u14-tiebreak"
    parser = PdfScoreSheetParser()

    parsed_match = parser.parse_match(
        parser.extract_page(fixture_dir / "match.pdf")
    )
    actual_match = json.loads(parsed_match.to_json())
    with (fixture_dir / "match.json").open(encoding="utf-8") as file:
        expected_match = json.load(file)

    assert actual_match["match_type"] == "U14"
    assert len(actual_match["sets"]) == 3
    assert actual_match["sets"][2]["points"] == {
        "TSV Ansbach": 15,
        "Post SV Nürnberg": 10
    }
    assert actual_match["sets"][2]["first_serving_team"] == (
        "Post SV Nürnberg"
    )
    assert actual_match == expected_match

    actual_reconstruction = reconstruct_match(actual_match)
    with (fixture_dir / "point_by_point.json").open(
        encoding="utf-8"
    ) as file:
        expected_reconstruction = json.load(file)

    assert actual_reconstruction == expected_reconstruction
    deciding_set = actual_reconstruction["sets"][2]
    assert len(deciding_set["points"]) == 25
    assert deciding_set["points"][-1]["score_after"] == {
        "TSV Ansbach": 15,
        "Post SV Nürnberg": 10
    }


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


def test_players_without_serve_rounds_do_not_prevent_reconstruction() -> None:
    teams = ("TSV A", "TSV B")
    match = {
        "sets": [
            {
                "set_no": 1,
                "start_rotation": {
                    teams[0]: [1, 2, 3, 4, 5, 6],
                    teams[1]: [7, 8, 9, 10, 11, 12]
                },
                "serve_rounds": {
                    teams[0]: {
                        "1": [25],
                        "2": [],
                        "3": [],
                        "4": [],
                        "5": [],
                        "6": []
                    },
                    teams[1]: {
                        "7": [],
                        "8": [],
                        "9": [],
                        "10": [],
                        "11": [],
                        "12": []
                    }
                },
                "substitutions": {teams[0]: [], teams[1]: []},
                "points": {teams[0]: 25, teams[1]: 0}
            }
        ]
    }

    reconstructed = reconstruct_match(match)

    assert len(reconstructed["sets"][0]["points"]) == 25
    assert reconstructed["sets"][0]["points"][-1]["score_after"] == {
        teams[0]: 25,
        teams[1]: 0
    }


@pytest.mark.parametrize(("header", "expected"), [
    ("Mfr - U16 Bezirksliga", "U16"),
    ("Mfr - U 18 Bezirksliga", "U18"),
    ("Mfr - U-20 Bezirksliga", "U20"),
    ("Mfr - U14 Bezirksliga", "U14"),
    ("Mfr - Bezirksliga", None)
])
def test_header_detects_youth_match_type(
    header: str,
    expected: str | None
) -> None:
    words = [
        PositionedWord(
            x0=float(index),
            y0=10,
            x1=float(index + 1),
            y1=15,
            text=word
        )
        for index, word in enumerate(header.split())
    ]

    assert PdfScoreSheetParser._read_match_type(words) == expected


@pytest.mark.parametrize("match_type", ["U16", "U18", "U20"])
def test_youth_parser_uses_side_switch_parser_for_deciding_set(
    match_type: str,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = PdfScoreSheetParser()
    team_names = {"A": "Team A", "B": "Team B"}
    set_scores = [
        {"Team A": 25, "Team B": 20},
        {"Team A": 20, "Team B": 25},
        {"Team A": 15, "Team B": 13}
    ]
    parsed_set_numbers = []

    monkeypatch.setattr(
        parser,
        "_read_team_names",
        lambda words: team_names
    )
    monkeypatch.setattr(
        parser,
        "_read_player_names",
        lambda words, names: {}
    )
    monkeypatch.setattr(parser, "_read_match_date", lambda words: "01.01.2026")
    monkeypatch.setattr(parser, "_read_match_type", lambda words: match_type)
    monkeypatch.setattr(
        parser,
        "_panel_team_codes",
        lambda *args: ("A", "B")
    )

    def parsed_set(set_no: int) -> SetJson:
        parsed_set_numbers.append(set_no)
        return SetJson(
            set_no=set_no,
            start_rotation={},
            serve_rounds={},
            substitutions={},
            points=set_scores[set_no - 1]
        )

    def parse_set(
        words: list[PositionedWord],
        panel: SetPanel,
        team_names: dict[str, str],
        scale_x: float,
        scale_y: float,
        set_no: int,
        is_u14: bool = False
    ) -> SetJson:
        return parsed_set(set_no)

    def parse_deciding_set(
        words: list[PositionedWord],
        team_names: dict[str, str],
        scale_x: float,
        scale_y: float,
        set_no: int
    ) -> SetJson:
        return parsed_set(set_no)

    monkeypatch.setattr(parser, "_parse_set", parse_set)
    monkeypatch.setattr(parser, "_parse_deciding_set", parse_deciding_set)
    parsed_match = parser.parse_match(PdfPage(841.92, 595.92, []))

    assert parsed_match.match_type == match_type
    assert [set_data.set_no for set_data in parsed_match.sets] == [1, 2, 3]
    assert parsed_set_numbers == [1, 2, 3]


def test_u16_deciding_set_requires_15_points_and_two_point_lead() -> None:
    teams = ("TSV A", "TSV B")
    match = {
        "match_type": "U16",
        "sets": [
            {
                "set_no": 3,
                "start_rotation": {
                    teams[0]: [1, 2, 3, 4, 5, 6],
                    teams[1]: [7, 8, 9, 10, 11, 12]
                },
                "serve_rounds": {teams[0]: {}, teams[1]: {}},
                "substitutions": {teams[0]: [], teams[1]: []},
                "points": {teams[0]: 15, teams[1]: 14}
            }
        ]
    }

    with pytest.raises(ValueError, match="15 or more with a two-point lead"):
        reconstruct_match(match)


def test_u16_deciding_set_uses_six_player_rotation_rules() -> None:
    with (FIXTURE_ROOT / "5-set" / "match.json").open(
        encoding="utf-8"
    ) as file:
        adult_match = json.load(file)
    deciding_set = adult_match["sets"][-1]
    deciding_set["set_no"] = 3

    reconstructed = reconstruct_match(
        {"match_type": "U16", "sets": [deciding_set]}
    )
    points = reconstructed["sets"][0]["points"]

    assert len(points) == 36
    assert points[-1]["score_after"] == {
        "DJK Augsburg Hochzoll": 17,
        "TSV Ansbach": 19
    }
    assert all(
        len(rotation) == 6
        for point in points
        for rotation in point["rotations"].values()
    )


def test_u16_parser_rejects_short_deciding_set_score() -> None:
    set_data = SetJson(
        set_no=3,
        start_rotation={},
        serve_rounds={},
        substitutions={},
        points={"Team A": 15, "Team B": 14}
    )
    sets_won = {"Team A": 0, "Team B": 0}

    with pytest.raises(ValueError, match="15 or more with a two-point lead"):
        PdfScoreSheetParser._record_set_win(
            sets_won,
            set_data,
            match_type="U16"
        )


def test_report_lists_setter_start_positions_and_service_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _capture_markdown_report(monkeypatch)
    teams = [
        {"name": "TSV A", "setters": [1, 2]},
        {"name": "TSV B", "setters": [7, 8]}
    ]
    point_data = {
        "match_date": "01.01.2026",
        "sets": [
            {
                "set_no": 1,
                "points": [
                    {
                        "point_no": 1,
                        "serving_team": "TSV A",
                        "scoring_team": "TSV A",
                        "rotations": {
                            "TSV A": [1, 2, 3, 4, 5, 6],
                            "TSV B": [7, 8, 9, 10, 11, 12]
                        },
                        "score_after": {"TSV A": 1, "TSV B": 0}
                    }
                ]
            },
            {
                "set_no": 2,
                "points": [
                    {
                        "point_no": 1,
                        "serving_team": "TSV B",
                        "scoring_team": "TSV B",
                        "rotations": {
                            "TSV A": [3, 4, 5, 6, 1, 2],
                            "TSV B": [9, 10, 11, 12, 7, 8]
                        },
                        "score_after": {"TSV A": 0, "TSV B": 1}
                    }
                ]
            }
        ]
    }

    rotation_analysis.analyze_rotation_runs(point_data, teams, tmp_path)

    report_file = next(tmp_path.glob("report_*.pdf"))
    report_text = report["text"]
    assert report_file.read_bytes().startswith(b"%PDF")
    assert not list(tmp_path.glob("*.md"))
    assert not list(tmp_path.glob("*.png"))
    assert (
        "- [Setter Starting Positions](#setter-starting-positions)"
        in report_text
    )
    assert (
        "### TSV A\n\n"
        "| Set | Setter(s) starting | At set start |\n"
        "|---:|---|---|\n"
        "| 1 | #1: Position I; #2: Position II | Own serve |\n"
        "| 2 | #1: Position V; #2: Position VI | Reception |"
        in report_text
    )
    assert (
        "### TSV B\n\n"
        "| Set | Setter(s) starting | At set start |\n"
        "|---:|---|---|\n"
        "| 1 | #7: Position I; #8: Position II | Reception |\n"
        "| 2 | #7: Position V; #8: Position VI | Own serve |"
        in report_text
    )
    setter_report = report_text.split(
        "## Setter Starting Positions", 1
    )[1].split("## Stats per Player", 1)[0]
    assert setter_report.count("| Set | Setter(s) starting | At set start |") == 2


def test_report_shows_rally_by_rally_score_progression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _capture_markdown_report(monkeypatch)
    team_a = "TSV Ansbach"
    team_b = "SV Hahnbach Volleyball Verein West"

    def make_set(set_no: int, scoring_sequence: list[str]) -> dict[str, object]:
        score = {team_a: 0, team_b: 0}
        points = []
        for rally_index, scoring_team in enumerate(scoring_sequence):
            score[scoring_team] += 1
            points.append(
                {
                    "point_no": rally_index + 1,
                    "serving_team": team_a,
                    "scoring_team": scoring_team,
                    "rotations": {
                        team_a: [1, 2, 3, 4, 5, 6],
                        team_b: [7, 8, 9, 10, 11, 12]
                    },
                    "score_after": score.copy()
                }
            )
        return {"set_no": set_no, "points": points}

    regular_set_sequence = [
        team_a if rally_index % 2 == 0 else team_b
        for rally_index in range(48)
    ] + [team_a] * 2
    extended_set_sequence = regular_set_sequence + [team_a]
    regular_set = make_set(1, regular_set_sequence)
    regular_set["timeouts"] = [
        {"team": team_a, "team_score": 2, "opponent_score": 1},
        {"team": team_b, "team_score": 1, "opponent_score": 2}
    ]
    deciding_set_sequence = [
        team_a if rally_index % 2 == 0 else team_b
        for rally_index in range(20)
    ] + [team_a] * 5

    rotation_analysis.analyze_rotation_runs(
        {
            "match_date": "01.01.2026",
            "sets": [
                regular_set,
                make_set(2, extended_set_sequence),
                make_set(3, deciding_set_sequence)
            ]
        },
        [
            {"name": team_a, "setters": []},
            {"name": team_b, "setters": []}
        ],
        tmp_path
    )

    report_text = report["text"]
    assert "- [Set Score Progression](#set-score-progression)" in report_text
    assert report_text.index("> Please take all analyses") < report_text.index(
        "## Contents"
    )
    assert report_text.index("## Contents") < report_text.index(
        "## Set Score Progression"
    )
    progression_section = report_text.split(
        "## Set Score Progression", 1
    )[1].split("## Performance per Set of Players", 1)[0]
    assert "### Set 1" in progression_section
    assert "### Set 2" in progression_section
    assert "### Set 3" in progression_section
    assert (
        f"<tr><th>{team_a}</th><td>1</td><td>&nbsp;</td><td>2</td>"
        in progression_section
    )
    assert (
        f"<tr><th>{team_b}</th><td>&nbsp;</td><td>1</td><td>&nbsp;</td>"
        in progression_section
    )
    assert "<td>26</td>" in progression_section
    set_one_progression = progression_section.split(
        "### Set 1", 1
    )[1].split("### Set 2", 1)[0]
    assert set_one_progression.count("<td>") == 100
    team_a_row = next(
        line for line in set_one_progression.splitlines()
        if line.startswith(f"<tr><th>{team_a}</th>")
    )
    team_b_row = next(
        line for line in set_one_progression.splitlines()
        if line.startswith(f"<tr><th>{team_b}</th>")
    )
    assert "<td>2<br><strong>T</strong></td>" in team_a_row
    assert "<td><strong>T</strong></td>" in team_b_row

    report_file = next(tmp_path.glob("report_*.pdf"))
    extracted_pdf = subprocess.run(
        ["pdftotext", str(report_file), "-"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8"
    ).stdout
    normalized_pdf_text = " ".join(extracted_pdf.split())
    assert "Set Score Progression" in extracted_pdf
    assert team_b in normalized_pdf_text
    assert extracted_pdf.index("Please take all analyses") < extracted_pdf.index(
        "Contents"
    )
    assert extracted_pdf.index("Contents") < extracted_pdf.index(
        "Set Score Progression"
    )
    first_page = subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", str(report_file), "-"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8"
    ).stdout
    second_page = subprocess.run(
        ["pdftotext", "-f", "2", "-l", "2", str(report_file), "-"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8"
    ).stdout
    assert "Each column is one rally" not in first_page
    assert "Each column is one rally" in second_page
    assert "Set 1" in extracted_pdf
    assert "Set 2" in extracted_pdf
    assert "Set 3" in extracted_pdf
    assert "continued: rallies 51" in extracted_pdf


def test_timeout_scores_are_extracted_and_reconstructed_from_team_perspective() -> None:
    fixture_dir = FIXTURE_ROOT / "5-set"
    parsed_match = PdfScoreSheetParser().parse_match(
        PdfScoreSheetParser().extract_page(fixture_dir / "match.pdf")
    )
    match_data = json.loads(parsed_match.to_json())
    reconstructed = reconstruct_match(match_data)

    assert {
        (timeout["team"], timeout["team_score"], timeout["opponent_score"])
        for timeout in match_data["sets"][0]["timeouts"]
    } >= {("TSV Ansbach", 19, 20)}
    assert {
        (timeout["team"], timeout["team_score"], timeout["opponent_score"])
        for timeout in match_data["sets"][4]["timeouts"]
    } >= {
        ("TSV Ansbach", 8, 8),
        ("DJK Augsburg Hochzoll", 9, 10)
    }
    assert reconstructed["sets"][4]["timeouts"] == match_data["sets"][4][
        "timeouts"
    ]


def test_serving_player_stats_exclude_own_team_points(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _capture_markdown_report(monkeypatch)
    team_a = "TSV A"
    team_b = "TSV B"
    rotation_a = [1, 2, 3, 4, 5, 6]
    rotation_a_after_rotate = [2, 3, 4, 5, 6, 1]
    rotation_b = [7, 8, 9, 10, 11, 12]
    score = {team_a: 0, team_b: 0}

    def point(
        serving_team: str,
        scoring_team: str,
        team_a_rotation: list[int],
        team_b_rotation: list[int]
    ) -> dict[str, object]:
        score[scoring_team] += 1
        return {
            "serving_team": serving_team,
            "scoring_team": scoring_team,
            "rotations": {
                team_a: team_a_rotation,
                team_b: team_b_rotation
            },
            "score_after": score.copy()
        }

    point_data = {
        "match_date": "01.01.2026",
        "player_names": {
            team_a: {"1": "Ada, A.", "2": "Bea, B."},
            team_b: {"7": "Cia, C."}
        },
        "sets": [
            {
                "set_no": 1,
                "points": [
                    point(team_a, team_a, rotation_a, rotation_b),
                    point(team_a, team_b, rotation_a, rotation_b),
                    point(team_b, team_b, rotation_a, rotation_b),
                    point(
                        team_a,
                        team_b,
                        rotation_a_after_rotate,
                        rotation_b
                    )
                ]
            }
        ]
    }
    rotation_analysis.analyze_rotation_runs(
        point_data,
        [{"name": team_a, "setters": []}, {"name": team_b, "setters": []}],
        tmp_path
    )

    report_text = report["text"]
    assert "Setter Starting Positions" not in report_text
    assert "- [Stats per Serving Player](#stats-per-serving-player)" in report_text
    assert "## Stats per Serving Player" in report_text
    assert "| 1 (Ada, A.) | 2 | 1 | 0.50 |" in report_text
    assert "| 2 (Bea, B.) | 1 | 1 | 1.00 |" in report_text
    assert "| 7 (Cia, C.) | 1 | 0 | 0.00 |" in report_text
    assert (
        f"<th>{team_a}</th><th>{team_b}</th>" in report_text
    )
    assert "<td>Sets: 0</td><td>Sets: 1</td>" in report_text
    assert (
        '<tr><td colspan="2">Set results: Set 1: 1–3</td></tr>'
        in report_text
    )
    assert "grain of salt" in report_text
    assert "not necessarily aces scored by that player" in report_text
    assert "#### Points by setter position" in report_text
    assert "#### Opponent scoring-run length by rotation" in report_text
    assert "Own service runs are shown as an overall distribution" not in report_text
    pdf_file = next(tmp_path.glob("report_*.pdf"))
    assert pdf_file.read_bytes().startswith(b"%PDF")
    assert not list(tmp_path.glob("*.md"))
    assert not list(tmp_path.glob("*.png"))


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
    report = _capture_markdown_report(monkeypatch, generate_pdf=False)

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
    report_text = report["text"]
    assert report_text.startswith("# TV Altdorf vs TSV Ansbach\n")
    assert "## Contents" in report_text
    result_table = report_text.split("## Match Result", 1)[1].split(
        "</table>", 1
    )[0]
    assert "<th>TV Altdorf</th><th>TSV Ansbach</th>" in result_table
    assert "<td>Sets: 0</td><td>Sets: 2</td>" in result_table
    assert (
        '<tr><td colspan="2">Set results: '
        "Set 1: 24–26; Set 2: 22–25</td></tr>"
    ) in result_table
    assert "grain of salt" in report_text
    serving_player_section = report_text.split(
        "## Stats per Serving Player", 1
    )[1].split("## Length of Service Runs", 1)[0]
    assert "| 10 (Sophy, B.) | 12 | 7 | 0.58 |" in serving_player_section
    assert "Points by setter position" not in report_text
    assert "Own service runs are shown as an overall distribution" in report_text
    assert "opponent scoring runs by own rotation" in report_text.lower()
    assert "#### Points by setter position" not in report_text
    assert "#### Opponent scoring-run length by own rotation" in report_text
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
