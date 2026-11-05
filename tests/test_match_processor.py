from pathlib import Path
import subprocess
import sys

import pytest

from match_processor import (
    extract_team_header_order,
    parse_setter_numbers,
    pdf_processing_error,
    process_match_pdf
)
from pdf_score_sheet import (
    PdfScoreSheetParser,
    PositionedWord,
    _pdftotext_executable
)


FIXTURE_ROOT = Path(__file__).parent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", []),
        ("  ", []),
        ("4, 12", [4, 12])
    ]
)
def test_parse_setter_numbers(value: str, expected: list[int]) -> None:
    assert parse_setter_numbers(value) == expected


@pytest.mark.parametrize("value", ["four", "0", "4, 4"])
def test_parse_setter_numbers_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_setter_numbers(value)


@pytest.mark.parametrize(
    ("stderr", "expected_detail"),
    [
        ("Access denied", "Access denied"),
        (b"Invalid PDF header", "Invalid PDF header"),
        ("", "Exit-Code 2")
    ]
)
def test_pdf_processing_error_includes_poppler_details(
    stderr: str | bytes,
    expected_detail: str
) -> None:
    error = subprocess.CalledProcessError(
        2,
        ["pdftotext", "match.pdf"],
        stderr=stderr
    )

    message = pdf_processing_error(error)

    assert "Das PDF konnte nicht gelesen werden." in message
    assert "Technische Details" in message
    assert expected_detail in message


def test_extract_team_header_order() -> None:
    pdf_path = FIXTURE_ROOT / "u18-tiebreak" / "match.pdf"

    assert extract_team_header_order(pdf_path) == [
        "VSG TV Bad Windsheim / SB Uffenheim",
        "TV Altdorf"
    ]


def test_process_match_pdf_generates_report(tmp_path: Path) -> None:
    pdf_path = FIXTURE_ROOT / "u14-tiebreak" / "match.pdf"

    report_path = process_match_pdf(pdf_path, [[], []], tmp_path)

    assert report_path.is_file()
    assert report_path.read_bytes().startswith(b"%PDF-")


def test_frozen_app_uses_bundled_pdftotext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    poppler_dir = tmp_path / "poppler"
    poppler_dir.mkdir()
    bundled_executable = poppler_dir / "pdftotext.exe"
    bundled_executable.touch()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert _pdftotext_executable() == str(bundled_executable)


def test_stacked_serve_marks_combined_into_one_word_are_split() -> None:
    words = [
        PositionedWord(10, 100, 14, 128, "1924"),
        *[
            PositionedWord(x, 100, x + 4, 112, str(score))
            for x, score in zip((30, 50, 70, 90, 110), (2, 3, 4, 5, 6))
        ]
    ]

    serve_rounds = PdfScoreSheetParser()._read_serve_rounds(
        words,
        serve_y=100,
        serve_x=(10, 30, 50, 70, 90, 110),
        rotation=[1, 2, 3, 4, 5, 6],
        scale_x=1,
        scale_y=1
    )

    assert serve_rounds[1] == [1, 9, 24]
