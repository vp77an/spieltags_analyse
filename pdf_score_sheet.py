import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from sams_mapper import MatchJson, SetJson, Substitution


@dataclass(frozen=True)
class PositionedWord:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class PdfPage:
    width: float
    height: float
    words: list[PositionedWord]


@dataclass(frozen=True)
class SetPanel:
    rotation_y: float
    rotation_x: tuple[float, ...]
    score_y: float
    own_score_x: float
    opp_score_x: float
    serve_y: float
    serve_x: tuple[float, ...]


SET_PANELS = (
    SetPanel(
        rotation_y=95.4,
        rotation_x=(130.4, 151.8, 170.9, 192.3, 216.2, 237.6),
        score_y=100.6,
        own_score_x=255.4,
        opp_score_x=411.4,
        serve_y=139.6,
        serve_x=(122.4, 143.9, 165.3, 186.8, 208.2, 229.7)
    ),
    SetPanel(
        rotation_y=95.4,
        rotation_x=(461.1, 480.1, 501.6, 525.4, 546.8, 568.3),
        score_y=100.6,
        own_score_x=586.1,
        opp_score_x=738.0,
        serve_y=139.6,
        serve_x=(453.1, 474.6, 496.0, 517.5, 538.9, 560.3)
    ),
    SetPanel(
        rotation_y=208.0,
        rotation_x=(130.4, 151.8, 170.9, 192.3, 216.2, 237.6),
        score_y=214.6,
        own_score_x=255.4,
        opp_score_x=411.4,
        serve_y=253.6,
        serve_x=(122.4, 143.9, 165.3, 186.8, 208.2, 229.7)
    ),
    SetPanel(
        rotation_y=208.0,
        rotation_x=(461.1, 480.1, 501.6, 525.4, 546.8, 568.3),
        score_y=214.6,
        own_score_x=586.1,
        opp_score_x=738.0,
        serve_y=253.6,
        serve_x=(453.1, 474.6, 496.0, 517.5, 538.9, 560.3)
    )
)


