import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path


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
    team_marker_y: float
    left_team_marker_x: float
    right_team_marker_x: float
    rotation_y: float
    left_rotation_x: tuple[float, ...]
    right_rotation_x: tuple[float, ...]
    score_y: float
    left_score_x: float
    right_score_x: float
    serve_y: float
    left_serve_x: tuple[float, ...]
    right_serve_x: tuple[float, ...]


@dataclass
class Substitution:
    player_in: int
    player_out: int
    score: dict[str, int]


@dataclass
class SetJson:
    set_no: int
    start_rotation: dict[str, list[int]]
    serve_rounds: dict[str, dict[int, list[int]]]
    substitutions: dict[str, list[Substitution]]
    points: dict[str, int]


@dataclass
class MatchJson:
    sets: list[SetJson]


SET_PANELS = (
    SetPanel(
        team_marker_y=71.3,
        left_team_marker_x=190.5,
        right_team_marker_x=345.6,
        rotation_y=95.4,
        left_rotation_x=(130.4, 151.8, 170.9, 192.3, 216.2, 237.6),
        right_rotation_x=(286.4, 307.8, 326.9, 348.3, 372.2, 393.6),
        score_y=100.6,
        left_score_x=255.4,
        right_score_x=411.4,
        serve_y=139.6,
        left_serve_x=(122.4, 143.9, 165.3, 186.8, 208.2, 229.7),
        right_serve_x=(278.4, 299.9, 321.3, 342.8, 364.2, 385.7)
    ),
    SetPanel(
        team_marker_y=71.3,
        left_team_marker_x=520.3,
        right_team_marker_x=677.1,
        rotation_y=95.4,
        left_rotation_x=(461.1, 480.1, 501.6, 525.4, 546.8, 568.3),
        right_rotation_x=(614.7, 636.1, 658.1, 681.4, 702.8, 724.3),
        score_y=100.6,
        left_score_x=586.1,
        right_score_x=742.1,
        serve_y=139.6,
        left_serve_x=(453.1, 474.6, 496.0, 517.5, 538.9, 560.3),
        right_serve_x=(609.1, 630.6, 652.0, 673.5, 694.9, 716.3)
    ),
    SetPanel(
        team_marker_y=184.5,
        left_team_marker_x=190.5,
        right_team_marker_x=345.6,
        rotation_y=208.0,
        left_rotation_x=(130.4, 151.8, 170.9, 192.3, 216.2, 237.6),
        right_rotation_x=(286.4, 307.8, 326.9, 348.3, 372.2, 393.6),
        score_y=214.6,
        left_score_x=255.4,
        right_score_x=411.4,
        serve_y=253.6,
        left_serve_x=(122.4, 143.9, 165.3, 186.8, 208.2, 229.7),
        right_serve_x=(278.4, 299.9, 321.3, 342.8, 364.2, 385.7)
    ),
    SetPanel(
        team_marker_y=184.5,
        left_team_marker_x=520.3,
        right_team_marker_x=677.1,
        rotation_y=208.0,
        left_rotation_x=(461.1, 480.1, 501.6, 525.4, 546.8, 568.3),
        right_rotation_x=(614.7, 636.1, 658.1, 681.4, 702.8, 724.3),
        score_y=214.6,
        left_score_x=586.1,
        right_score_x=742.1,
        serve_y=253.6,
        left_serve_x=(453.1, 474.6, 496.0, 517.5, 538.9, 560.3),
        right_serve_x=(609.1, 630.6, 652.0, 673.5, 694.9, 716.3)
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
                "No embedded text found in the PDF; this parser requires a formatted "
                "score sheet with an embedded text layer"
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
        team_names = self._read_team_names(page.words)
        sets = [
            self._parse_set(
                page.words,
                panel,
                team_names,
                scale_x,
                scale_y,
                set_no
            )
            for set_no, panel in enumerate(SET_PANELS, start=1)
        ]
        return MatchJson(sets=sets)

    def _read_team_names(
        self,
        words: list[PositionedWord]
    ) -> dict[str, str]:
        versus = next(
            (word for word in words if word.text.lower() == "vs."),
            None
        )
        if versus is None:
            raise ValueError("Could not find the team names in the PDF header")

        marker_a = self._nearest_word(
            words,
            x=(versus.x0 - 65),
            y=versus.y0,
            x_tolerance=100,
            y_tolerance=4,
            pattern=r"A"
        )
        marker_b = self._nearest_word(
            words,
            x=(versus.x1 + 65),
            y=versus.y0,
            x_tolerance=100,
            y_tolerance=4,
            pattern=r"B"
        )
        if marker_a is None or marker_b is None:
            raise ValueError("Could not find the team identifiers in the PDF header")

        header_words = [
            word for word in words
            if abs(word.y0 - versus.y0) <= 4
        ]
        team_a_words = [
            word.text for word in sorted(header_words, key=lambda item: item.x0)
            if word.x0 >= marker_a.x1 and word.x1 <= versus.x0
        ]
        team_b_words = [
            word.text for word in sorted(header_words, key=lambda item: item.x0)
            if word.x0 >= versus.x1 and word.x1 <= marker_b.x0
        ]
        if not team_a_words or not team_b_words:
            raise ValueError("Could not read both team names in the PDF header")

        return {
            "A": " ".join(team_a_words),
            "B": " ".join(team_b_words)
        }

    def _parse_set(
        self,
        words: list[PositionedWord],
        panel: SetPanel,
        team_names: dict[str, str],
        scale_x: float,
        scale_y: float,
        set_no: int
    ) -> SetJson:
        left_team_code = self._read_team_code(
            words, panel.left_team_marker_x * scale_x,
            panel.team_marker_y * scale_y, scale_x, scale_y, set_no
        )
        right_team_code = self._read_team_code(
            words, panel.right_team_marker_x * scale_x,
            panel.team_marker_y * scale_y, scale_x, scale_y, set_no
        )
        if left_team_code == right_team_code:
            raise ValueError(f"Both sides of set {set_no} have the same team marker")

        left_team = team_names[left_team_code]
        right_team = team_names[right_team_code]
        left_rotation = self._read_rotation(
            words, panel.left_rotation_x, panel.rotation_y,
            scale_x, scale_y, set_no, left_team
        )
        right_rotation = self._read_rotation(
            words, panel.right_rotation_x, panel.rotation_y,
            scale_x, scale_y, set_no, right_team
        )

        left_points = self._read_score(
            words, panel.left_score_x * scale_x, panel.score_y * scale_y,
            scale_x, scale_y, set_no, left_team
        )
        right_points = self._read_score(
            words, panel.right_score_x * scale_x, panel.score_y * scale_y,
            scale_x, scale_y, set_no, right_team
        )

        left_serve_rounds = self._read_serve_rounds(
            words, panel.serve_y, panel.left_serve_x,
            left_rotation, scale_x, scale_y
        )
        right_serve_rounds = self._read_serve_rounds(
            words, panel.serve_y, panel.right_serve_x,
            right_rotation, scale_x, scale_y
        )
        left_substitutions = self._read_substitutions(
            words, panel.left_rotation_x, panel.rotation_y,
            left_rotation, team_names, scale_x, scale_y
        )
        right_substitutions = self._read_substitutions(
            words, panel.right_rotation_x, panel.rotation_y,
            right_rotation, team_names, scale_x, scale_y
        )

        return SetJson(
            set_no=set_no,
            start_rotation={
                left_team: left_rotation,
                right_team: right_rotation
            },
            serve_rounds={
                left_team: left_serve_rounds,
                right_team: right_serve_rounds
            },
            substitutions={
                left_team: left_substitutions,
                right_team: right_substitutions
            },
            points={
                left_team: left_points,
                right_team: right_points
            }
        )

    def _read_team_code(
        self,
        words: list[PositionedWord],
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        set_no: int
    ) -> str:
        marker = self._nearest_word(
            words,
            x=x,
            y=y,
            x_tolerance=6 * scale_x,
            y_tolerance=3 * scale_y,
            pattern=r"[AB]"
        )
        if marker is None:
            raise ValueError(f"Could not identify a team side for set {set_no}")
        return marker.text

    def _read_rotation(
        self,
        words: list[PositionedWord],
        positions: tuple[float, ...],
        y: float,
        scale_x: float,
        scale_y: float,
        set_no: int,
        team: str
    ) -> list[int]:
        rotation = []
        for target_x in positions:
            word = self._nearest_word(
                words,
                x=target_x * scale_x,
                y=y * scale_y,
                x_tolerance=8 * scale_x,
                y_tolerance=3 * scale_y,
                pattern=r"\d{1,2}"
            )
            if word is None:
                raise ValueError(
                    f"Could not read starting rotation position {len(rotation) + 1} "
                    f"for {team} in set {set_no}"
                )
            rotation.append(int(word.text))
        return rotation

    def _read_score(
        self,
        words: list[PositionedWord],
        x: float,
        y: float,
        scale_x: float,
        scale_y: float,
        set_no: int,
        team: str
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
            raise ValueError(f"Could not read {team} score for set {set_no}")
        return int(word.text)

    def _read_serve_rounds(
        self,
        words: list[PositionedWord],
        serve_y: float,
        serve_x: tuple[float, ...],
        rotation: list[int],
        scale_x: float,
        scale_y: float
    ) -> dict[int, list[int]]:
        rounds = {player: [] for player in rotation}

        for player, target_x in zip(rotation, serve_x):
            column_words = [
                word for word in words
                if abs(word.x0 - target_x * scale_x) <= 8 * scale_x
                and serve_y * scale_y <= word.y0
                <= (serve_y + 34) * scale_y
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
        rotation_x: tuple[float, ...],
        rotation_y: float,
        rotation: list[int],
        team_names: dict[str, str],
        scale_x: float,
        scale_y: float
    ) -> list[Substitution]:
        events = []

        for slot, player_x in enumerate(rotation_x):
            player = self._nearest_word(
                words,
                x=player_x * scale_x,
                y=(rotation_y + 11) * scale_y,
                x_tolerance=8 * scale_x,
                y_tolerance=5 * scale_y,
                pattern=r"\d{1,2}"
            )
            if player is None:
                continue

            score_words = [
                word for word in words
                if abs(word.x0 - player_x * scale_x) <= 10 * scale_x
                and (rotation_y + 19) * scale_y <= word.y0
                <= (rotation_y + 42) * scale_y
                and re.fullmatch(r"\d{1,2}:\d{1,2}", word.text)
            ]

            for score in sorted(score_words, key=lambda item: item.y0):
                team_a_score, team_b_score = map(
                    int,
                    score.text.split(":")
                )
                events.append(
                    (
                        score.y0,
                        score.x0,
                        slot,
                        int(player.text),
                        team_a_score,
                        team_b_score
                    )
                )

        substitutions = []
        players_on_court = rotation.copy()
        replacements = {
            slot: player_in
            for _, _, slot, player_in, _, _ in events
        }
        for _, _, slot, _, team_a_score, team_b_score in sorted(events):
            player_out = players_on_court[slot]
            player_in = replacements[slot]
            substitutions.append(
                Substitution(
                    player_in=player_in,
                    player_out=player_out,
                    score={
                        team_names["A"]: team_a_score,
                        team_names["B"]: team_b_score
                    }
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
