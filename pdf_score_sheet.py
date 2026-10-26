import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
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


@dataclass(frozen=True)
class SetPanelRegion:
    team_marker_x: float
    rotation_x: tuple[float, ...]
    score_x: float
    serve_x: tuple[float, ...]


@dataclass
class Substitution:
    player_in: int
    player_out: int
    score: dict[str, int]


@dataclass(frozen=True)
class ParsedPanelRegion:
    team: str
    rotation: list[int]
    points: int
    serve_rounds: dict[int, list[int]]
    substitutions: list[Substitution]


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
    player_names: dict[str, dict[str, str]] = field(default_factory=dict)
    match_date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            indent=2,
            ensure_ascii=False
        )


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

FIFTH_SET_REGIONS = (
    SetPanelRegion(
        team_marker_x=190.5,
        rotation_x=(130.4, 151.8, 170.9, 192.3, 216.2, 237.6),
        score_x=255.4,
        serve_x=(122.4, 143.9, 165.3, 186.8, 208.2, 229.7)
    ),
    SetPanelRegion(
        team_marker_x=345.6,
        rotation_x=(286.4, 307.8, 326.9, 348.3, 372.2, 393.6),
        score_x=411.4,
        serve_x=(278.4, 299.9, 321.3, 342.8, 364.2, 385.7)
    ),
    SetPanelRegion(
        team_marker_x=505.3,
        rotation_x=(442.4, 463.8, 482.9, 504.3, 525.8, 549.6),
        score_x=563.1,
        serve_x=(434.4, 455.9, 477.3, 498.8, 520.2, 541.7)
    )
)


