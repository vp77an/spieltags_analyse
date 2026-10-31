import json
from pathlib import Path
from typing import Any


def load_match_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as match_file:
        match = json.load(match_file)
    if not isinstance(match, dict) or not isinstance(match.get("sets"), list):
        raise ValueError("Match JSON must contain a 'sets' list")
    return match


def reconstruct_match(match: dict[str, Any]) -> dict[str, Any]:
    reconstructed_sets = [
        (
            _reconstruct_u14_set(set_data)
            if match.get("match_type") == "U14"
            else _reconstruct_set(set_data)
        )
        for set_data in match["sets"]
    ]
    reconstruction = {"sets": reconstructed_sets}
    if "player_names" in match:
        reconstruction["player_names"] = match["player_names"]
    if "match_date" in match:
        reconstruction["match_date"] = match["match_date"]
    if "match_type" in match:
        reconstruction["match_type"] = match["match_type"]
    return reconstruction


def _reconstruct_u14_set(set_data: dict[str, Any]) -> dict[str, Any]:
    set_no = _integer(set_data["set_no"], "set number")
    rotations_data = set_data["start_rotation"]
    serve_rounds = set_data["serve_rounds"]
    substitutions_data = set_data["substitutions"]
    final_score_data = set_data["points"]
    teams = list(rotations_data)
    if len(teams) != 2:
        raise ValueError(f"Set {set_no} must have exactly two teams")
    if any(
        team not in serve_rounds
        or team not in substitutions_data
        or team not in final_score_data
        for team in teams
    ):
        raise ValueError(f"Set {set_no} has inconsistent team data")

    rotations = {
        team: _u14_rotation(rotations_data[team], set_no, team)
        for team in teams
    }
    slot_keys = {team: rotations[team][:] for team in teams}
    histories = {
        team: _normalize_u14_serve_rounds(
            serve_rounds[team], rotations[team], set_no, team
        )
        for team in teams
    }
    substitutions = {
        team: _normalize_substitutions(
            substitutions_data[team], teams, set_no, team
        )
        for team in teams
    }
    final_score = {
        team: _integer(final_score_data[team], "final score")
        for team in teams
    }
    target_score = 15 if set_no == 3 else 25
    if (
        max(final_score.values()) < target_score
        or abs(final_score[teams[0]] - final_score[teams[1]]) < 2
    ):
        raise ValueError(
            f"U14 set {set_no} must end at {target_score} or more "
            "with a two-point lead"
        )

    serving_team = set_data.get("first_serving_team")
    if not isinstance(serving_team, str) or serving_team not in teams:
        raise ValueError(
            f"Set {set_no}: first_serving_team must name one of the two teams"
        )
    scores = {team: 0 for team in teams}
    history_indexes = {
        team: {player: 0 for player in rotations[team]}
        for team in teams
    }
    substitution_indexes = {team: 0 for team in teams}
    points = []

    def apply_substitutions() -> None:
        for team in teams:
            team_substitutions = substitutions[team]
            while substitution_indexes[team] < len(team_substitutions):
                event = team_substitutions[substitution_indexes[team]]
                if event["score"] != scores:
                    break
                player_out = event["player_out"]
                player_in = event["player_in"]
                if player_out not in rotations[team]:
                    raise ValueError(
                        f"Set {set_no}: player {player_out} is not on court "
                        f"for {team} at substitution score {scores}"
                    )
                if player_in in rotations[team]:
                    raise ValueError(
                        f"Set {set_no}: player {player_in} is already on court "
                        f"for {team} at substitution score {scores}"
                    )
                rotations[team][rotations[team].index(player_out)] = player_in
                substitution_indexes[team] += 1

    def record_point(scoring_team: str) -> None:
        scores[scoring_team] += 1
        points.append(
            {
                "point_no": len(points) + 1,
                "rotations": {
                    team: rotations[team][:]
                    for team in teams
                },
                "serving_team": serving_team,
                "scoring_team": scoring_team,
                "score_after": scores.copy()
            }
        )
        apply_substitutions()

    def is_complete() -> bool:
        winner_score = max(scores.values())
        loser_score = min(scores.values())
        return (
            winner_score >= target_score
            and winner_score - loser_score >= 2
        )

    while not is_complete():
        server_slot = slot_keys[serving_team][0]
        history_index = history_indexes[serving_team][server_slot]
        history = histories[serving_team][server_slot]
        if history_index >= len(history):
            raise ValueError(
                f"Set {set_no}: no remaining U14 serve-round entry for "
                f"{serving_team} player {server_slot}"
            )
        endpoint = history[history_index]
        history_indexes[serving_team][server_slot] += 1
        delta = endpoint - scores[serving_team]
        if delta not in (0, 1, 2):
            raise ValueError(
                f"Set {set_no}: U14 serve-round score {endpoint} for "
                f"{serving_team} player {server_slot} is inconsistent with "
                f"current score {scores[serving_team]}"
            )

        receiving_team = next(team for team in teams if team != serving_team)
        for _ in range(delta):
            record_point(serving_team)
            if is_complete():
                break
        if is_complete():
            break
        if delta == 2:
            rotations[serving_team] = (
                rotations[serving_team][1:] + rotations[serving_team][:1]
            )
            slot_keys[serving_team] = (
                slot_keys[serving_team][1:] + slot_keys[serving_team][:1]
            )
            continue

        record_point(receiving_team)
        if is_complete():
            break
        rotations[receiving_team] = (
            rotations[receiving_team][1:] + rotations[receiving_team][:1]
        )
        slot_keys[receiving_team] = (
            slot_keys[receiving_team][1:] + slot_keys[receiving_team][:1]
        )
        serving_team = receiving_team

    if scores != final_score:
        raise ValueError(
            f"Set {set_no}: reconstructed score {scores} does not match "
            f"the recorded final score {final_score}"
        )
    for team in teams:
        for player, history in histories[team].items():
            if history_indexes[team][player] != len(history):
                raise ValueError(
                    f"Set {set_no}: unused U14 serve-round entries remain for "
                    f"{team} player {player}"
                )
        if substitution_indexes[team] != len(substitutions[team]):
            raise ValueError(
                f"Set {set_no}: substitution for {team} at "
                f"{substitutions[team][substitution_indexes[team]]['score']} "
                "was not reached"
            )
    return {"set_no": set_no, "points": points}


