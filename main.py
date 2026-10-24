# Requires Poppler's pdftotext utility (poppler-utils on Linux).

import argparse
from pathlib import Path

from match_reconstructor import (
    load_match_json,
    reconstruct_match,
    write_reconstruction
)
from pdf_score_sheet import PdfScoreSheetParser


def main(
    pdf_file="Spielbericht_ANS_HAHNBACH.pdf",
    output_file="match.json",
    points_output_file="point_by_point.json",
    load_match=False
):
    if load_match:
        match_data = load_match_json(output_file)
    else:
        parser = PdfScoreSheetParser()
        page = parser.extract_page(pdf_file)
        match = parser.parse_match(page)
        Path(output_file).write_text(
            match.to_json() + "\n",
            encoding="utf-8"
        )
        match_data = load_match_json(output_file)
        print(f"Saved match data to {output_file}")

    reconstruction = reconstruct_match(match_data)
    write_reconstruction(reconstruction, points_output_file)
    print(f"Saved point-by-point reconstruction to {points_output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf_file",
        nargs="?",
        default="Spielbericht_ANS_HAHNBACH.pdf"
    )
    parser.add_argument(
        "--output",
        default="match.json",
        help="JSON file to write parsed match data to"
    )
    parser.add_argument(
        "--points-output",
        default="point_by_point.json",
        help="JSON file to write the point-by-point reconstruction to"
    )
    parser.add_argument(
        "--load-match",
        action="store_true",
        help="Reconstruct points from the existing match JSON without parsing a PDF"
    )
    args = parser.parse_args()

    main(
        pdf_file=args.pdf_file,
        output_file=args.output,
        points_output_file=args.points_output,
        load_match=args.load_match
    )
