import re

from model import (
    MatchData,
    SetData,
    Substitution
)


class MatchTextParser:

    def __init__(self):
        pass

    def parse(self, text: str) -> MatchData:

        sets = []

        for set_no in range(1, 6):

            section = self._extract_set(
                text,
                set_no
            )

            if section is None:
                continue

            sets.append(
                self._parse_set(
                    set_no,
                    section
                )
            )

        return MatchData(
            sets=sets
        )

    def _extract_set(
        self,
        text,
        set_no
    ):

        pattern = rf"S\s*A\s*T\s*Z\s*{set_no}(.*?)(S\s*A\s*T\s*Z\s*{set_no+1}|Regionalliga|Ergebnisse)"

        m = re.search(
            pattern,
            text,
            re.DOTALL | re.IGNORECASE
        )

        if not m:
            return None

        return m.group(1)

    def _parse_start_rotation(
        self,
        section
    ):

        nums = re.findall(
            r"\b\d+\b",
            section
        )

        nums = [int(x) for x in nums]

        if len(nums) < 6:
            raise ValueError(
                "Could not determine start rotation"
            )

        return nums[:6]

    def _parse_serve_rounds(
        self,
        section,
        lineup
    ):

        rounds = {}

        lines = [
            line.strip()
            for line in section.splitlines()
            if line.strip()
        ]

        player_idx = 0

        for line in lines:

            if player_idx >= 6:
                break

            scores = re.findall(
                r"\d+",
                line
            )

            if not scores:
                continue

            scores = [
                int(x)
                for x in scores
            ]

            rounds[
                lineup[player_idx]
            ] = scores

            player_idx += 1

        return rounds

    def _parse_substitutions(
        self,
        section
    ):

        subs = []

        pattern = r"(\d+)\s+(\d+):(\d+)"

        matches = re.findall(
            pattern,
            section
        )

        for player_in, own, opp in matches:

            subs.append(
                Substitution(
                    player_out=-1,
                    player_in=int(player_in),
                    own_score=int(own),
                    opp_score=int(opp)
                )
            )

        return subs

    def _parse_final_score(
        self,
        section
    ):

        numbers = re.findall(
            r"\b\d+\b",
            section
        )

        numbers = [
            int(x)
            for x in numbers
        ]

        return (
            max(numbers),
            sorted(numbers)[-2]
        )

    def _parse_set(
        self,
        set_no,
        section
    ):

        lineup = self._parse_start_rotation(
            section
        )

        return SetData(

            set_no=set_no,

            start_rotation=lineup,

            own_serve_rounds=
                self._parse_serve_rounds(
                    section,
                    lineup
                ),

            opponent_serve_rounds={},

            substitutions=
                self._parse_substitutions(
                    section
                ),

            own_points=0,

            opp_points=0
        )
