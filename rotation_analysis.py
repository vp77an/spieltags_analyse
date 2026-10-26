import colorsys
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

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
    teams: dict[str, dict[str, Any]],
    output_dir: str | Path
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    team_names = {
        config["name"]: config["setters"]
        for config in teams.values()
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
            initial_rotation = _rotation_for(points[0], team_name, set_no)
            setters_by_team[team_name] = (
                team_names[team_name]
                if team_names[team_name]
                else [initial_rotation[0]]
            )
            for point in points:
                if point.get("scoring_team") not in team_names:
                    raise ValueError(
                        f"Set {set_no}: invalid scoring team "
                        f"{point.get('scoring_team')!r}"
                    )
                rotation = _rotation_for(point, team_name, set_no)
                rotation_key = _rotation_key(
                    rotation, setters_by_team[team_name]
                )
                rotation_data[team_name].setdefault(
                    rotation_key,
                    {"runs": [], "counts": Counter(), "lineups": set()}
                )
                rotation_data[team_name][rotation_key]["lineups"].add(rotation)

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

                rotation = _rotation_for(points[index], team_name, set_no)
                rotation_key = _rotation_key(
                    rotation, setters_by_team[team_name]
                )
                end_index = index + 1
                while end_index < len(points):
                    next_rotation = _rotation_for(
                        points[end_index], team_name, set_no
                    )
                    if (
                        points[end_index].get("scoring_team") == team_name
                        or _rotation_key(
                            next_rotation, setters_by_team[team_name]
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

            rotation = _rotation_for(points[index], serving_team, set_no)
            rotation_key = _rotation_key(
                rotation, setters_by_team[serving_team]
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
                        rotation_key, stats["lineups"], setters
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
            output_path / f"{_filename_slug(team_name)}_rotation_runs.png"
        )

        service_results = _distribution_results(
            service_run_data[team_name], setters
        )
        service_report["teams"].append(
            {"team": team_name, "setter_positions": service_results}
        )
        _plot_service_run_distribution(
            team_name,
            service_results,
            output_path / f"{_filename_slug(team_name)}_service_runs.png"
        )

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

    (output_path / "rotation_run_distributions.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    (output_path / "service_run_distributions.json").write_text(
        json.dumps(service_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    (output_path / "points_by_setter_position.json").write_text(
        json.dumps(points_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )
    return report


def _rotation_for(
    point: dict[str, Any],
    team_name: str,
    set_no: Any
) -> tuple[int, ...]:
    rotations = point.get("rotations")
    if not isinstance(rotations, dict) or team_name not in rotations:
        raise ValueError(
            f"Set {set_no}: team {team_name!r} has no rotation in point data"
        )
    rotation = rotations[team_name]
    if (
        not isinstance(rotation, list)
        or len(rotation) != 6
        or any(
            not isinstance(player, int) or isinstance(player, bool)
            for player in rotation
        )
    ):
        raise ValueError(
            f"Set {set_no}: invalid six-player rotation for {team_name!r}"
        )
    return tuple(rotation)


def _rotation_key(
    rotation: tuple[int, ...],
    setters: list[int]
) -> tuple[Any, ...]:
    if not setters:
        return ("setter", 0)
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
) -> int:
    if not setters:
        return 0
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
    setters: list[int]
) -> str:
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
    setters: list[int]
) -> list[dict[str, Any]]:
    results = []
    for rotation_key, stats in sorted(
        data.items(),
        key=lambda item: (
            0 if item[0][0] == "setter" else 1,
            item[0][1]
        )
    ):
        results.append(
            {
                "lineups": [
                    list(lineup) for lineup in sorted(stats["lineups"])
                ],
                "label": _rotation_label(
                    rotation_key, stats["lineups"], setters
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
            *(
                f"Laeufer {numeral}"
                for numeral in ROMAN_NUMERALS
            ),
            "Unknown"
        ]
    ]


def _plot_team_distribution(
    team_name: str,
    rotations: list[dict[str, Any]],
    output_file: Path
) -> None:
    _plot_run_distribution_bars(
        team_name,
        rotations,
        output_file,
        title=f"Opponent scoring runs while {team_name} held rotation",
        x_label="Consecutive points scored by opponent",
        legend_title="Rotation",
        empty_length=1
    )


def _plot_service_run_distribution(
    team_name: str,
    rotations: list[dict[str, Any]],
    output_file: Path
) -> None:
    _plot_run_distribution_bars(
        team_name,
        rotations,
        output_file,
        title=f"{team_name} points scored during own service runs",
        x_label="Points won before losing serve",
        legend_title="Setter position",
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
