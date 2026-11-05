import colorsys
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Image,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle
)

matplotlib.use("Agg")

import matplotlib.pyplot as plt


ROMAN_NUMERALS = ("I", "II", "III", "IV", "V", "VI")


def load_point_by_point(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as point_file:
        data = json.load(point_file)
    if not isinstance(data, dict) or not isinstance(data.get("sets"), list):
        raise ValueError("Point-by-point JSON must contain a 'sets' list")
    return data


def analyze_rotation_runs(
    point_data: dict[str, Any],
    teams: list[dict[str, Any]],
    output_dir: str | Path
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    is_u14 = point_data.get("match_type") == "U14"
    player_count = 4 if is_u14 else 6
    team_names = {
        team["name"]: team["setters"]
        for team in teams
    }
    rotation_data: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
        team_name: {} for team_name in team_names
    }
    service_run_data: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = {
        team_name: {} for team_name in team_names
    }
    points_by_setter_position: dict[str, Counter[str]] = {
        team_name: Counter()
        for team_name in team_names
    }
    points_by_lineup: dict[
        str, dict[tuple[int, ...], Counter[str]]
    ] = {
        team_name: {}
        for team_name in team_names
    }
    points_by_player: dict[str, dict[int, Counter[str]]] = {
        team_name: {}
        for team_name in team_names
    }
    serving_player_stats: dict[str, dict[int, Counter[str]]] = {
        team_name: {}
        for team_name in team_names
    }

    for set_data in point_data["sets"]:
        set_no = set_data.get("set_no")
        points = set_data.get("points")
        if not isinstance(points, list):
            raise ValueError(f"Set {set_no}: point list is missing")
        if not points:
            continue

        setters_by_team = {}
        for team_name in team_names:
            if team_name not in points[0].get("rotations", {}):
                raise ValueError(
                    f"Set {set_no}: team {team_name!r} is missing from point data"
                )
            setters_by_team[team_name] = team_names[team_name]
            for point in points:
                if point.get("scoring_team") not in team_names:
                    raise ValueError(
                        f"Set {set_no}: invalid scoring team "
                        f"{point.get('scoring_team')!r}"
                    )
                rotation = _rotation_for(
                    point, team_name, set_no, player_count
                )
                if point.get("serving_team") == team_name:
                    serving_player = rotation[0]
                    serving_stats = serving_player_stats[team_name].setdefault(
                        serving_player,
                        Counter()
                    )
                    serving_stats["points_served"] += 1
                    if point["scoring_team"] != team_name:
                        serving_stats["opponent_points"] += 1

                rotation_key = _rotation_key(
                    rotation, setters_by_team[team_name], is_u14=is_u14
                )
                rotation_data[team_name].setdefault(
                    rotation_key,
                    {"runs": [], "counts": Counter(), "lineups": set()}
                )
                rotation_data[team_name][rotation_key]["lineups"].add(rotation)

                lineup_totals = points_by_lineup[team_name].setdefault(
                    tuple(sorted(rotation)),
                    Counter()
                )
                lineup_result = (
                    "own_points"
                    if point["scoring_team"] == team_name
                    else "opponent_points"
                )
                lineup_totals[lineup_result] += 1
                for jersey in rotation:
                    player_totals = points_by_player[team_name].setdefault(
                        jersey,
                        Counter()
                    )
                    player_totals["points_played"] += 1
                    player_totals[lineup_result] += 1

                if not is_u14:
                    setter_position = _setter_position(
                        rotation, setters_by_team[team_name]
                    )
                    point_result = (
                        ":own"
                        if point["scoring_team"] == team_name
                        else ":opponent"
                    )
                    points_by_setter_position[team_name][
                        _setter_position_label(setter_position) + point_result
                    ] += 1

            index = 0
            while index < len(points):
                scoring_team = points[index].get("scoring_team")
                if scoring_team == team_name:
                    index += 1
                    continue

                rotation = _rotation_for(
                    points[index], team_name, set_no, player_count
                )
                rotation_key = _rotation_key(
                    rotation, setters_by_team[team_name], is_u14=is_u14
                )
                end_index = index + 1
                while end_index < len(points):
                    next_rotation = _rotation_for(
                        points[end_index],
                        team_name,
                        set_no,
                        player_count
                    )
                    if (
                        points[end_index].get("scoring_team") == team_name
                        or _rotation_key(
                            next_rotation,
                            setters_by_team[team_name],
                            is_u14=is_u14
                        ) != rotation_key
                    ):
                        break
                    end_index += 1

                run_length = end_index - index
                stats = rotation_data[team_name][rotation_key]
                stats["counts"][run_length] += 1
                stats["runs"].append(
                    {
                        "set_no": set_no,
                        "start_point": points[index].get("point_no", index + 1),
                        "end_point": points[end_index - 1].get(
                            "point_no", end_index
                        ),
                        "length": run_length
                    }
                )
                index = end_index

        index = 0
        while index < len(points):
            serving_team = points[index].get("serving_team")
            if serving_team not in team_names:
                raise ValueError(
                    f"Set {set_no}: invalid serving team {serving_team!r}"
                )
            end_index = index + 1
            while (
                end_index < len(points)
                and points[end_index].get("serving_team") == serving_team
            ):
                end_index += 1

            rotation = _rotation_for(
                points[index], serving_team, set_no, player_count
            )
            rotation_key = (
                ("all",)
                if is_u14
                else _rotation_key(
                    rotation,
                    setters_by_team[serving_team]
                )
            )
            stats = service_run_data[serving_team].setdefault(
                rotation_key,
                {"runs": [], "counts": Counter(), "lineups": set()}
            )
            stats["lineups"].add(rotation)
            scoring_points = sum(
                point.get("scoring_team") == serving_team
                for point in points[index:end_index]
            )
            stats["counts"][scoring_points] += 1
            lost_serve_point = next(
                (
                    point.get("point_no", point_index + 1)
                    for point_index, point in enumerate(
                        points[index:end_index], start=index
                    )
                    if point.get("scoring_team") != serving_team
                ),
                None
            )
            stats["runs"].append(
                {
                    "set_no": set_no,
                    "start_point": points[index].get("point_no", index + 1),
                    "end_point": points[end_index - 1].get(
                        "point_no", end_index
                    ),
                    "length": scoring_points,
                    "lost_serve_at_point": lost_serve_point
                }
            )
            index = end_index

    report = {"teams": []}
    service_report = {"teams": []}
    points_report = {"teams": []}
    performance_report = {"teams": []}
    player_names = point_data.get("player_names", {})
    for team_name, setters in team_names.items():
        rotations = rotation_data[team_name]
        rotation_items = sorted(
            rotations.items(),
            key=lambda item: (
                0 if item[0][0] == "setter" else 1,
                item[0][1]
            )
        )
        rotation_results = []
        for rotation_key, stats in rotation_items:
            distribution = {
                str(length): stats["counts"][length]
                for length in sorted(stats["counts"])
            }
            rotation_results.append(
                {
                    "lineups": [
                        list(lineup)
                        for lineup in sorted(stats["lineups"])
                    ],
                    "label": _rotation_label(
                        rotation_key,
                        stats["lineups"],
                        setters,
                        is_u14=is_u14,
                        player_names=player_names.get(team_name, {})
                    ),
                    "run_length_distribution": distribution,
                    "mean_run_length": _mean_run_length(stats["runs"]),
                    "runs": stats["runs"]
                }
            )

        report["teams"].append(
            {
                "team": team_name,
                "rotations": rotation_results
            }
        )
        _plot_team_distribution(
            team_name,
            rotation_results,
            output_path / f"{_filename_slug(team_name)}_rotation_runs.png",
            u14=is_u14
        )

        service_results = _distribution_results(
            service_run_data[team_name],
            setters,
            is_u14=is_u14,
            player_names=player_names.get(team_name, {})
        )
        service_report["teams"].append({
            "team": team_name,
            ("rotations" if is_u14 else "setter_positions"): service_results
        })
        _plot_service_run_distribution(
            team_name,
            service_results,
            output_path / f"{_filename_slug(team_name)}_service_runs.png",
            u14=is_u14
        )

        if not is_u14:
            point_totals = _point_totals_results(
                points_by_setter_position[team_name]
            )
            points_report["teams"].append(
                {"team": team_name, "setter_positions": point_totals}
            )
            _plot_points_by_setter_position(
                team_name,
                point_totals,
                output_path
                / f"{_filename_slug(team_name)}_points_by_setter_position.png"
            )
        performance_report["teams"].append(
            {
                "team": team_name,
                "lineups": _lineup_performance_results(
                    points_by_lineup[team_name],
                    player_names.get(team_name, {})
                ),
                "players": _player_performance_results(
                    points_by_player[team_name],
                    player_names.get(team_name, {})
                ),
                "serving_players": _serving_player_results(
                    serving_player_stats[team_name],
                    player_names.get(team_name, {})
                )
            }
        )

    (output_path / "rotation_run_distributions.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    (output_path / "service_run_distributions.json").write_text(
        json.dumps(service_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    if is_u14:
        (output_path / "points_by_setter_position.json").unlink(
            missing_ok=True
        )
        for team_name in team_names:
            (
                output_path
                / f"{_filename_slug(team_name)}_points_by_setter_position.png"
            ).unlink(missing_ok=True)
    else:
        (output_path / "points_by_setter_position.json").write_text(
            json.dumps(points_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8"
        )
    match_result = _match_result(point_data["sets"], list(team_names))
    score_progressions = _set_score_progressions(
        point_data["sets"],
        list(team_names),
        player_names,
        {
            team["name"]: team["setters"]
            for team in teams
        }
    )
    service_run_sections = _service_run_report_sections(
        performance_report,
        report,
        service_report,
        is_u14
    )
    front_matter = _report_front_matter(
        list(team_names),
        point_data["match_date"],
        match_result,
        _setter_start_positions(
            point_data["sets"],
            teams,
            player_count
        ),
        score_progressions
    )
    _write_markdown_report(
        output_path,
        performance_report,
        service_run_sections,
        front_matter
    )
    return report


def _match_result(
    set_data_list: list[dict[str, Any]],
    team_names: list[str]
) -> dict[str, Any]:
    if len(team_names) != 2:
        raise ValueError("The match report requires exactly two team names")
    set_results = []
    sets_won = {team_name: 0 for team_name in team_names}
    for set_data in set_data_list:
        points = set_data.get("points")
        if not isinstance(points, list):
            raise ValueError(
                f"Set {set_data.get('set_no')}: point list is missing"
            )
        if not points:
            continue
        final_score = points[-1].get("score_after")
        if (
            not isinstance(final_score, dict)
            or any(team_name not in final_score for team_name in team_names)
        ):
            raise ValueError(
                f"Set {set_data.get('set_no')}: final score is missing"
            )
        team_a_score = final_score[team_names[0]]
        team_b_score = final_score[team_names[1]]
        if (
            not isinstance(team_a_score, int)
            or not isinstance(team_b_score, int)
            or team_a_score < 0
            or team_b_score < 0
        ):
            raise ValueError(
                f"Set {set_data.get('set_no')}: invalid final score"
            )
        if team_a_score > team_b_score:
            sets_won[team_names[0]] += 1
        elif team_b_score > team_a_score:
            sets_won[team_names[1]] += 1
        set_results.append(
            {
                "set_no": set_data.get("set_no"),
                "team_a_score": team_a_score,
                "team_b_score": team_b_score
            }
        )
    return {"sets_won": sets_won, "set_scores": set_results}


def _set_score_progressions(
    set_data_list: list[dict[str, Any]],
    team_names: list[str],
    player_names: dict[str, dict[str, str]],
    setter_numbers: dict[str, list[int]]
) -> list[dict[str, Any]]:
    if len(team_names) != 2:
        raise ValueError("The score progression requires exactly two teams")

    progressions = []
    for set_data in set_data_list:
        set_no = set_data.get("set_no")
        points = set_data.get("points")
        if not isinstance(points, list):
            raise ValueError(f"Set {set_no}: point list is missing")
        if not points:
            continue
        opening_serving_team = points[0].get("serving_team")
        if opening_serving_team not in team_names:
            raise ValueError(f"Set {set_no}: invalid opening serving team")

        timeouts_by_point: dict[int, list[dict[str, Any]]] = {
            index: [] for index in range(len(points))
        }
        for timeout in set_data.get("timeouts", []):
            timeout_team = timeout.get("team")
            team_score = timeout.get("team_score")
            opponent_score = timeout.get("opponent_score")
            if (
                timeout_team not in team_names
                or any(
                    not isinstance(score, int)
                    or isinstance(score, bool)
                    or score < 0
                    for score in (team_score, opponent_score)
                )
            ):
                raise ValueError(f"Set {set_no}: invalid timeout score")
            opponent = next(
                team_name
                for team_name in team_names
                if team_name != timeout_team
            )
            matching_points = [
                index
                for index, point in enumerate(points)
                if point["score_after"].get(timeout_team) == team_score
                and point["score_after"].get(opponent) == opponent_score
            ]
            if len(matching_points) != 1:
                raise ValueError(
                    f"Set {set_no}: timeout score "
                    f"{team_score}:{opponent_score} for {timeout_team} "
                    "does not match exactly one rally"
                )
            timeouts_by_point[matching_points[0]].append(timeout)

        previous_lineups: dict[str, set[int] | None] = {
            team_name: None for team_name in team_names
        }
        rallies = []
        for point in points:
            scoring_team = point.get("scoring_team")
            serving_team = point.get("serving_team")
            score_after = point.get("score_after")
            if scoring_team not in team_names or serving_team not in team_names:
                raise ValueError(
                    f"Set {set_no}: invalid serving or scoring team"
                )
            if (
                not isinstance(score_after, dict)
                or any(team_name not in score_after for team_name in team_names)
                or any(
                    not isinstance(score_after[team_name], int)
                    or isinstance(score_after[team_name], bool)
                    or score_after[team_name] < 0
                    for team_name in team_names
                )
            ):
                raise ValueError(
                    f"Set {set_no}: invalid score after rally"
                )

            rotations = {}
            for team_name in team_names:
                rotation = point.get("rotations", {}).get(team_name)
                if (
                    not isinstance(rotation, list)
                    or any(
                        not isinstance(jersey, int)
                        or isinstance(jersey, bool)
                        or jersey < 1
                        for jersey in rotation
                    )
                ):
                    raise ValueError(
                        f"Set {set_no}: invalid rotation for {team_name!r}"
                    )
                current_lineup = set(rotation)
                previous_lineup = previous_lineups[team_name]
                substitutions = (
                    current_lineup - previous_lineup
                    if previous_lineup is not None
                    else set()
                )
                rotations[team_name] = [
                    {
                        "jersey": jersey,
                        "name": player_names.get(team_name, {}).get(
                            str(jersey)
                        ),
                        "is_setter": jersey in setter_numbers.get(
                            team_name, []
                        ),
                        "is_substitution": jersey in substitutions
                    }
                    for jersey in rotation
                ]
                previous_lineups[team_name] = current_lineup

            rallies.append(
                {
                    "point_no": point.get("point_no"),
                    "scoring_team": scoring_team,
                    "serving_team": serving_team,
                    "score_after": score_after,
                    "rotations": rotations,
                    "timeouts": timeouts_by_point[len(rallies)]
                }
            )

        progressions.append(
            {
                "set_no": set_no,
                "opening_serving_team": opening_serving_team,
                "rallies": rallies
            }
        )
    return progressions


def _player_label_markup(
    jersey: int,
    name: str | None = None,
    highlighted: bool = False,
    markdown: bool = False,
    setter: bool = False
) -> str:
    label = f"<b>{jersey}</b>"
    if name:
        label += f" ({escape(name)})"
    if not highlighted:
        if not setter:
            return label
        if markdown:
            return f'<span style="background-color: #cfe2f3">{label}</span>'
        return f'<font backColor="#cfe2f3">{label}</font>'
    if markdown:
        return f'<span style="background-color: #d9ead3">{label}</span>'
    return f'<font backColor="#d9ead3">{label}</font>'


def _setter_start_positions(
    set_data_list: list[dict[str, Any]],
    teams: list[dict[str, Any]],
    player_count: int
) -> list[dict[str, Any]]:
    if not any(team["setters"] for team in teams):
        return []

    positions = []
    for set_data in set_data_list:
        points = set_data.get("points")
        if not isinstance(points, list):
            raise ValueError(
                f"Set {set_data.get('set_no')}: point list is missing"
            )
        if not points:
            continue

        serving_team = points[0].get("serving_team")
        team_rotations = []
        for team in teams:
            team_name = team["name"]
            rotation = _rotation_for(
                points[0],
                team_name,
                set_data.get("set_no"),
                player_count
            )
            setters = set(team["setters"])
            starting_setters = [
                f"#{_player_label_markup(jersey)}: "
                f"Position {ROMAN_NUMERALS[position]}"
                for position, jersey in enumerate(rotation)
                if jersey in setters
            ]
            team_rotations.append(
                {
                    "team": team_name,
                    "setters": (
                        "; ".join(starting_setters)
                        if starting_setters
                        else "No configured setter in starting lineup"
                    ),
                    "start_type": (
                        "Own serve"
                        if serving_team == team_name
                        else "Reception"
                    )
                }
            )
        positions.extend(
            {
                "set_no": set_data.get("set_no"),
                **team_rotation
            }
            for team_rotation in team_rotations
        )
    return positions


def _rotation_for(
    point: dict[str, Any],
    team_name: str,
    set_no: Any,
    player_count: int = 6
) -> tuple[int, ...]:
    rotations = point.get("rotations")
    if not isinstance(rotations, dict) or team_name not in rotations:
        raise ValueError(
            f"Set {set_no}: team {team_name!r} has no rotation in point data"
        )
    rotation = rotations[team_name]
    if (
        not isinstance(rotation, list)
        or len(rotation) != player_count
        or any(
            not isinstance(player, int) or isinstance(player, bool)
            for player in rotation
        )
    ):
        raise ValueError(
            f"Set {set_no}: invalid {player_count}-player rotation for "
            f"{team_name!r}"
        )
    return tuple(rotation)


def _rotation_key(
    rotation: tuple[int, ...],
    setters: list[int],
    is_u14: bool = False
) -> tuple[Any, ...]:
    if is_u14:
        return ("lineup", rotation)
    if not setters:
        return ("lineup", rotation)
    active_setter = next(
        (setter for setter in setters if setter in rotation),
        None
    )
    if active_setter is not None:
        return ("setter", rotation.index(active_setter))
    return ("lineup", rotation)


def _setter_position(
    rotation: tuple[int, ...],
    setters: list[int]
) -> int | None:
    if not setters:
        return None
    active_setter = next(
        (setter for setter in setters if setter in rotation),
        None
    )
    if active_setter is None:
        return -1
    return rotation.index(active_setter)


def _setter_position_label(position: int | None) -> str:
    return (
        f"Laeufer {ROMAN_NUMERALS[position]}"
        if position is not None and position >= 0
        else "Unknown"
    )


def _rotation_label(
    rotation_key: tuple[Any, ...],
    lineups: set[tuple[int, ...]],
    setters: list[int],
    is_u14: bool = False,
    player_names: dict[str, str] | None = None
) -> str:
    if rotation_key[0] == "all":
        return "All serving rotations"
    if is_u14:
        lineup = rotation_key[1]
        player_names = player_names or {}
        return " | ".join(
            f"{ROMAN_NUMERALS[position]}: #{jersey}"
            + (
                f" ({player_names[str(jersey)]})"
                if str(jersey) in player_names
                else ""
            )
            for position, jersey in enumerate(lineup)
        )
    if rotation_key[0] == "setter":
        return f"Laeufer {ROMAN_NUMERALS[rotation_key[1]]}"
    lineup = next(iter(lineups))
    numbers = "-".join(map(str, lineup))
    if setters:
        return f"Setter not on court ({numbers})"
    return f"Rotation {numbers}"


def _mean_run_length(runs: list[dict[str, Any]]) -> float | None:
    if not runs:
        return None
    return sum(run["length"] for run in runs) / len(runs)


def _distribution_results(
    data: dict[tuple[Any, ...], dict[str, Any]],
    setters: list[int],
    is_u14: bool = False,
    player_names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    results = []
    for rotation_key, stats in sorted(
        data.items(),
        key=lambda item: (
            0 if item[0][0] == "setter" else 1,
            item[0][1] if len(item[0]) > 1 else ()
        )
    ):
        results.append(
            {
                "lineups": [
                    list(lineup)
                    for lineup in sorted(stats["lineups"])
                ] if rotation_key[0] != "all" else [],
                "label": _rotation_label(
                    rotation_key,
                    stats["lineups"],
                    setters,
                    is_u14=is_u14,
                    player_names=player_names
                ),
                "run_length_distribution": {
                    str(length): stats["counts"][length]
                    for length in sorted(stats["counts"])
                },
                "mean_run_length": _mean_run_length(stats["runs"]),
                "runs": stats["runs"]
            }
        )
    return results


def _serving_player_results(
    stats_by_player: dict[int, Counter[str]],
    player_names: dict[str, str]
) -> list[dict[str, Any]]:
    results = []
    for jersey, stats in stats_by_player.items():
        points_served = stats["points_served"]
        opponent_points = stats["opponent_points"]
        results.append(
            {
                "jersey": jersey,
                "name": player_names.get(str(jersey)),
                "points_served": points_served,
                "opponent_points": opponent_points,
                "opponent_points_per_serve": (
                    opponent_points / points_served
                    if points_served
                    else 0.0
                )
            }
        )
    results.sort(
        key=lambda result: (
            result["opponent_points_per_serve"],
            -result["points_served"],
            result["jersey"]
        )
    )
    return results


def _point_totals_results(
    totals: Counter[str]
) -> list[dict[str, Any]]:
    return [
        {
            "setter_position": label,
            "own_points": totals[f"{label}:own"],
            "opponent_points": totals[f"{label}:opponent"]
        }
        for label in [
            *(f"Laeufer {numeral}" for numeral in ROMAN_NUMERALS),
            "Unknown"
        ]
    ]


def _lineup_performance_results(
    totals_by_lineup: dict[tuple[int, ...], Counter[str]],
    player_names: dict[str, str]
) -> list[dict[str, Any]]:
    results = []
    for lineup, totals in totals_by_lineup.items():
        own_points = totals["own_points"]
        opponent_points = totals["opponent_points"]
        results.append(
            {
                "players": [
                    {
                        "jersey": jersey,
                        "name": player_names.get(str(jersey))
                    }
                    for jersey in lineup
                ],
                "own_points": own_points,
                "opponent_points": opponent_points,
                "ratio": (
                    own_points / opponent_points
                    if opponent_points
                    else None
                )
            }
        )
    results.sort(
        key=lambda result: (
            0 if result["ratio"] is None else 1,
            -(result["ratio"] or 0),
            tuple(player["jersey"] for player in result["players"])
        )
    )
    return results


def _player_performance_results(
    totals_by_player: dict[int, Counter[str]],
    player_names: dict[str, str]
) -> list[dict[str, Any]]:
    results = []
    for jersey, totals in totals_by_player.items():
        won_points = totals["own_points"]
        lost_points = totals["opponent_points"]
        results.append(
            {
                "jersey": jersey,
                "name": player_names.get(str(jersey)),
                "points_played": totals["points_played"],
                "won_points": won_points,
                "lost_points": lost_points,
                "ratio": (
                    won_points / lost_points
                    if lost_points
                    else None
                )
            }
        )
    results.sort(
        key=lambda result: (
            0 if result["ratio"] is None else 1,
            -(result["ratio"] or 0),
            -result["points_played"],
            result["jersey"]
        )
    )
    return results


def _service_run_report_sections(
    performance_report: dict[str, Any],
    opponent_run_report: dict[str, Any],
    service_run_report: dict[str, Any],
    is_u14: bool
) -> list[dict[str, Any]]:
    opponent_runs_by_team = {
        team["team"]: team["rotations"]
        for team in opponent_run_report["teams"]
    }
    service_runs_by_team = {
        team["team"]: team
        for team in service_run_report["teams"]
    }
    sections = []
    for team in performance_report["teams"]:
        team_name = team["team"]
        service_data = service_runs_by_team[team_name]
        service_runs = service_data[
            "rotations" if is_u14 else "setter_positions"
        ]
        opponent_runs = opponent_runs_by_team[team_name]
        summaries = []
        if not is_u14:
            own_best, own_worst = _rotation_extremes(
                service_runs,
                best_is_high=True
            )
            summaries.append(
                {
                    "category": "Own service runs",
                    "best": own_best,
                    "worst": own_worst,
                    "interpretation": "more points per run is better"
                }
            )

        opponent_best, opponent_worst = _rotation_extremes(
            opponent_runs,
            best_is_high=False
        )
        summaries.append(
            {
                "category": (
                    "Opponent scoring runs by own rotation"
                    if is_u14
                    else "Opponent scoring runs"
                ),
                "best": opponent_best,
                "worst": opponent_worst,
                "interpretation": "fewer points conceded per run is better"
            }
        )

        team_slug = _filename_slug(team_name)
        images = [
            {
                "heading": "Own service-run length distribution",
                "alt": f"Own service-run lengths for {team_name}",
                "filename": f"{team_slug}_service_runs.png"
            },
            {
                "heading": (
                    "Opponent scoring-run length by own rotation"
                    if is_u14
                    else "Opponent scoring-run length by rotation"
                ),
                "alt": f"Opponent scoring-run lengths for {team_name}",
                "filename": f"{team_slug}_rotation_runs.png"
            }
        ]
        if not is_u14:
            images.append(
                {
                    "heading": "Points by setter position",
                    "alt": f"Points by setter position for {team_name}",
                    "filename": f"{team_slug}_points_by_setter_position.png"
                }
            )
        sections.append(
            {
                "team": team_name,
                "note": (
                    "Own service runs are shown as an overall distribution; "
                    "rotation comparisons are omitted."
                    if is_u14
                    else None
                ),
                "summaries": summaries,
                "images": images
            }
        )
    return sections


def _report_front_matter(
    team_names: list[str],
    match_date: str,
    match_result: dict[str, Any],
    setter_start_positions: list[dict[str, Any]],
    score_progressions: list[dict[str, Any]]
) -> dict[str, Any]:
    if len(team_names) != 2:
        raise ValueError("The match report requires exactly two team names")
    front_matter = {
        "team_names": team_names,
        "match_date": match_date,
        "title": f"{team_names[0]} vs {team_names[1]} - ({match_date})",
        "report_heading": "Match analysis",
        "match_result_heading": "Match Result",
        "contents_heading": "Contents",
        "match_result": {
            "sets_won": [
                match_result["sets_won"][team_name]
                for team_name in team_names
            ],
            "set_scores": "; ".join(
                f"Set {set_result['set_no']}: "
                f"{set_result['team_a_score']}–{set_result['team_b_score']}"
                for set_result in match_result["set_scores"]
            )
        },
        "disclaimer": (
            "Please take all analyses with a grain of salt: this simplified "
            "analysis is not a substitute for full scouting. For example, "
            "points scored while a player serves are not necessarily aces "
            "scored by that player."
        ),
        "contents": [
            ("Performance per Set of Players", "performance-per-set-of-players"),
            ("Stats per Player", "performance-per-player"),
            ("Stats per Serving Player", "stats-per-serving-player"),
            ("Length of Service Runs", "length-of-service-runs")
        ]
    }
    if score_progressions:
        front_matter["contents"].insert(
            0,
            ("Set Progression Point-by-Point", "set-score-progression")
        )
        front_matter["score_progressions"] = score_progressions
    if setter_start_positions:
        front_matter["contents"].insert(
            1,
            ("Setter Starting Positions", "setter-starting-positions")
        )
        front_matter["setter_start_positions"] = setter_start_positions
    return front_matter


def _write_markdown_report(
    output_path: Path,
    performance_report: dict[str, Any],
    service_run_sections: list[dict[str, Any]],
    front_matter: dict[str, Any]
) -> None:
    team_names = front_matter["team_names"]
    report_stem = _report_stem(team_names, front_matter["match_date"])
    lines = [
        f"# {team_names[0]} vs {team_names[1]}",
        "",
        f"## {front_matter['report_heading']} - "
        f"{front_matter['match_date']}",
        "",
        f"## {front_matter['match_result_heading']}",
        "",
        "<table>",
        "<tr>"
        f"<th>{escape(front_matter['team_names'][0])}</th>"
        f"<th>{escape(front_matter['team_names'][1])}</th>"
        "</tr>",
        "<tr>"
        f"<td>Sets: {front_matter['match_result']['sets_won'][0]}</td>"
        f"<td>Sets: {front_matter['match_result']['sets_won'][1]}</td>"
        "</tr>",
        f'<tr><td colspan="2">Set results: '
        f'{escape(front_matter["match_result"]["set_scores"])}</td></tr>',
        "</table>",
    ]
    score_progressions = front_matter.get("score_progressions", [])
    lines.extend(
        [
            "",
            f"> {front_matter['disclaimer']}",
            "",
            f"## {front_matter['contents_heading']}",
            "",
            *(
                f"- [{label}](#{anchor})"
                for label, anchor in front_matter["contents"]
            ),
            ""
        ]
    )
    if score_progressions:
        lines.extend(
            [
                "",
                "## Set Progression Point-by-Point",
                "",
                "Each row shows the score after a rally and both teams' "
                "rotations. The winning team's current score appears in its "
                "score column. S/R in the team header identifies which team "
                "serves first. Configured setters are marked (S); new players "
                "are highlighted in green.",
                ""
            ]
        )
        for progression in score_progressions:
            player_counts = [
                len(progression["rallies"][0]["rotations"][team_name])
                for team_name in team_names
            ]
            lines.extend(
                [
                    f"### Set {progression['set_no']}",
                    "",
                    "<table>",
                    "<tr>"
                    f'<th colspan="{player_counts[0]}">'
                    f"{escape(team_names[0])} "
                    f"({'S' if progression['opening_serving_team'] == team_names[0] else 'R'})"
                    "</th>"
                    '<th colspan="2">Score</th>'
                    f'<th colspan="{player_counts[1]}">'
                    f"{escape(team_names[1])} "
                    f"({'S' if progression['opening_serving_team'] == team_names[1] else 'R'})"
                    "</th>",
                    "</tr>",
                    "<tr>"
                ]
            )
            lines.extend(
                f"<th>{position}</th>"
                for position in ROMAN_NUMERALS[:player_counts[0]]
            )
            lines.extend(["<th></th>", "<th></th>"])
            lines.extend(
                f"<th>{position}</th>"
                for position in ROMAN_NUMERALS[:player_counts[1]]
            )
            lines.append("</tr>")
            for rally in progression["rallies"]:
                score = rally["score_after"]
                lines.append("<tr>")
                for team_name in team_names:
                    for player in rally["rotations"][team_name]:
                        label = _player_label_markup(
                            player["jersey"],
                            player["name"],
                            highlighted=player["is_substitution"],
                            markdown=True,
                            setter=player["is_setter"]
                        )
                        if player["is_setter"]:
                            label += " (S)"
                        lines.append(f"<td>{label}</td>")
                    if team_name == team_names[0]:
                        lines.append(
                            "<td>"
                            f"{score[team_name] if rally['scoring_team'] == team_name else ''}"
                            "</td>"
                        )
                        lines.append(
                            "<td>"
                            f"{score[team_names[1]] if rally['scoring_team'] == team_names[1] else ''}"
                            "</td>"
                        )
                lines.append("</tr>")
                for timeout in rally["timeouts"]:
                    timeout_team = timeout["team"]
                    opponent = next(
                        name for name in team_names if name != timeout_team
                    )
                    timeout_scores = {
                        timeout_team: timeout["team_score"],
                        opponent: timeout["opponent_score"]
                    }
                    timeout_score = ":".join(
                        str(timeout_scores[name]) for name in team_names
                    )
                    timeout_cells = []
                    if timeout_team == team_names[0]:
                        timeout_cells.append(
                            f'<td colspan="{player_counts[0]}">'
                            f"<strong>Timeout — {escape(timeout_team)}</strong>"
                            "</td>"
                        )
                        timeout_cells.append(
                            f'<td colspan="2">{timeout_score}</td>'
                        )
                        timeout_cells.append(
                            f'<td colspan="{player_counts[1]}"></td>'
                        )
                    else:
                        timeout_cells.append(
                            f'<td colspan="{player_counts[0]}"></td>'
                        )
                        timeout_cells.append(
                            f'<td colspan="2">{timeout_score}</td>'
                        )
                        timeout_cells.append(
                            f'<td colspan="{player_counts[1]}">'
                            f"<strong>Timeout — {escape(timeout_team)}</strong>"
                            "</td>"
                        )
                    lines.append(
                        f"<tr>{''.join(timeout_cells)}</tr>"
                    )
            lines.extend(["</table>", ""])

    lines.extend(
        [
            "## Performance per Set of Players",
            "",
            "Lineups are grouped by the jersey numbers on court. The ratio is "
            "team points scored divided by opponent points scored; a lineup "
            "that conceded no points is shown as `∞`.",
            ""
        ]
    )
    for team in performance_report["teams"]:
        lines.extend(
            [
                f"### {team['team']}",
                "",
                "| Players on court | Team points | Opponent points | Ratio "
                "(team:opponent) |",
                "|---|---:|---:|---:|"
            ]
        )
        for lineup in team["lineups"]:
            players = ", ".join(
                _player_label_markup(
                    player["jersey"],
                    player["name"]
                )
                for player in lineup["players"]
            )
            ratio = (
                "∞"
                if lineup["ratio"] is None
                else f"{lineup['ratio']:.2f}"
            )
            lines.append(
                f"| {players} | {lineup['own_points']} | "
                f"{lineup['opponent_points']} | {ratio} |"
            )
        lines.append("")

    setter_start_positions = front_matter.get("setter_start_positions", [])
    if setter_start_positions:
        lines.extend(["## Setter Starting Positions", ""])
        for team_name in team_names:
            team_positions = [
                entry for entry in setter_start_positions
                if entry["team"] == team_name
            ]
            if not team_positions:
                continue
            lines.extend(
                [
                    f"### {team_name}",
                    "",
                    "| Set | Setter(s) starting | At set start |",
                    "|---:|---|---|"
                ]
            )
            lines.extend(
                f"| {entry['set_no']} | {entry['setters']} | "
                f"{entry['start_type']} |"
                for entry in team_positions
            )
            lines.append("")

    lines.extend(["## Stats per Player", ""])
    lines.append(
        "'Points played' counts every rally while the player was on court. "
        "'Won/lost ratio' is team 'points won' divided by 'points lost' while "
        "they were on court; a player with no lost points is shown as `∞`."
    )
    lines.append("")
    for team in performance_report["teams"]:
        lines.extend(
            [
                f"### {team['team']}",
                "",
                "| Player | Points played | Points won | Points lost | "
                "Won/lost ratio |",
                "|---|---:|---:|---:|---:|"
            ]
        )
        for player in team["players"]:
            name = _player_label_markup(
                player["jersey"],
                player["name"]
            )
            ratio = (
                "∞"
                if player["ratio"] is None
                else f"{player['ratio']:.2f}"
            )
            lines.append(
                f"| {name} | {player['points_played']} | "
                f"{player['won_points']} | {player['lost_points']} | "
                f"{ratio} |"
            )
        lines.append("")

    lines.extend(["## Stats per Serving Player", ""])
    lines.append(
        "'Rallies served' counts every rally served by the player. 'Opponent "
        "points on serve' counts only rallies won by the opponent; points "
        "scored by the serving team are not included. The rate is opponent "
        "points divided by rallies served, sorted from lowest to highest."
    )
    lines.append("")
    for team in performance_report["teams"]:
        lines.extend(
            [
                f"### {team['team']}",
                "",
                "| Serving player | Rallies served | Opponent points on serve | "
                "Opponent points per serve |",
                "|---|---:|---:|---:|"
            ]
        )
        for player in team["serving_players"]:
            name = _player_label_markup(
                player["jersey"],
                player["name"]
            )
            lines.append(
                f"| {name} | {player['points_served']} | "
                f"{player['opponent_points']} | "
                f"{player['opponent_points_per_serve']:.2f} |"
            )
        lines.append("")

    lines.extend(["## Length of Service Runs", ""])
    for section in service_run_sections:
        lines.extend([f"### {section['team']}", ""])
        if section["note"]:
            lines.extend([section["note"], ""])
        for summary in section["summaries"]:
            lines.extend(
                _extreme_summary_lines(
                    summary["category"],
                    summary["best"],
                    summary["worst"],
                    summary["interpretation"]
                )
            )
        for image in section["images"]:
            lines.extend(
                [
                    f"#### {image['heading']}",
                    "",
                    f"![{image['alt']}]({image['filename']})",
                    ""
                ]
            )
    report_path = output_path / f"{report_stem}.md"
    report_path.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )
    _write_pdf_report(
        output_path / f"{report_stem}.pdf",
        report_path,
        performance_report,
        service_run_sections,
        front_matter
    )
    for image_path in output_path.glob("*.png"):
        image_path.unlink()
    report_path.unlink()


def _report_stem(team_names: list[str], match_date: str) -> str:
    if len(team_names) != 2:
        raise ValueError("The match report requires exactly two team names")
    try:
        filename_date = datetime.strptime(match_date, "%d.%m.%Y").strftime(
            "%Y-%m-%d"
        )
    except ValueError as error:
        raise ValueError(
            f"Invalid match date for report filename: {match_date!r}"
        ) from error
    return (
        f"report_{_filename_slug(team_names[0])}_vs_"
        f"{_filename_slug(team_names[1])}_{filename_date}"
    )


def _write_pdf_report(
    output_file: Path,
    markdown_file: Path,
    performance_report: dict[str, Any],
    service_run_sections: list[dict[str, Any]],
    front_matter: dict[str, Any]
) -> None:
    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]
    match_result_table = Table(
        [
            [
                Paragraph(escape(front_matter["team_names"][0]), body_style),
                Paragraph(escape(front_matter["team_names"][1]), body_style)
            ],
            [
                Paragraph(
                    f"Sets: {front_matter['match_result']['sets_won'][0]}",
                    body_style
                ),
                Paragraph(
                    f"Sets: {front_matter['match_result']['sets_won'][1]}",
                    body_style
                )
            ],
            [
                Paragraph(
                    "Set results: "
                    f"{escape(front_matter['match_result']['set_scores'])}",
                    body_style
                ),
                ""
            ]
        ],
        colWidths=[60 * mm, 60 * mm]
    )
    match_result_table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 2), (1, 2)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6)
            ]
        )
    )
    score_progressions = front_matter.get("score_progressions", [])
    score_progression_story: list[Any] = []
    if score_progressions:
        progression_style = ParagraphStyle(
            "ScoreProgression",
            parent=body_style,
            fontSize=6.2,
            leading=6.6,
            spaceBefore=0,
            spaceAfter=0
        )
        progression_header_style = ParagraphStyle(
            "ScoreProgressionHeader",
            parent=progression_style,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#15345b")
        )
        progression_intro_style = ParagraphStyle(
            "ScoreProgressionIntro",
            parent=body_style,
            fontSize=8,
            leading=9,
            spaceBefore=0,
            spaceAfter=0
        )
        progression_timeout_style = ParagraphStyle(
            "ScoreProgressionTimeout",
            parent=progression_style,
            textColor=colors.HexColor("#9c0006")
        )
        score_progression_story.extend(
            [
                PageBreak(),
                Paragraph(
                    '<a name="set-score-progression"/>'
                    "Set Progression Point-by-Point",
                    styles["Heading1"]
                ),
                Paragraph(
                    "Each rally shows both rotations and the winner's current "
                    "score on that team's side. S/R in the team header "
                    "identifies which team serves first; configured setters "
                    "are marked (S). Green marks substitutions.",
                    progression_intro_style
                )
            ]
        )
        available_width = landscape(A4)[0] - 30 * mm
        score_column_width = 10 * mm
        team_width = (available_width - 2 * score_column_width) / 2
        for progression_index, progression in enumerate(score_progressions):
            if progression_index:
                score_progression_story.append(PageBreak())
            score_progression_story.append(
                Paragraph(
                    f"Set {progression['set_no']}",
                    styles["Heading2"]
                )
            )
            player_counts = [
                len(progression["rallies"][0]["rotations"][team_name])
                for team_name in front_matter["team_names"]
            ]
            column_widths = [
                *([team_width / player_counts[0]] * player_counts[0]),
                score_column_width,
                score_column_width,
                *([team_width / player_counts[1]] * player_counts[1])
            ]
            team_a_score_column = player_counts[0]
            team_b_score_column = team_a_score_column + 1
            team_b_start_column = team_b_score_column + 1
            table_data: list[list[Any]] = [
                [
                    Paragraph(
                        f"{escape(front_matter['team_names'][0])} "
                        f"({'S' if progression['opening_serving_team'] == front_matter['team_names'][0] else 'R'})",
                        progression_header_style
                    ),
                    *("" for _ in range(player_counts[0] - 1)),
                    Paragraph("Score", progression_header_style),
                    "",
                    Paragraph(
                        f"{escape(front_matter['team_names'][1])} "
                        f"({'S' if progression['opening_serving_team'] == front_matter['team_names'][1] else 'R'})",
                        progression_header_style
                    ),
                    *("" for _ in range(player_counts[1] - 1))
                ],
                [
                    *(
                        Paragraph(position, progression_header_style)
                        for position in ROMAN_NUMERALS[:player_counts[0]]
                    ),
                    "",
                    "",
                    *(
                        Paragraph(position, progression_header_style)
                        for position in ROMAN_NUMERALS[:player_counts[1]]
                    )
                ]
            ]
            span_rows: list[tuple[int, int, int]] = [
                (0, 0, player_counts[0] - 1),
                (0, team_a_score_column, team_b_score_column),
                (0, team_b_start_column, len(column_widths) - 1)
            ]
            span_rows.append(
                (1, team_a_score_column, team_b_score_column)
            )
            timeout_rows: list[tuple[int, str]] = []
            for rally in progression["rallies"]:
                score = rally["score_after"]
                rally_cells = []
                for team_name in front_matter["team_names"]:
                    for player in rally["rotations"][team_name]:
                        label = _player_label_markup(
                            player["jersey"],
                            player["name"],
                            highlighted=player["is_substitution"],
                            setter=player["is_setter"]
                        )
                        if player["is_setter"]:
                            label += " (S)"
                        rally_cells.append(
                            Paragraph(label, progression_style)
                        )
                    if team_name == front_matter["team_names"][0]:
                        rally_cells.append(
                            Paragraph(
                                str(score[team_name])
                                if rally["scoring_team"] == team_name
                                else "",
                                progression_style
                            )
                        )
                        rally_cells.append(
                            Paragraph(
                                str(score[front_matter["team_names"][1]])
                                if rally["scoring_team"] == front_matter["team_names"][1]
                                else "",
                                progression_style
                            )
                        )
                table_data.append(rally_cells)
                for timeout in rally["timeouts"]:
                    timeout_row = len(table_data)
                    timeout_team = timeout["team"]
                    timeout_rows.append((timeout_row, timeout_team))
                    opponent = next(
                        name
                        for name in front_matter["team_names"]
                        if name != timeout_team
                    )
                    timeout_scores = {
                        timeout_team: timeout["team_score"],
                        opponent: timeout["opponent_score"]
                    }
                    timeout_score = ":".join(
                        str(timeout_scores[name])
                        for name in front_matter["team_names"]
                    )
                    timeout_cells = [""] * len(column_widths)
                    if timeout_team == front_matter["team_names"][0]:
                        timeout_cells[0] = Paragraph(
                            f"<b>Timeout — {escape(timeout_team)}</b>",
                            progression_timeout_style
                        )
                        span_rows.append(
                            (timeout_row, 0, player_counts[0] - 1)
                        )
                        span_rows.append(
                            (
                                timeout_row,
                                team_b_start_column,
                                len(column_widths) - 1
                            )
                        )
                    else:
                        timeout_cells[team_b_start_column] = Paragraph(
                            f"<b>Timeout — {escape(timeout_team)}</b>",
                            progression_timeout_style
                        )
                        span_rows.append((timeout_row, 0, player_counts[0] - 1))
                        span_rows.append(
                            (
                                timeout_row,
                                team_b_start_column,
                                len(column_widths) - 1
                            )
                        )
                    timeout_cells[team_a_score_column] = Paragraph(
                        timeout_score,
                        progression_timeout_style
                    )
                    timeout_cells[team_b_score_column] = ""
                    span_rows.append(
                        (
                            timeout_row,
                            team_a_score_column,
                            team_b_score_column
                        )
                    )
                    table_data.append(
                        timeout_cells
                    )

            score_table = Table(
                table_data,
                colWidths=column_widths,
                hAlign="LEFT"
            )
            score_table_style = [
                ("FONTSIZE", (0, 0), (-1, -1), 6.2),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b7c3d0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 0.45 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0.45 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0.15 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0.15 * mm),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f2f5f8")),
                ("ALIGN", (0, 0), (-1, 1), "CENTER"),
                ("ALIGN", (team_a_score_column, 2), (team_a_score_column, -1), "RIGHT"),
                ("ALIGN", (team_b_score_column, 2), (team_b_score_column, -1), "LEFT")
            ]
            for row_index, start_column, end_column in span_rows:
                score_table_style.append(
                    (
                        "SPAN",
                        (start_column, row_index),
                        (end_column, row_index)
                    )
                )
            for row_index, timeout_team in timeout_rows:
                timeout_team_start = (
                    0
                    if timeout_team == front_matter["team_names"][0]
                    else team_b_start_column
                )
                timeout_team_end = (
                    player_counts[0] - 1
                    if timeout_team == front_matter["team_names"][0]
                    else len(column_widths) - 1
                )
                score_table_style.extend(
                    [
                        (
                            "BACKGROUND",
                            (timeout_team_start, row_index),
                            (timeout_team_end, row_index),
                            colors.HexColor("#f4cccc")
                        ),
                        (
                            "BACKGROUND",
                            (team_a_score_column, row_index),
                            (team_b_score_column, row_index),
                            colors.HexColor("#f4cccc")
                        ),
                        (
                            "ALIGN",
                            (team_a_score_column, row_index),
                            (team_b_score_column, row_index),
                            "CENTER"
                        )
                    ]
                )
            score_table.setStyle(TableStyle(score_table_style))
            score_progression_story.append(score_table)
        score_progression_story.extend(
            [PageBreak()]
        )
    story = [
        Paragraph(
            front_matter["report_heading"],
            styles["Title"]
        ),
        Paragraph(
            escape(front_matter["title"]),
            styles["Title"]
        ),
        Paragraph(
            front_matter["match_result_heading"],
            styles["Heading1"]
        ),
        match_result_table,
        Paragraph(
            escape(front_matter["disclaimer"]),
            body_style
        ),
        Paragraph(front_matter["contents_heading"], styles["Heading1"]),
        *[
            Paragraph(
                f'<link href="#{anchor}" color="blue">{escape(label)}</link>',
                body_style
            )
            for label, anchor in front_matter["contents"]
        ],
        *score_progression_story,
        Spacer(1, 4 * mm),
        Paragraph(
            '<a name="performance-per-set-of-players"/>'
            "Performance per Set of Players",
            styles["Heading1"]
        ),
        Paragraph(
            "Lineups are grouped by the players on court. The ratio is team "
            "points scored divided by opponent points scored; a lineup that "
            "conceded no points is shown as infinity.",
            body_style
        ),
        Spacer(1, 4 * mm)
    ]
    for team in performance_report["teams"]:
        story.extend(
            [
                Paragraph(escape(team["team"]), styles["Heading2"]),
                Spacer(1, 2 * mm)
            ]
        )
        table_data = [[
            "Players on court",
            "Team points",
            "Opponent points",
            "Ratio"
        ]]
        for lineup in team["lineups"]:
            players = ", ".join(
                _player_label_markup(
                    player["jersey"],
                    player["name"]
                )
                for player in lineup["players"]
            )
            ratio = (
                "Infinity"
                if lineup["ratio"] is None
                else f"{lineup['ratio']:.2f}"
            )
            table_data.append(
                [
                    Paragraph(players, body_style),
                    str(lineup["own_points"]),
                    str(lineup["opponent_points"]),
                    ratio
                ]
            )
        lineup_table = Table(
            table_data,
            colWidths=[183 * mm, 28 * mm, 32 * mm, 24 * mm],
            repeatRows=1
        )
        lineup_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5)
                ]
            )
        )
        story.extend([lineup_table, Spacer(1, 5 * mm)])

    setter_start_positions = front_matter.get("setter_start_positions", [])
    if setter_start_positions:
        story.extend(
            [
                Paragraph(
                    '<a name="setter-starting-positions"/>'
                    "Setter Starting Positions",
                    styles["Heading1"]
                ),
                Spacer(1, 2 * mm)
            ]
        )
        for team_name in front_matter["team_names"]:
            team_positions = [
                entry for entry in setter_start_positions
                if entry["team"] == team_name
            ]
            if not team_positions:
                continue
            story.extend(
                [
                    Paragraph(escape(team_name), styles["Heading2"]),
                    Spacer(1, 2 * mm)
                ]
            )
            setter_table_data: list[list[Any]] = [[
                "Set",
                "Setter(s) starting",
                "At set start"
            ]]
            setter_table_data.extend(
                [
                    str(entry["set_no"]),
                    Paragraph(entry["setters"], body_style),
                    entry["start_type"]
                ]
                for entry in team_positions
            )
            setter_table = Table(
                setter_table_data,
                colWidths=[15 * mm, 100 * mm, 45 * mm],
                repeatRows=1
            )
            setter_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5)
                    ]
                )
            )
            story.extend([setter_table, Spacer(1, 3 * mm)])

    story.extend(
        [
            Paragraph(
                '<a name="performance-per-player"/>Stats per Player',
                styles["Heading1"]
            ),
            Paragraph(
                "'Points played' counts every rally while the player was on "
                "court. 'Won/lost ratio' is team 'points won' divided by 'points lost' while "
                "they were on court; a player with no lost points "
                "is shown as infinity.",
                body_style
            ),
            Spacer(1, 4 * mm)
        ]
    )
    for team in performance_report["teams"]:
        story.extend(
            [
                Paragraph(escape(team["team"]), styles["Heading2"]),
                Spacer(1, 2 * mm)
            ]
        )
        table_data = [[
            "Player",
            "Points played",
            "Points won",
            "Points lost",
            "Won/lost ratio"
        ]]
        for player in team["players"]:
            name = _player_label_markup(
                player["jersey"],
                player["name"]
            )
            ratio = (
                "Infinity"
                if player["ratio"] is None
                else f"{player['ratio']:.2f}"
            )
            table_data.append(
                [
                    Paragraph(name, body_style),
                    str(player["points_played"]),
                    str(player["won_points"]),
                    str(player["lost_points"]),
                    ratio
                ]
            )
        player_table = Table(
            table_data,
            colWidths=[145 * mm, 30 * mm, 25 * mm, 25 * mm, 35 * mm],
            repeatRows=1
        )
        player_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5)
                ]
            )
        )
        story.extend([player_table, Spacer(1, 5 * mm)])

    story.extend(
        [
            Paragraph(
                '<a name="stats-per-serving-player"/>'
                "Stats per Serving Player",
                styles["Heading1"]
            ),
            Paragraph(
                "'Rallies served' counts every rally served by the player. "
                "'Opponent points on serve' counts only rallies won by the "
                "opponent; points scored by the serving team are not "
                "included. The rate is 'opponent points' divided by 'rallies "
                "served', sorted from lowest to highest.",
                body_style
            ),
            Spacer(1, 4 * mm)
        ]
    )
    for team in performance_report["teams"]:
        story.extend(
            [
                Paragraph(escape(team["team"]), styles["Heading2"]),
                Spacer(1, 2 * mm)
            ]
        )
        table_data = [[
            "Serving player",
            "Rallies served",
            "Opponent points on serve",
            "Opponent points per serve"
        ]]
        for player in team["serving_players"]:
            name = _player_label_markup(
                player["jersey"],
                player["name"]
            )
            table_data.append(
                [
                    Paragraph(name, body_style),
                    str(player["points_served"]),
                    str(player["opponent_points"]),
                    f"{player['opponent_points_per_serve']:.2f}"
                ]
            )
        serving_table = Table(
            table_data,
            colWidths=[100 * mm, 35 * mm, 45 * mm, 55 * mm],
            repeatRows=1
        )
        serving_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5)
                ]
            )
        )
        story.extend([serving_table, Spacer(1, 5 * mm)])

    story.append(
        Paragraph(
            '<a name="length-of-service-runs"/>Length of Service Runs',
            styles["Heading1"]
        )
    )
    for section in service_run_sections:
        story.append(
            Paragraph(escape(section["team"]), styles["Heading2"])
        )
        if section["note"]:
            story.append(Paragraph(escape(section["note"]), body_style))
        for summary in section["summaries"]:
            story.extend(
                _extreme_summary_paragraphs(
                    summary["category"],
                    summary["best"],
                    summary["worst"],
                    summary["interpretation"],
                    body_style
                )
            )
        for image_data in section["images"]:
            image_path = markdown_file.parent / image_data["filename"]
            if not image_path.is_file():
                raise FileNotFoundError(
                    f"Cannot create report PDF; plot is missing: {image_path}"
                )
            image = Image(str(image_path))
            image_width = 170 * mm
            scale = min(image_width / image.imageWidth, 110 * mm / image.imageHeight)
            image.drawWidth *= scale
            image.drawHeight *= scale
            story.extend(
                [
                    Paragraph(
                        escape(image_data["heading"]),
                        styles["Heading3"]
                    ),
                    image,
                    Spacer(1, 4 * mm)
                ]
            )

    landscape_width, landscape_height = landscape(A4)
    document = BaseDocTemplate(
        str(output_file),
        pagesize=landscape(A4),
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Match analysis"
    )
    document.addPageTemplates(
        [
            PageTemplate(
                id="landscape",
                frames=[
                    Frame(
                        15 * mm,
                        15 * mm,
                        landscape_width - 30 * mm,
                        landscape_height - 30 * mm,
                        id="landscape-frame"
                    )
                ],
                pagesize=landscape(A4)
            )
        ]
    )
    document.build(story)