def _reconstruct_set(set_data: dict[str, Any]) -> dict[str, Any]:
    set_no = set_data["set_no"]
    rotations_data = set_data["start_rotation"]
    serve_rounds = set_data["serve_rounds"]
    substitutions = set_data["substitutions"]
    final_score = set_data["points"]
    teams = list(rotations_data)

    if len(teams) != 2:
        raise ValueError(f"Set {set_no} must have exactly two teams")
    if any(team not in serve_rounds or team not in substitutions for team in teams):
        raise ValueError(f"Set {set_no} has inconsistent team data")
    if any(team not in final_score for team in teams):
        raise ValueError(f"Set {set_no} is missing a final team score")

    rotations = {
        team: _six_player_rotation(rotations_data[team], set_no, team)
        for team in teams
    }
    slot_keys = {team: rotations[team][:] for team in teams}
    histories = {
        team: _normalize_serve_rounds(
            serve_rounds[team], rotations[team], set_no, team
        )
        for team in teams
    }
    opening_marks = {
        team: min(histories[team][rotations[team][0]])
        for team in teams
    }
    if opening_marks[teams[0]] == opening_marks[teams[1]]:
        raise ValueError(f"Set {set_no} has no unique opening serving team")

    serving_team = min(teams, key=opening_marks.__getitem__)
    receiving_team = next(team for team in teams if team != serving_team)
    scores = {team: 0 for team in teams}
    final_score = {team: _integer(final_score[team], "final score") for team in teams}
    history_indexes = {
        team: {player: 0 for player in rotations[team]}
        for team in teams
    }
    substitution_indexes = {team: 0 for team in teams}
    substitutions = {
        team: _normalize_substitutions(substitutions[team], teams, set_no, team)
        for team in teams
    }
    points = []

    def apply_substitutions() -> None:
        for team in teams:
            team_substitutions = substitutions[team]
            while substitution_indexes[team] < len(team_substitutions):
                event = team_substitutions[substitution_indexes[team]]
                if event["score"] != scores:
                    break
                player_out = event["player_out"]
                player_in = event["player_in"]
                if player_out not in rotations[team]:
                    raise ValueError(
                        f"Set {set_no}: player {player_out} is not on court "
                        f"for {team} at substitution score {scores}"
                    )
                if player_in in rotations[team]:
                    raise ValueError(
                        f"Set {set_no}: player {player_in} is already on court "
                        f"for {team} at substitution score {scores}"
                    )
                player_index = rotations[team].index(player_out)
                rotations[team][player_index] = player_in
                substitution_indexes[team] += 1

    def record_point(scoring_team: str) -> None:
        scores[scoring_team] += 1
        points.append(
            {
                "point_no": len(points) + 1,
                "rotations": {
                    team: rotations[team][:]
                    for team in teams
                },
                "serving_team": serving_team,
                "scoring_team": scoring_team,
                "score_after": {
                    team: scores[team]
                    for team in teams
                }
            }
        )

    while not _set_is_complete(scores, final_score):
        server_slot = slot_keys[serving_team][0]
        mark_index = history_indexes[serving_team][server_slot]
        service_history = histories[serving_team][server_slot]
        if mark_index >= len(service_history):
            raise ValueError(
                f"Set {set_no}: no remaining serve-round entry for "
                f"{serving_team} player {server_slot}"
            )

        service_endpoint = service_history[mark_index]
        history_indexes[serving_team][server_slot] += 1
        if service_endpoint < scores[serving_team]:
            raise ValueError(
                f"Set {set_no}: serve-round score {service_endpoint} is below "
                f"{serving_team}'s current score {scores[serving_team]}"
            )
        if service_endpoint > final_score[serving_team]:
            raise ValueError(
                f"Set {set_no}: serve-round score {service_endpoint} exceeds "
                f"{serving_team}'s final score {final_score[serving_team]}"
            )
        while scores[serving_team] < service_endpoint:
            record_point(serving_team)
            apply_substitutions()
            if _set_is_complete(scores, final_score):
                break

        if _set_is_complete(scores, final_score):
            break
        record_point(receiving_team)
        apply_substitutions()
        if _set_is_complete(scores, final_score):
            break

        rotations[receiving_team] = (
            rotations[receiving_team][1:] + rotations[receiving_team][:1]
        )
        slot_keys[receiving_team] = (
            slot_keys[receiving_team][1:] + slot_keys[receiving_team][:1]
        )
        serving_team, receiving_team = receiving_team, serving_team

    unused_marks = []
    for team in teams:
        if scores[team] != final_score[team]:
            raise ValueError(
                f"Set {set_no}: reconstructed score {scores} does not match "
                f"the recorded final score {final_score}"
            )
        for player, service_history in histories[team].items():
            remaining = service_history[history_indexes[team][player]:]
            if remaining:
                unused_marks.append((team, player, remaining))
        if substitution_indexes[team] != len(substitutions[team]):
            raise ValueError(
                f"Set {set_no}: substitution for {team} at "
                f"{substitutions[team][substitution_indexes[team]]['score']} "
                "was not reached"
            )

    if unused_marks:
        last_point = points[-1]
        # A final sideout may leave its marker under the next server's slot.
        is_terminal_sideout_mark = (
            len(unused_marks) == 1
            and len(unused_marks[0][2]) == 1
            and unused_marks[0][2][0] == final_score[unused_marks[0][0]]
            and slot_keys[unused_marks[0][0]][1] == unused_marks[0][1]
            and last_point["scoring_team"] == unused_marks[0][0]
            and last_point["serving_team"] != unused_marks[0][0]
        )
        if not is_terminal_sideout_mark:
            team, player, _ = unused_marks[0]
            raise ValueError(
                f"Set {set_no}: unused serve-round entries remain for "
                f"{team} player {player}"
            )

    return {"set_no": set_no, "points": points}