class PdfScoreSheetParser:
    """Extract the four score panels from a SAMS PDF's embedded text layer."""

    def extract_page(self, pdf_file: str | Path) -> PdfPage:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", "-bbox-layout", str(pdf_file), "-"],
            check=True,
            capture_output=True,
            text=True
        )

        root = ET.fromstring(result.stdout)
        page_element = next(
            (element for element in root.iter() if element.tag.endswith("page")),
            None
        )
        if page_element is None:
            raise ValueError("The PDF does not contain a readable first page")

        words = []
        for element in page_element.iter():
            if not element.tag.endswith("word"):
                continue

            text = "".join(element.itertext()).strip()
            if not text:
                continue

            words.append(
                PositionedWord(
                    x0=float(element.attrib["xMin"]),
                    y0=float(element.attrib["yMin"]),
                    x1=float(element.attrib["xMax"]),
                    y1=float(element.attrib["yMax"]),
                    text=text
                )
            )

        if not words:
            raise ValueError(
                "No embedded text found in the PDF; use the OCR extractor for scanned PDFs"
            )

        return PdfPage(
            width=float(page_element.attrib["width"]),
            height=float(page_element.attrib["height"]),
            words=words
        )

    def save_page(self, page: PdfPage, outfile: str | Path) -> None:
        data = {
            "width": page.width,
            "height": page.height,
            "words": [asdict(word) for word in page.words]
        }
        with open(outfile, "w", encoding="utf8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

    def load_page(self, infile: str | Path) -> PdfPage:
        with open(infile, "r", encoding="utf8") as file:
            data = json.load(file)

        if not isinstance(data, dict) or not isinstance(data.get("words"), list):
            raise ValueError("PDF text cache must contain a 'words' list")

        try:
            width = float(data["width"])
            height = float(data["height"])
            words = [PositionedWord(**word) for word in data["words"]]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("PDF text cache has an invalid structure") from error

        return PdfPage(width=width, height=height, words=words)

    def parse_match(self, page: PdfPage) -> MatchJson:
        scale_x = page.width / 841.92
        scale_y = page.height / 595.92
        sets = [
            self._parse_set(page.words, panel, scale_x, scale_y, set_no)
            for set_no, panel in enumerate(SET_PANELS, start=1)
        ]
        return MatchJson(sets=sets)

    def _parse_set(
        self,
        words: list[PositionedWord],
        panel: SetPanel,
        scale_x: float,
        scale_y: float,
        set_no: int
    ) -> SetJson:
        rotation = []
        for target_x in panel.rotation_x:
            word = self._nearest_word(
                words,
                x=target_x * scale_x,
                y=panel.rotation_y * scale_y,
                x_tolerance=8 * scale_x,
                y_tolerance=3 * scale_y,
                pattern=r"\d{1,2}"
            )
            if word is None:
                raise ValueError(
                    f"Could not read starting rotation position {len(rotation) + 1} "
                    f"for set {set_no}"
                )
            rotation.append(int(word.text))

        own_points = self._read_score(
            words, panel.own_score_x * scale_x, panel.score_y * scale_y,
            scale_x, scale_y, set_no, "own"
        )
        opp_points = self._read_score(
            words, panel.opp_score_x * scale_x, panel.score_y * scale_y,
            scale_x, scale_y, set_no, "opponent"
        )

        serve_rounds = self._read_serve_rounds(
            words, panel, rotation, scale_x, scale_y
        )
        substitutions = self._read_substitutions(
            words, panel, rotation, scale_x, scale_y
        )

        return SetJson(
            set_no=set_no,
            start_rotation=rotation,
            serve_rounds=serve_rounds,
            substitutions=substitutions,
            own_points=own_points,
            opp_points=opp_points
        )

    def _read_score(
        self,
        words: list[PositionedWord],
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        set_no: int,
        side: str
    ) -> int:
        word = self._nearest_word(
            words,
            x=x,
            y=y,
            x_tolerance=12 * scale_x,
            y_tolerance=8 * scale_y,
            pattern=r"\d{1,2}"
        )
        if word is None:
            raise ValueError(f"Could not read {side} score for set {set_no}")
        return int(word.text)

    def _read_serve_rounds(
        self,
        words: list[PositionedWord],
        panel: SetPanel,
        rotation: list[int],
        scale_x: float,
        scale_y: float
    ) -> dict[int, list[int]]:
        rounds = {player: [] for player in rotation}

        for player, target_x in zip(rotation, panel.serve_x):
            column_words = [
                word for word in words
                if abs(word.x0 - target_x * scale_x) <= 8 * scale_x
                and panel.serve_y * scale_y <= word.y0
                <= (panel.serve_y + 34) * scale_y
            ]
            scores = []
            for word in sorted(column_words, key=lambda item: item.y0):
                if re.fullmatch(r"\d{1,2}", word.text):
                    scores.append(int(word.text))
                elif (
                    re.fullmatch(r"\d{3,}", word.text)
                    and word.y1 - word.y0 >= 20 * scale_y
                ):
                    scores.extend(
                        self._split_stacked_scores(word.text)
                    )
            rounds[player] = scores

        return rounds

    @staticmethod
    def _split_stacked_scores(text: str) -> list[int]:
        def find_sequence(start: int, previous: int) -> list[int] | None:
            if start == len(text):
                return []

            for width in (1, 2):
                end = start + width
                if end > len(text):
                    continue
                score = int(text[start:end])
                if previous < score <= 32:
                    remainder = find_sequence(end, score)
                    if remainder is not None:
                        return [score, *remainder]
            return None

        sequence = find_sequence(0, -1)
        if sequence is None or len(sequence) < 3:
            return []
        return sequence

    def _read_substitutions(
        self,
        words: list[PositionedWord],
        panel: SetPanel,
        rotation: list[int],
        scale_x: float,
        scale_y: float
    ) -> list[Substitution]:
        events = []

        for slot, player_x in enumerate(panel.rotation_x):
            player = self._nearest_word(
                words,
                x=player_x * scale_x,
                y=(panel.rotation_y + 11) * scale_y,
                x_tolerance=8 * scale_x,
                y_tolerance=5 * scale_y,
                pattern=r"\d{1,2}"
            )
            if player is None:
                continue

            score_words = [
                word for word in words
                if abs(word.x0 - player_x * scale_x) <= 10 * scale_x
                and (panel.rotation_y + 19) * scale_y <= word.y0
                <= (panel.rotation_y + 42) * scale_y
                and re.fullmatch(r"\d{1,2}:\d{1,2}", word.text)
            ]

            for score in sorted(score_words, key=lambda item: item.y0):
                own, opponent = map(int, score.text.split(":"))
                events.append(
                    (score.y0, score.x0, slot, int(player.text), own, opponent)
                )

        substitutions = []
        players_on_court = rotation.copy()
        replacements = {
            slot: player_in
            for _, _, slot, player_in, _, _ in events
        }
        for _, _, slot, _, own, opponent in sorted(events):
            player_out = players_on_court[slot]
            player_in = replacements[slot]
            substitutions.append(
                Substitution(
                    player_in=player_in,
                    player_out=player_out,
                    own_score=own,
                    opp_score=opponent
                )
            )
            players_on_court[slot] = player_in
            replacements[slot] = player_out

        return substitutions

    @staticmethod
    def _nearest_word(
        words: list[PositionedWord],
        x: float,
        y: float,
        x_tolerance: float,
        y_tolerance: float,
        pattern: str
    ) -> PositionedWord | None:
        candidates = [
            word for word in words
            if re.fullmatch(pattern, word.text)
            and abs(word.x0 - x) <= x_tolerance
            and abs(word.y0 - y) <= y_tolerance
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda word: abs(word.x0 - x) + abs(word.y0 - y)
        )
