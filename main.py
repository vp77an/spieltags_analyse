# Requires Poppler's pdftotext utility (poppler-utils on Linux).

import argparse

from pdf_score_sheet import PdfScoreSheetParser


def main(
    pdf_file="Spielbericht_ANS_HAHNBACH.pdf",
    cache_file="pdf_text.json",
    load_cache=False
):

    parser = PdfScoreSheetParser()
    if load_cache:
        page = parser.load_page(cache_file)
    else:
        page = parser.extract_page(pdf_file)
        parser.save_page(page, cache_file)
    match = parser.parse_match(page)

    print(match)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf_file",
        nargs="?",
        default="Spielbericht_ANS_HAHNBACH.pdf"
    )
    parser.add_argument(
        "--cache-file",
        dest="cache_file",
        default="pdf_text.json",
        help="JSON file used to cache extracted positioned PDF text"
    )
    parser.add_argument(
        "--load-cache",
        dest="load_cache",
        action="store_true",
        help="Load cached positioned text instead of parsing the PDF"
    )
    args = parser.parse_args()

    main(
        pdf_file=args.pdf_file,
        cache_file=args.cache_file,
        load_cache=args.load_cache
    )