def _six_player_rotation(value: Any, set_no: int, team: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 6:
        raise ValueError(f"Set {set_no}: {team} must have six starting players")
    rotation = [_integer(player, "player number") for player in value]
    if len(set(rotation)) != 6:
        raise ValueError(f"Set {set_no}: {team} has duplicate starting players")
    return rotation


def _u14_rotation(value: Any, set_no: int, team: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"Set {set_no}: {team} must have four U14 players")
    rotation = [_integer(player, "player number") for player in value]
    if len(set(rotation)) != 4:
        raise ValueError(f"Set {set_no}: {team} has duplicate starting players")
    return rotation


def _normalize_u14_serve_rounds(
    value: Any,
    rotation: list[int],
    set_no: int,
    team: str
) -> dict[int, list[int]]:
    if not isinstance(value, dict):
        raise ValueError(f"Set {set_no}: invalid U14 serve rounds for {team}")
    histories = {}
    for player in rotation:
        values = value.get(str(player), value.get(player, []))
        if not isinstance(values, list):
            raise ValueError(
                f"Set {set_no}: invalid serve-round entries for "
                f"{team} player {player}"
            )
        histories[player] = [
            _integer(mark, "serve-round score")
            for mark in values
        ]
    return histories


def _normalize_serve_rounds(
    value: Any,
    rotation: list[int],
    set_no: int,
    team: str
) -> dict[int, list[int]]:
    if not isinstance(value, dict):
        raise ValueError(f"Set {set_no}: invalid serve rounds for {team}")
    histories = {}
    for player in rotation:
        values = value.get(str(player), value.get(player))
        if not isinstance(values, list) or not values:
            raise ValueError(
                f"Set {set_no}: missing serve-round entries for "
                f"{team} player {player}"
            )
        histories[player] = [_integer(mark, "serve-round score") for mark in values]
    return histories


def _normalize_substitutions(
    value: Any,
    teams: list[str],
    set_no: int,
    team: str
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"Set {set_no}: invalid substitutions for {team}")
    normalized = []
    for event in value:
        if not isinstance(event, dict):
            raise ValueError(f"Set {set_no}: invalid substitution for {team}")
        event_score = event.get("score")
        if not isinstance(event_score, dict) or any(
            name not in event_score for name in teams
        ):
            raise ValueError(
                f"Set {set_no}: substitution for {team} needs both team scores"
            )
        normalized.append(
            {
                "player_in": _integer(event.get("player_in"), "player number"),
                "player_out": _integer(event.get("player_out"), "player number"),
                "score": {
                    name: _integer(event_score[name], "substitution score")
                    for name in teams
                }
            }
        )
    return sorted(
        normalized,
        key=lambda event: sum(event["score"].values())
    )


def _integer(value: Any, description: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {description}: {value!r}") from error


def _set_is_complete(scores: dict[str, int], final_score: dict[str, int]) -> bool:
    return scores == final_score


def write_reconstruction(
    reconstruction: dict[str, Any],
    path: str | Path
) -> None:
    Path(path).write_text(
        json.dumps(reconstruction, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
