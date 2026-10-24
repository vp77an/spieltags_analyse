from dataclasses import dataclass
from dataclasses import field
from layouts import SET_LAYOUTS
import re
from typing import Protocol, Sequence


@dataclass
class Substitution:

    player_in: int
    player_out: int | None

    own_score: int
    opp_score: int


@dataclass
class SetJson:

    set_no: int

    start_rotation: list[int]

    serve_rounds: dict[int, list[int]]

    substitutions: list[Substitution] = field(
        default_factory=list
    )

    own_points: int = 0
    opp_points: int = 0

from dataclasses import dataclass


@dataclass
class MatchJson:

    sets: list[SetJson]


class TextCell(Protocol):
    text: str


class CellGrid:

    def __init__(self):

        self.cells: dict[tuple[int, int], str] = {}

    @classmethod
    def from_rows(
        cls,
        rows: Sequence[Sequence[TextCell]]
    ) -> "CellGrid":
        grid = cls()

        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                grid.put(row_idx, col_idx, cell.text)

        return grid

    def put(
        self,
        row: int,
        col: int,
        text: str
    ):

        self.cells[(row, col)] = text

    def get(
        self,
        row: int,
        col: int,
        default: str = ""
    ):

        return self.cells.get(
            (row, col),
            default
        )

class SamsScoreSheetMapper:

    def __init__(self):

        pass

    def map_match(
        self,
        grid: CellGrid
    ):

        sets = []

        for set_no in range(1, 5):

            sets.append(
                self.map_set(
                    grid,
                    set_no
                )
            )

        return MatchJson(
            sets=sets
        )

    def parse_start_rotation(
        self,
        grid,
        region
    ):

        rotation = []

        row = region.row1

        for col in range(
            region.col1,
            region.col2 + 1
        ):

            text = grid.get(
                row,
                col
            )

            if text:

                rotation.append(
                    int(text)
                )

        return rotation

    def parse_serve_rounds(
        self,
        grid,
        start_rotation,
        region
    ):

        rounds = {}

        for idx, player in enumerate(
            start_rotation
        ):

            col = region.col1 + idx

            scores = []

            for row in range(
                region.row1,
                region.row2 + 1
            ):

                text = grid.get(
                    row,
                    col
                )

                text = text.strip()

                if not text:
                    continue

                if not text.isdigit():
                    continue

                scores.append(
                    int(text)
                )

            rounds[player] = scores

        return rounds

    def parse_substitutions(
        self,
        grid,
        region
    ):

        substitutions = []

        for col in range(
            region.col1,
            region.col2 + 1
        ):

            player_text = grid.get(
                region.row1,
                col
            )

            score_text = grid.get(
                region.row1 + 1,
                col
            )

            if not player_text:

                continue

            m = re.match(
                r"(\d+):(\d+)",
                score_text
            )

            if not m:
                continue

            substitutions.append(

                Substitution(
                    player_in=int(
                        player_text
                    ),

                    player_out=None,

                    own_score=int(
                        m.group(1)
                    ),

                    opp_score=int(
                        m.group(2)
                    )
                )
            )

        return substitutions

    def layout(self, set_no):

        if set_no not in SET_LAYOUTS:

            raise ValueError(
                f"No layout configured for set {set_no}"
            )

        return SET_LAYOUTS[set_no]

    def read_score(
        self,
        grid,
        cell
    ):

        row, col = cell

        value = grid.get(
            row,
            col,
            "0"
        )

        try:
            return int(value)

        except ValueError:
            return 0

    def map_set(
        self,
        grid,
        set_no
    ):

        cfg = self.layout(set_no)

        rotation = self.parse_start_rotation(
            grid,
            cfg.start_rotation
        )

        rounds = self.parse_serve_rounds(
            grid,
            rotation,
            cfg.serve_rounds
        )

        substitutions = self.parse_substitutions(
            grid,
            cfg.substitutions
        )

        return SetJson(

            set_no=set_no,

            start_rotation=rotation,

            serve_rounds=rounds,

            substitutions=substitutions,

            own_points=self.read_score(
                grid,
                cfg.own_score
            ),

            opp_points=self.read_score(
                grid,
                cfg.opp_score
            )
        )