class PdfScoreSheetParser:
    """Extract set score panels from a SAMS PDF's embedded text layer."""

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

    def parse_match(self, page: PdfPage) -> MatchJson:
        scale_x = page.width / 841.92
        scale_y = page.height / 595.92
        team_names = self._read_team_names(page.words)
        player_names = self._read_player_names(page.words, team_names)
        match_date = self._read_match_date(page.words)
        sets = []
        sets_won = {team: 0 for team in team_names.values()}
        for set_no, panel in enumerate(SET_PANELS, start=1):
            team_codes = self._panel_team_codes(
                page.words,
                panel,
                scale_x,
                scale_y
            )
            if team_codes == (None, None):
                if max(sets_won.values(), default=0) == 3:
                    break
                raise ValueError(
                    f"Set {set_no} is missing from the PDF before either "
                    "team reached three set wins"
                )
            if None in team_codes:
                raise ValueError(
                    f"Could not identify both team sides for set {set_no}"
                )
            set_data = self._parse_set(
                page.words,
                panel,
                team_names,
                scale_x,
                scale_y,
                set_no
            )
            sets.append(set_data)
            self._record_set_win(sets_won, set_data)
            if max(sets_won.values()) == 3:
                break

        if max(sets_won.values(), default=0) < 3:
            fifth_set = self._parse_fifth_set(
                page.words,
                team_names,
                scale_x,
                scale_y
            )
            if fifth_set is None:
                raise ValueError(
                    "The first four sets did not produce a winner and no "
                    "fifth set was found in the PDF"
                )
            sets.append(fifth_set)
            self._record_set_win(sets_won, fifth_set)
            if max(sets_won.values()) != 3:
                raise ValueError(
                    "The parsed fifth set did not produce a team with three "
                    "set wins"
                )
        return MatchJson(
            sets=sets,
            player_names=player_names,
            match_date=match_date
        )

    @staticmethod
    def _record_set_win(
        sets_won: dict[str, int],
        set_data: SetJson
    ) -> None:
        highest_score = max(set_data.points.values())
        winners = [
            team
            for team, score in set_data.points.items()
            if score == highest_score
        ]
        if len(winners) != 1:
            raise ValueError(
                f"Set {set_data.set_no} does not have a unique winning team"
            )
        sets_won[winners[0]] += 1

    def extract_player_names(
        self,
        pdf_file: str | Path
    ) -> dict[str, dict[str, str]]:
        page = self.extract_page(pdf_file)
        return self._read_player_names(
            page.words,
            self._read_team_names(page.words)
        )

    def extract_match_date(self, pdf_file: str | Path) -> str:
        return self._read_match_date(self.extract_page(pdf_file).words)

    def _read_match_date(self, words: list[PositionedWord]) -> str:
        header = " ".join(word.text for word in words if word.y0 < 110)
        match_date = re.search(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", header)
        if match_date is None:
            raise ValueError("Could not find the match date in the PDF header")
        return match_date.group(0)

    def _parse_fifth_set(
        self,
        words: list[PositionedWord],
        team_names: dict[str, str],
        scale_x: float,
        scale_y: float
    ) -> SetJson | None:
        team_codes = [
            self._nearest_word(
                words,
                x=region.team_marker_x * scale_x,
                y=299.3 * scale_y,
                x_tolerance=6 * scale_x,
                y_tolerance=3 * scale_y,
                pattern=r"[AB]"
            )
            for region in FIFTH_SET_REGIONS
        ]
        if all(code is None for code in team_codes):
            return None
        if any(code is None for code in team_codes):
            raise ValueError("Could not identify all three team headers in set 5")

        left_code, middle_code, right_code = [
            code.text for code in team_codes if code is not None
        ]
        if left_code != right_code or left_code == middle_code:
            raise ValueError(
                "Set 5 must show the same team in the left and right panels "
                "and the other team in the middle panel"
            )

        panel_data = [
            self._parse_panel_region(
                words,
                region,
                team_names[team_code],
                team_names["B" if team_code == "A" else "A"],
                scale_x,
                scale_y,
                5
            )
            for region, team_code in zip(
                FIFTH_SET_REGIONS,
                (left_code, middle_code, right_code)
            )
        ]
        left, middle, right = panel_data
        split_team = left.team
        if left.rotation != right.rotation:
            raise ValueError(
                f"Set 5: {split_team} has inconsistent rotations across "
                "the side-switch panels"
            )

        serve_rounds = {
            player: [
                *left.serve_rounds[player],
                *right.serve_rounds[player]
            ]
            for player in left.rotation
        }
        substitutions = {
            split_team: [
                *left.substitutions,
                *right.substitutions
            ],
            middle.team: middle.substitutions
        }
        return SetJson(
            set_no=5,
            start_rotation={
                split_team: left.rotation,
                middle.team: middle.rotation
            },
            serve_rounds={
                split_team: serve_rounds,
                middle.team: middle.serve_rounds
            },
            substitutions=substitutions,
            points={
                split_team: right.points,
                middle.team: middle.points
            }
        )

    def _parse_panel_region(
        self,
        words: list[PositionedWord],
        region: SetPanelRegion,
        team: str,
        opponent: str,
        scale_x: float,
        scale_y: float,
        set_no: int
    ) -> ParsedPanelRegion:
        rotation = self._read_rotation(
            words, region.rotation_x, 323.4,
            scale_x, scale_y, set_no, team
        )
        points = self._read_score(
            words, region.score_x * scale_x, 328.6 * scale_y,
            scale_x, scale_y, set_no, team
        )
        serve_rounds = self._read_serve_rounds(
            words, 367.6, region.serve_x, rotation, scale_x, scale_y
        )
        substitutions = self._read_substitutions(
            words, region.rotation_x, 323.4,
            rotation, team, opponent, scale_x, scale_y
        )
        return ParsedPanelRegion(
            team=team,
            rotation=rotation,
            points=points,
            serve_rounds=serve_rounds,
            substitutions=substitutions
        )

    def _read_player_names(
        self,
        words: list[PositionedWord],
        team_names: dict[str, str]
    ) -> dict[str, dict[str, str]]:
        player_names = {team: {} for team in team_names.values()}
        columns = (
            (585, 700, 693),
            (700, 820, 808)
        )
        for number_min, number_max, name_max in columns:
            team_markers = [
                word
                for word in words
                if word.text in {"A", "B"}
                and number_min <= word.x0 < number_max
                and 290 <= word.y0 <= 310
            ]
            if len(team_markers) != 1:
                raise ValueError(
                    "Could not determine the team for a player-roster column"
                )
            team_code = team_markers[0].text
            team_name = team_names[team_code]
            numbers = [
                word
                for word in words
                if number_min <= word.x0 < number_max
                and 300 <= word.y0 <= 445
                and re.fullmatch(r"\d{1,2}", word.text)
            ]
            for number in numbers:
                name_words = sorted(
                    (
                        word
                        for word in words
                        if number.x1 < word.x0 < name_max
                        and abs(word.y0 - number.y0) <= 4
                        and word.text != "C"
                    ),
                    key=lambda word: word.x0
                )
                if not name_words:
                    continue
                name = " ".join(word.text for word in name_words)
                last_name, separator, first_names = name.partition(",")
                first_name = first_names.strip().split()
                if not separator or not last_name.strip() or not first_name:
                    continue
                player_names[team_name][number.text] = (
                    f"{first_name[0]}, {last_name.strip()[0].upper()}."
                )
        return player_names

    def _read_team_names(
        self,
        words: list[PositionedWord]
    ) -> dict[str, str]:
        team_names, _ = self._read_team_header(words)
        return team_names

    def extract_team_header_order(self, pdf_file: str | Path) -> list[str]:
        page = self.extract_page(pdf_file)
        _, team_order = self._read_team_header(page.words)
        return team_order

    def _read_team_header(
        self,
        words: list[PositionedWord]
    ) -> tuple[dict[str, str], list[str]]:
        versus = next(
            (word for word in words if word.text.lower() == "vs."),
            None
        )
        if versus is None:
            raise ValueError("Could not find the team names in the PDF header")

        header_markers = [
            word
            for word in words
            if word.text in {"A", "B"}
            and abs(word.y0 - versus.y0) <= 4
        ]
        marker_a = min(
            (word for word in header_markers if word.text == "A"),
            key=lambda word: abs(word.x0 - versus.x0),
            default=None
        )
        marker_b = min(
            (word for word in header_markers if word.text == "B"),
            key=lambda word: abs(word.x0 - versus.x0),
            default=None
        )
        if marker_a is None or marker_b is None:
            raise ValueError("Could not find the team identifiers in the PDF header")

        if marker_a.x1 < versus.x0 and versus.x1 < marker_b.x0:
            team_a_bounds = (marker_a.x1, versus.x0)
            team_b_bounds = (versus.x1, marker_b.x0)
        elif marker_b.x1 < versus.x0 and versus.x1 < marker_a.x0:
            team_b_bounds = (marker_b.x1, versus.x0)
            team_a_bounds = (versus.x1, marker_a.x0)
        else:
            raise ValueError(
                "Could not determine team order from the PDF header identifiers"
            )

        header_words = sorted(
            (
                word for word in words
                if abs(word.y0 - versus.y0) <= 4
            ),
            key=lambda item: item.x0
        )
        team_a_words = [
            word.text
            for word in header_words
            if team_a_bounds[0] <= word.x0 and word.x1 <= team_a_bounds[1]
        ]
        team_b_words = [
            word.text
            for word in header_words
            if team_b_bounds[0] <= word.x0 and word.x1 <= team_b_bounds[1]
        ]
        if not team_a_words or not team_b_words:
            raise ValueError("Could not read both team names in the PDF header")

        team_names = {
            "A": " ".join(team_a_words),
            "B": " ".join(team_b_words)
        }
        team_order = [
            team_names[marker.text]
            for marker in sorted((marker_a, marker_b), key=lambda word: word.x0)
        ]
        return team_names, team_order

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
            left_rotation, left_team, right_team, scale_x, scale_y
        )
        right_substitutions = self._read_substitutions(
            words, panel.right_rotation_x, panel.rotation_y,
            right_rotation, right_team, left_team, scale_x, scale_y
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

    def _panel_team_codes(
        self,
        words: list[PositionedWord],
        panel: SetPanel,
        scale_x: float,
        scale_y: float
    ) -> tuple[str | None, str | None]:
        codes = []
        for x in (
            panel.left_team_marker_x,
            panel.right_team_marker_x
        ):
            marker = self._nearest_word(
                words,
                x=x * scale_x,
                y=panel.team_marker_y * scale_y,
                x_tolerance=6 * scale_x,
                y_tolerance=3 * scale_y,
                pattern=r"[AB]"
            )
            codes.append(marker.text if marker is not None else None)
        return codes[0], codes[1]

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
                    and word.y1 - word.y0 >= 18 * scale_y
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
        if sequence is None or len(sequence) < 2:
            return []
        return sequence

    def _read_substitutions(
        self,
        words: list[PositionedWord],
        rotation_x: tuple[float, ...],
        rotation_y: float,
        rotation: list[int],
        team: str,
        opponent: str,
        scale_x: float,
        scale_y: float
    ) -> list[Substitution]:
        replacement_players = {}
        for slot, player_x in enumerate(rotation_x):
            player = self._nearest_word(
                words,
                x=player_x * scale_x,
                y=(rotation_y + 11) * scale_y,
                x_tolerance=8 * scale_x,
                y_tolerance=5 * scale_y,
                pattern=r"\d{1,2}"
            )
            if player is not None:
                replacement_players[slot] = int(player.text)

        if not replacement_players:
            return []

        score_words = [
            word
            for word in words
            if min(rotation_x) * scale_x - 12 * scale_x
            <= word.x0
            <= max(rotation_x) * scale_x + 12 * scale_x
            and (rotation_y + 19) * scale_y <= word.y0
            <= (rotation_y + 42) * scale_y
            and re.search(r"\d{1,2}:\d{1,2}", word.text)
        ]
        events = []
        for score_word in score_words:
            score_matches = list(
                re.finditer(r"\d{1,2}:\d{1,2}", score_word.text)
            )
            for score_match in score_matches:
                token_x = score_word.x0 + (
                    (
                        score_match.start() + score_match.end()
                    ) / (2 * len(score_word.text))
                ) * (score_word.x1 - score_word.x0)
                slot = min(
                    replacement_players,
                    key=lambda candidate: abs(
                        token_x - rotation_x[candidate] * scale_x
                    )
                )
                if (
                    abs(token_x - rotation_x[slot] * scale_x)
                    > 12 * scale_x
                ):
                    continue
                team_score, opponent_score = map(
                    int,
                    score_match.group().split(":")
                )
                events.append(
                    (
                        score_word.y0,
                        token_x,
                        slot,
                        replacement_players[slot],
                        team_score,
                        opponent_score
                    )
                )

        substitutions = []
        players_on_court = rotation.copy()
        replacements = {
            slot: player_in
            for _, _, slot, player_in, _, _ in events
        }
        for _, _, slot, _, team_score, opponent_score in sorted(events):
            player_out = players_on_court[slot]
            player_in = replacements[slot]
            substitutions.append(
                Substitution(
                    player_in=player_in,
                    player_out=player_out,
                    score={
                        team: team_score,
                        opponent: opponent_score
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
