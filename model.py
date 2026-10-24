from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass
class Region:

    row1: int
    row2: int

    col1: int
    col2: int

@dataclass
class Substitution:
    player_out: int
    player_in: int
    own_score: int
    opp_score: int


@dataclass
class SetLayout:

    start_rotation: Region

    serve_rounds: Region

    substitutions: Region

    own_score: tuple[int, int]

    opp_score: tuple[int, int]

@dataclass
class SetData:

    set_no: int

    start_rotation: List[int]

    own_serve_rounds: Dict[int, List[int]]

    opponent_serve_rounds: Dict[int, List[int]]

    substitutions: List[Substitution]

    own_points: int
    opp_points: int


@dataclass
class MatchData:

    sets: List[SetData]

    def to_json(self):
        import json

        return json.dumps(
            asdict(self),
            indent=2
        )