def _rotation_extremes(
    rotations: list[dict[str, Any]],
    best_is_high: bool
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    observed = [
        (rotation["label"], rotation["mean_run_length"])
        for rotation in rotations
        if rotation["mean_run_length"] is not None
    ]
    if not observed:
        return [], []
    best_value = (
        max if best_is_high else min
    )(value for _, value in observed)
    worst_value = (
        min if best_is_high else max
    )(value for _, value in observed)
    return (
        [
            (label, value)
            for label, value in observed
            if value == best_value
        ],
        [
            (label, value)
            for label, value in observed
            if value == worst_value
        ]
    )


def _extreme_summary_lines(
    category: str,
    best: list[tuple[str, float]],
    worst: list[tuple[str, float]],
    interpretation: str
) -> list[str]:
    if not best:
        return [f"- No {category.lower()} were recorded."]

    lines = [f"- {category} ({interpretation})", "  - Best rotations:"]
    lines.extend(
        f"    - {label} ({mean:.2f} points/run)"
        for label, mean in best
    )
    lines.append("  - Worst rotations:")
    lines.extend(
        f"    - {label} ({mean:.2f} points/run)"
        for label, mean in worst
    )
    lines.append("")
    return lines


def _extreme_summary_paragraphs(
    category: str,
    best: list[tuple[str, float]],
    worst: list[tuple[str, float]],
    interpretation: str,
    style: Any
) -> list[Paragraph]:
    lines = _extreme_summary_lines(
        category,
        best,
        worst,
        interpretation
    )
    paragraphs = []
    for line in lines:
        if not line:
            paragraphs.append(Spacer(1, 2 * mm))
            continue
        level = (len(line) - len(line.lstrip())) // 2
        text = line.lstrip()[2:]
        paragraphs.append(
            Paragraph(
                escape(text),
                bulletText="-",
                style=ParagraphStyle(
                    name=f"ExtremeBullet{level}",
                    parent=style,
                    leftIndent=(level + 1) * 5 * mm,
                    firstLineIndent=-3 * mm
                )
            )
        )
    return paragraphs


def _plot_team_distribution(
    team_name: str,
    rotations: list[dict[str, Any]],
    output_file: Path,
    u14: bool = False
) -> None:
    _plot_run_distribution_bars(
        team_name,
        rotations,
        output_file,
        title=(
            f"'Opponent scoring runs' by 'rotation' of {team_name}"
            if u14
            else f"'Opponent scoring runs' while {team_name} held 'rotation'"
        ),
        x_label="Consecutive points scored by opponent",
        legend_title="Position and player" if u14 else "Rotation",
        empty_length=1
    )


def _plot_service_run_distribution(
    team_name: str,
    rotations: list[dict[str, Any]],
    output_file: Path,
    u14: bool = False
) -> None:
    _plot_run_distribution_bars(
        team_name,
        rotations,
        output_file,
        title=f"'{team_name} points scored during own service runs'",
        x_label="Points won before losing serve",
        legend_title="Service runs" if u14 else "Setter position",
        empty_length=0
    )


def _plot_run_distribution_bars(
    team_name: str,
    rotations: list[dict[str, Any]],
    output_file: Path,
    title: str,
    x_label: str,
    legend_title: str,
    empty_length: int
) -> None:
    observed_lengths = [
        int(length)
        for rotation in rotations
        for length in rotation["run_length_distribution"]
    ]
    lengths = (
        list(range(min(observed_lengths), max(observed_lengths) + 1))
        if observed_lengths
        else [empty_length]
    )

    figure, axis = plt.subplots(figsize=(10, 6))
    bar_width = 0.8 / max(len(rotations), 1)
    for index, rotation in enumerate(rotations):
        distribution = rotation["run_length_distribution"]
        frequencies = [distribution.get(str(length), 0) for length in lengths]
        color = colorsys.hsv_to_rgb(
            index / max(len(rotations), 1),
            0.7,
            0.8
        )
        offset = (index - (len(rotations) - 1) / 2) * bar_width
        axis.bar(
            [length + offset for length in lengths],
            frequencies,
            width=bar_width,
            color=color,
            edgecolor="white",
            linewidth=0.6,
            label=(
                f"{rotation['label']} "
                f"(mean: {rotation['mean_run_length']:.2f})"
                if rotation["mean_run_length"] is not None
                else f"{rotation['label']} (mean: n/a)"
            )
        )

    for left_length, right_length in zip(lengths, lengths[1:]):
        axis.axvline(
            (left_length + right_length) / 2,
            color="0.55",
            linestyle="--",
            linewidth=0.8,
            alpha=0.7
        )

    axis.set_title(title)
    axis.set_xlabel(x_label)
    axis.set_ylabel("Number of runs")
    axis.set_xticks(lengths)
    axis.grid(axis="y", alpha=0.3)
    if rotations:
        axis.legend(
            title=legend_title,
            bbox_to_anchor=(1.02, 1),
            loc="upper left"
        )
    figure.tight_layout()
    figure.savefig(str(output_file), dpi=160, bbox_inches="tight")
    plt.close(figure)


def _plot_points_by_setter_position(
    team_name: str,
    positions: list[dict[str, Any]],
    output_file: Path
) -> None:
    displayed_positions = [
        position
        for position in positions
        if position["own_points"] or position["opponent_points"]
    ]
    if not displayed_positions:
        displayed_positions = positions
    labels = [position["setter_position"] for position in displayed_positions]
    own_points = [position["own_points"] for position in displayed_positions]
    opponent_points = [
        position["opponent_points"] for position in displayed_positions
    ]
    indexes = list(range(len(labels)))
    bar_width = 0.38

    figure, axis = plt.subplots(figsize=(10, 6))
    axis.bar(
        [index - bar_width / 2 for index in indexes],
        own_points,
        bar_width,
        label=f"{team_name} points"
    )
    axis.bar(
        [index + bar_width / 2 for index in indexes],
        opponent_points,
        bar_width,
        label="Opponent points"
    )
    axis.set_title(f"Points scored by setter position: {team_name}")
    axis.set_xlabel("Setter position")
    axis.set_ylabel("Total points")
    axis.set_xticks(indexes)
    axis.set_xticklabels(labels)
    axis.grid(axis="y", alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(str(output_file), dpi=160, bbox_inches="tight")
    plt.close(figure)


def _filename_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if not slug:
        raise ValueError(f"Cannot create output filename for team {value!r}")
    return slug
