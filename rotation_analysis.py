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
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
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
    _write_markdown_report(
        output_path,
        performance_report,
        report,
        service_report,
        point_data["match_date"],
        is_u14=is_u14
    )
    return report


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


def _write_markdown_report(
    output_path: Path,
    performance_report: dict[str, Any],
    opponent_run_report: dict[str, Any],
    service_run_report: dict[str, Any],
    match_date: str,
    is_u14: bool = False
) -> None:
    team_names = [
        team["team"]
        for team in performance_report["teams"]
    ]
    report_stem = _report_stem(team_names, match_date)
    lines = [
        f"# Match analysis - {match_date}",
        "",
        "## Contents",
        "",
        "- [Performance per Set of Players](#performance-per-set-of-players)",
        "- [Performance per Player](#performance-per-player)",
        "- [Length of Service Runs](#length-of-service-runs)",
        "",
        "## Performance per Set of Players",
        "",
        "Lineups are grouped by the jersey numbers on court. The ratio is "
        "team points scored divided by opponent points scored; a lineup that "
        "conceded no points is shown as `∞`.",
        ""
    ]
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
                f"{player['jersey']} ({player['name']})"
                if player["name"]
                else str(player["jersey"])
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

    lines.extend(["## Performance per Player", ""])
    lines.append(
        "Points played counts every rally while the player was on court. "
        "Won/lost ratio is team points won divided by points lost while "
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
            name = (
                f"{player['jersey']} ({player['name']})"
                if player["name"]
                else str(player["jersey"])
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

    lines.extend(["## Length of Service Runs", ""])
    for team_name in (
        team["team"] for team in performance_report["teams"]
    ):
        team_slug = _filename_slug(team_name)
        service_runs = next(
            team["rotations" if is_u14 else "setter_positions"]
            for team in service_run_report["teams"]
            if team["team"] == team_name
        )
        opponent_rotations = next(
            team["rotations"]
            for team in opponent_run_report["teams"]
            if team["team"] == team_name
        )
        opponent_best, opponent_worst = _rotation_extremes(
            opponent_rotations,
            best_is_high=False
        )
        lines.extend([f"### {team_name}", ""])
        if is_u14:
            lines.extend(
                [
                    "Own service runs are shown as an overall distribution; "
                    "rotation comparisons are omitted.",
                    _extreme_summary(
                        "opponent scoring runs by own rotation",
                        opponent_best,
                        opponent_worst,
                        "fewer points conceded per run is better"
                    ),
                    "",
                    "#### Own service-run length distribution",
                    "",
                    f"![Own service-run lengths for {team_name}]"
                    f"({team_slug}_service_runs.png)",
                    "",
                    "#### Opponent scoring-run length by own rotation",
                    "",
                    f"![Opponent scoring-run lengths for {team_name}]"
                    f"({team_slug}_rotation_runs.png)",
                    ""
                ]
            )
        else:
            own_best, own_worst = _rotation_extremes(
                service_runs,
                best_is_high=True
            )
            lines.extend(
                [
                    _extreme_summary(
                        "own service runs",
                        own_best,
                        own_worst,
                        "more points per run is better"
                    ),
                    _extreme_summary(
                        "opponent scoring runs",
                        opponent_best,
                        opponent_worst,
                        "fewer points conceded per run is better"
                    ),
                    "",
                    "#### Own service-run length distribution",
                    "",
                    f"![Own service-run lengths for {team_name}]"
                    f"({team_slug}_service_runs.png)",
                    "",
                    "#### Opponent scoring-run length by rotation",
                    "",
                    f"![Opponent scoring-run lengths for {team_name}]"
                    f"({team_slug}_rotation_runs.png)",
                    "",
                    "#### Points by setter position",
                    "",
                    f"![Points by setter position for {team_name}]"
                    f"({team_slug}_points_by_setter_position.png)",
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
        opponent_run_report,
        service_run_report,
        match_date,
        is_u14=is_u14
    )


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
    opponent_run_report: dict[str, Any],
    service_run_report: dict[str, Any],
    match_date: str,
    is_u14: bool = False
) -> None:
    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]
    team_names = [
        team["team"]
        for team in performance_report["teams"]
    ]
    if len(team_names) != 2:
        raise ValueError("The match report requires exactly two team names")
    story = [
        Paragraph(
            f"{escape(team_names[0])} vs {escape(team_names[1])}",
            styles["Title"]
        ),
        Paragraph(
            f"Match analysis - {escape(match_date)}",
            styles["Heading2"]
        ),
        Paragraph("Contents", styles["Heading1"]),
        Paragraph(
            '<link href="#performance-per-set-of-players" color="blue">'
            "Performance per Set of Players</link>",
            body_style
        ),
        Paragraph(
            '<link href="#performance-per-player" color="blue">'
            "Performance per Player</link>",
            body_style
        ),
        Paragraph(
            '<link href="#length-of-service-runs" color="blue">'
            "Length of Service Runs</link>",
            body_style
        ),
        Spacer(1, 4 * mm),
        Paragraph(
            '<a name="performance-per-set-of-players"/>'
            "Performance per Set of Players",
            styles["Heading1"]
        ),
        Paragraph(
            (
                "Lineups are grouped by the jersey numbers on court. The ratio "
                "is team points scored divided by opponent points scored; a "
                "lineup that conceded no points is shown as infinity."
                if is_u14
                else "Lineups are grouped by the six jersey numbers on court. "
                "The ratio is team points scored divided by opponent points "
                "scored; a lineup that conceded no points is shown as infinity."
            ),
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
                f"{player['jersey']} ({player['name']})"
                if player["name"]
                else str(player["jersey"])
                for player in lineup["players"]
            )
            ratio = (
                "Infinity"
                if lineup["ratio"] is None
                else f"{lineup['ratio']:.2f}"
            )
            table_data.append(
                [
                    Paragraph(escape(players), body_style),
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

    story.extend(
        [
            Paragraph(
                '<a name="performance-per-player"/>Performance per Player',
                styles["Heading1"]
            ),
            Paragraph(
                "Points played counts every rally while the player was on "
                "court. Won/lost ratio is team points won divided by points "
                "lost while they were on court; a player with no lost points "
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
            name = (
                f"{player['jersey']} ({player['name']})"
                if player["name"]
                else str(player["jersey"])
            )
            ratio = (
                "Infinity"
                if player["ratio"] is None
                else f"{player['ratio']:.2f}"
            )
            table_data.append(
                [
                    Paragraph(escape(name), body_style),
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

    story.append(
        Paragraph(
            '<a name="length-of-service-runs"/>Length of Service Runs',
            styles["Heading1"]
        )
    )
    for team_name in (
        team["team"] for team in performance_report["teams"]
    ):
        service_runs = next(
            team["rotations" if is_u14 else "setter_positions"]
            for team in service_run_report["teams"]
            if team["team"] == team_name
        )
        opponent_rotations = next(
            team["rotations"]
            for team in opponent_run_report["teams"]
            if team["team"] == team_name
        )
        opponent_best, opponent_worst = _rotation_extremes(
            opponent_rotations,
            best_is_high=False
        )
        story.append(Paragraph(escape(team_name), styles["Heading2"]))
        if is_u14:
            story.extend(
                [
                    Paragraph(
                        "Own service runs are shown as an overall "
                        "distribution; rotation comparisons are omitted.",
                        body_style
                    ),
                    Paragraph(
                        escape(
                            _extreme_summary(
                                "opponent scoring runs by own rotation",
                                opponent_best,
                                opponent_worst,
                                "fewer points conceded per run is better"
                            )
                        ),
                        body_style
                    )
                ]
            )
        else:
            own_best, own_worst = _rotation_extremes(
                service_runs,
                best_is_high=True
            )
            story.extend(
                [
                    Paragraph(
                        escape(
                            _extreme_summary(
                                "own service runs",
                                own_best,
                                own_worst,
                                "more points per run is better"
                            )
                        ),
                        body_style
                    ),
                    Paragraph(
                        escape(
                            _extreme_summary(
                                "opponent scoring runs",
                                opponent_best,
                                opponent_worst,
                                "fewer points conceded per run is better"
                            )
                        ),
                        body_style
                    )
                ]
            )
        team_slug = _filename_slug(team_name)
        images = [
            (
                "Own service-run length distribution",
                f"{team_slug}_service_runs.png"
            ),
            (
                "Opponent scoring-run length by own rotation"
                if is_u14
                else "Opponent scoring-run length by rotation",
                f"{team_slug}_rotation_runs.png"
            )
        ]
        if not is_u14:
            images.append(
                (
                    "Points by setter position",
                    f"{team_slug}_points_by_setter_position.png"
                )
            )
        for heading, image_name in images:
            image_path = markdown_file.parent / image_name
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
                    Paragraph(escape(heading), styles["Heading3"]),
                    image,
                    Spacer(1, 4 * mm)
                ]
            )

    document = SimpleDocTemplate(
        str(output_file),
        pagesize=landscape(A4),
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Match analysis"
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


def _extreme_summary(
    category: str,
    best: list[tuple[str, float]],
    worst: list[tuple[str, float]],
    interpretation: str
) -> str:
    if not best:
        return f"No {category} were recorded."

    def describe(rotations: list[tuple[str, float]]) -> str:
        return ", ".join(
            f"{label} ({mean:.2f} points/run)"
            for label, mean in rotations
        )

    return (
        f"For {category}, the best rotation(s) were "
        f"{describe(best)} and the worst were {describe(worst)} "
        f"({interpretation})."
    )


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
            f"Opponent scoring runs by rotation of {team_name}"
            if u14
            else f"Opponent scoring runs while {team_name} held rotation"
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
        title=f"{team_name} points scored during own service runs",
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
