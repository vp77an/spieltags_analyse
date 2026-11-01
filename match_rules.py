TWO_SET_MATCH_TYPES = frozenset({"U14", "U16", "U18", "U20"})


def sets_to_win(match_type: str | None) -> int:
    return 2 if match_type in TWO_SET_MATCH_TYPES else 3


def is_deciding_set(match_type: str | None, set_no: int) -> bool:
    deciding_set_no = 3 if match_type in TWO_SET_MATCH_TYPES else 5
    return set_no == deciding_set_no


def set_target_score(match_type: str | None, set_no: int) -> int:
    return 15 if is_deciding_set(match_type, set_no) else 25
