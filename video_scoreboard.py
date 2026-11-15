"""Extract score changes from a video using a local vision model."""

from __future__ import annotations

import csv
import base64
import json
import itertools
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamScore:
    name: str
    sets: int
    points: int


@dataclass(frozen=True)
class ScoreboardState:
    teams: tuple[TeamScore, TeamScore]
    serving_team: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ScoreboardState:
        teams_value = value.get("teams")
        if not isinstance(teams_value, list) or len(teams_value) != 2:
            raise ValueError("A scoreboard must contain exactly two teams.")

        team_scores = [
            TeamScore(
                name=_required_team_name(team),
                sets=_required_score(team, "sets"),
                points=_required_score(team, "points")
            )
            for team in teams_value
        ]
        teams = (team_scores[0], team_scores[1])
        if teams[0].name == teams[1].name:
            raise ValueError("The two team names must be distinct.")
        serving_team = value.get("serving_team")
        if serving_team is not None and (
            not isinstance(serving_team, str)
            or serving_team not in {team.name for team in teams}
        ):
            raise ValueError("serving_team must match one of the team names.")

        return cls(teams=teams, serving_team=serving_team)

    @property
    def score_key(self) -> tuple[tuple[str, int, int], ...]:
        return tuple(
            (team.name, team.sets, team.points)
            for team in self.teams
        )

    @property
    def is_initial_score(self) -> bool:
        return all(team.sets == 0 and team.points == 0 for team in self.teams)


@dataclass(frozen=True)
class ScoreChange:
    timestamp_seconds: float
    state: ScoreboardState

    @property
    def timestamp(self) -> str:
        return _format_timestamp(self.timestamp_seconds)


def _format_timestamp(timestamp_seconds: float) -> str:
    total_milliseconds = round(timestamp_seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


class ScoreProgressionGuide:
    """Constrain video observations to the reconstructed match progression."""

    def __init__(self, point_data: Mapping[str, Any]) -> None:
        sets = point_data.get("sets")
        if not isinstance(sets, list) or not sets:
            raise ValueError(
                "Point-by-point data must contain at least one set."
            )

        self._states: list[ScoreboardState] = []
        self._signatures: list[tuple[tuple[int, int], ...]] = []
        self._state_rally_indices: list[int] = []
        self._rally_states: list[ScoreboardState] = []
        self._rally_cell_indices: list[int] = []
        self._rally_set_numbers: list[int] = []
        self._skipped_rally_numbers: list[int] = []
        self._untimed_rally_numbers: list[int] = []
        self._last_accepted_rally_index = -1
        teams: tuple[str, str] | None = None
        sets_won: dict[str, int] = {}
        self._teams: tuple[str, str] | None = None

        for set_index, set_data in enumerate(sets):
            if not isinstance(set_data, Mapping):
                raise ValueError("Each reconstructed set must be an object.")
            points = set_data.get("points")
            if not isinstance(points, list) or not points:
                raise ValueError(
                    f"Set {set_index + 1} has no reconstructed points."
                )

            scores_by_point: list[dict[str, int]] = []
            serving_teams: list[object] = []
            for point in points:
                if not isinstance(point, Mapping):
                    raise ValueError("Each reconstructed point must be an object.")
                score_after = point.get("score_after")
                if not isinstance(score_after, Mapping) or len(score_after) != 2:
                    raise ValueError(
                        "Each reconstructed score must contain two teams."
                    )
                point_teams = tuple(score_after)
                if teams is None:
                    teams = (point_teams[0], point_teams[1])
                    if any(
                        not isinstance(team, str) or not team.strip()
                        for team in teams
                    ):
                        raise ValueError(
                            "Reconstructed team names must be non-empty."
                        )
                    self._teams = teams
                    sets_won = {team: 0 for team in teams}
                elif set(point_teams) != set(teams):
                    raise ValueError(
                        "Reconstructed team names must be consistent."
                    )

                scores = {
                    team: _required_score(score_after, team)
                    for team in teams
                }
                scores_by_point.append(scores)
                serving_teams.append(point.get("serving_team"))

            if teams is None:
                raise ValueError("The point-by-point data has no teams.")
            final_score = scores_by_point[-1]
            winning_team = max(teams, key=lambda team: final_score[team])
            if final_score[teams[0]] == final_score[teams[1]]:
                raise ValueError(
                    f"Set {set_index + 1} has no unique winning team."
                )

            for point_index, (scores, serving_team) in enumerate(
                zip(scores_by_point, serving_teams, strict=True)
            ):
                point_sets_won = sets_won.copy()
                is_set_point = point_index + 1 == len(points)
                if is_set_point:
                    point_sets_won[winning_team] += 1
                    alias_state = self._make_expected_state(
                        teams,
                        point_sets_won,
                        scores,
                        serving_team
                    )
                    self._states.append(alias_state)
                    self._signatures.append(
                        tuple(
                            (sets_won[team], scores[team])
                            for team in teams
                        )
                    )
                    self._state_rally_indices.append(
                        len(self._rally_states)
                    )
                self._rally_states.append(
                    self._make_expected_state(
                        teams,
                        point_sets_won,
                        scores,
                        serving_team
                    )
                )
                previous_scores = (
                    scores_by_point[point_index - 1]
                    if point_index > 0
                    else {team: 0 for team in teams}
                )
                scoring_teams = [
                    team
                    for team in teams
                    if scores[team] == previous_scores[team] + 1
                ]
                if len(scoring_teams) != 1:
                    raise ValueError(
                        "Each reconstructed rally must award exactly one "
                        "point."
                    )
                scoring_row = teams.index(scoring_teams[0])
                self._rally_cell_indices.append(scoring_row * 2 + 1)
                self._rally_set_numbers.append(set_index)
                self._add_expected_state(
                    teams,
                    point_sets_won,
                    scores,
                    serving_team,
                    rally_index=len(self._rally_states) - 1
                )
            sets_won[winning_team] += 1
            if set_index + 1 < len(sets):
                self._add_expected_state(
                    teams,
                    sets_won,
                    {team: 0 for team in teams},
                    None,
                    rally_index=len(self._rally_states)
                )

        self._cursor = 0
        self._rally_cursor = 0
        self._has_accepted_score = False
        self._raw_team_mapping: dict[str, str] = {}
        self._row_mapping: tuple[str, str] | None = None

    @property
    def next_rally_state(self) -> ScoreboardState | None:
        if self._rally_cursor >= len(self._rally_states):
            return None
        return self._rally_states[self._rally_cursor]

    @property
    def next_rally_cell_index(self) -> int | None:
        if self._rally_cursor >= len(self._rally_cell_indices):
            return None
        return self._rally_cell_indices[self._rally_cursor]

    @property
    def next_rally_starts_set(self) -> bool:
        return (
            self._rally_cursor > 0
            and self._rally_cursor < len(self._rally_set_numbers)
            and self._rally_set_numbers[self._rally_cursor]
            != self._rally_set_numbers[self._rally_cursor - 1]
        )

    @property
    def total_rallies(self) -> int:
        return len(self._rally_states)

    @property
    def skipped_rally_numbers(self) -> tuple[int, ...]:
        return tuple(self._skipped_rally_numbers)

    @property
    def rally_states(self) -> tuple[ScoreboardState, ...]:
        return tuple(self._rally_states)

    @property
    def last_accepted_rally_index(self) -> int:
        return self._last_accepted_rally_index

    @property
    def unverified_rally_numbers(self) -> tuple[int, ...]:
        return tuple(sorted(
            set(self._skipped_rally_numbers)
            | set(self._untimed_rally_numbers)
            | set(range(self._last_accepted_rally_index + 2, self.total_rallies + 1))
        ))

    def mark_current_rally_untimed(self) -> None:
        if self._last_accepted_rally_index < 0:
            raise ValueError("No rally has been accepted.")
        self._untimed_rally_numbers.append(self._last_accepted_rally_index + 1)

    def use_match_team_order_for_rows(self) -> None:
        """Map top and bottom scoreboard rows to the report's team order."""
        self._row_mapping = self._teams

    def set_point_for_reset(
        self, reset: ScoreboardState, previous: ScoreboardState | None
    ) -> ScoreboardState | None:
        """Find the pending set point only when its predecessor was observed."""
        previous_index = self._last_accepted_rally_index
        final_index = previous_index + 1
        next_index = final_index + 1
        if (
            previous_index < 0
            or next_index >= len(self._rally_states)
            or self._rally_set_numbers[previous_index]
            != self._rally_set_numbers[final_index]
            or self._rally_set_numbers[final_index]
            == self._rally_set_numbers[next_index]
            or previous != self._rally_states[previous_index]
        ):
            return None
        final = self._rally_states[final_index]
        if tuple((team.sets, team.points) for team in reset.teams) != tuple(
            (team.sets, 0) for team in final.teams
        ):
            return None
        return final

    def accept_next_rally(self) -> ScoreboardState:
        state = self.next_rally_state
        if state is None:
            raise ValueError("The reconstructed match has no more rallies.")
        self.accept(state)
        self._rally_cursor += 1
        return state

    def match(
        self,
        state: ScoreboardState,
        allow_resynchronization: bool = False
    ) -> ScoreboardState | None:
        """Return the authoritative state if this score is in sequence."""
        if self._teams is None:
            return None

        raw_names = (state.teams[0].name, state.teams[1].name)
        raw_scores = tuple(
            (team.sets, team.points)
            for team in state.teams
        )
        if set(raw_names) == set(self._teams):
            self._raw_team_mapping.update(
                (name, name) for name in raw_names
            )
        mapped_teams = tuple(
            self._raw_team_mapping.get(name)
            for name in raw_names
        )
        if all(team is not None for team in mapped_teams):
            ordered_scores = self._scores_in_team_order(
                raw_scores,
                (mapped_teams[0], mapped_teams[1])
            )
            for index in self._matchable_indices(allow_resynchronization):
                if self._signatures[index] == ordered_scores:
                    return self._states[index]
            return None

        if self._row_mapping is not None:
            ordered_scores = self._scores_in_team_order(
                raw_scores,
                self._row_mapping
            )
            for index in self._matchable_indices(allow_resynchronization):
                if self._signatures[index] == ordered_scores:
                    self._raw_team_mapping.update(
                        zip(raw_names, self._row_mapping, strict=True)
                    )
                    return self._states[index]
            return None

        reversed_scores = (raw_scores[1], raw_scores[0])
        for index in self._matchable_indices(allow_resynchronization):
            expected = self._signatures[index]
            matches_forward = expected == raw_scores
            matches_reversed = expected == reversed_scores
            if not matches_forward and not matches_reversed:
                continue

            if matches_forward != matches_reversed:
                self._row_mapping = (
                    self._teams
                    if matches_forward
                    else (self._teams[1], self._teams[0])
                )
                self._raw_team_mapping.update(
                    zip(raw_names, self._row_mapping, strict=True)
                )
            return self._states[index]
        return None

    def accept(self, state: ScoreboardState) -> None:
        """Advance only after the analyzer confirms a stable score reading."""
        for index in range(self._cursor, len(self._states)):
            if self._states[index] == state:
                if all(team.points == 0 for team in state.teams):
                    self._cursor = index
                    self._has_accepted_score = True
                    return
                rally_index = self._state_rally_indices[index]
                first_unaccepted = self._last_accepted_rally_index + 1
                if rally_index > first_unaccepted:
                    self._skipped_rally_numbers.extend(
                        range(first_unaccepted + 1, rally_index + 1)
                    )
                self._last_accepted_rally_index = max(
                    self._last_accepted_rally_index,
                    rally_index
                )
                self._cursor = index
                self._has_accepted_score = True
                return

    def _matchable_indices(
        self,
        allow_resynchronization: bool = False
    ) -> Iterable[int]:
        if allow_resynchronization:
            return range(self._cursor, len(self._signatures))
        if not self._has_accepted_score:
            return range(self._cursor, len(self._signatures))

        maximum_rally_index = self._last_accepted_rally_index + 2
        indices = []
        for index in range(self._cursor, len(self._signatures)):
            if self._state_rally_indices[index] > maximum_rally_index:
                break
            indices.append(index)
        return tuple(indices)

    def _scores_in_team_order(
        self,
        raw_scores: tuple[tuple[int, int], tuple[int, int]],
        row_mapping: tuple[str | None, str | None]
    ) -> tuple[tuple[int, int], ...]:
        if self._teams is None:
            return ()
        scores_by_team = dict(zip(row_mapping, raw_scores, strict=True))
        if set(scores_by_team) != set(self._teams):
            return ()
        return tuple(scores_by_team[team] for team in self._teams)

    def _add_expected_state(
        self,
        teams: tuple[str, str],
        sets_won: Mapping[str, int],
        scores: Mapping[str, int],
        serving_team: object,
        rally_index: int
    ) -> None:
        state = self._make_expected_state(
            teams,
            sets_won,
            scores,
            serving_team
        )
        self._states.append(state)
        self._signatures.append(_score_signature(state.teams))
        self._state_rally_indices.append(rally_index)

    def _make_expected_state(
        self,
        teams: tuple[str, str],
        sets_won: Mapping[str, int],
        scores: Mapping[str, int],
        serving_team: object
    ) -> ScoreboardState:
        if serving_team not in teams:
            serving_team = None
        state = ScoreboardState(
            teams=tuple(
                TeamScore(team, sets_won[team], scores[team])
                for team in teams
            ),
            serving_team=serving_team
        )
        return state


class TesseractScoreboardRecognizer:
    """Read the four numeric scoreboard cells using local Tesseract OCR."""

    def __init__(
        self,
        scoreboard_region: tuple[float, float, float, float] = (
            0.002,
            0.069,
            0.162,
            0.158
        ),
        ocr: Callable[[object], str] | None = None
    ) -> None:
        x_min, y_min, x_max, y_max = scoreboard_region
        if not (
            0 <= x_min < x_max <= 1
            and 0 <= y_min < y_max <= 1
        ):
            raise ValueError(
                "scoreboard_region must be normalized bounds within the image."
            )
        if ocr is None:
            try:
                from PIL import ImageOps
                import pytesseract
            except ImportError as error:
                raise RuntimeError(
                    "Tesseract scoreboard recognition requires pytesseract. "
                    "Install it with `python -m pip install "
                    "-r requirements-video.txt`."
                ) from error
            if not pytesseract.pytesseract.tesseract_cmd:
                raise RuntimeError(
                    "The Tesseract executable is not configured."
                )

            def read_cell(image: object) -> list[str]:
                options = "-c tessedit_char_whitelist=0123456789"
                try:
                    grayscale_image = ImageOps.grayscale(image)
                    readings = []
                    for page_segmentation_mode in (10, 6, 7):
                        readings.append(pytesseract.image_to_string(
                            grayscale_image,
                            config=(
                                f"--psm {page_segmentation_mode} {options}"
                            )
                        ))
                    enlarged = grayscale_image.resize(
                        (
                            getattr(image, "width") * 8,
                            getattr(image, "height") * 8
                        )
                    )
                    thresholded = enlarged.point(
                        lambda value: 0 if value < 140 else 255
                    )
                    for page_segmentation_mode in (6, 8):
                        readings.append(
                            pytesseract.image_to_string(
                                thresholded,
                                config=(
                                    f"--psm {page_segmentation_mode} "
                                    f"{options}"
                                )
                            )
                        )
                    score_digit_threshold = enlarged.point(
                        lambda value: 0 if value < 120 else 255
                    )
                    readings.append(pytesseract.image_to_string(
                        score_digit_threshold,
                        config=f"--psm 10 {options}"
                    ))
                except pytesseract.TesseractNotFoundError as error:
                    raise RuntimeError(
                        "Tesseract is not installed or is not available on "
                        "PATH. Install the Tesseract OCR executable."
                    ) from error
                return readings
        else:
            def read_cell(image: object) -> list[str]:
                return [ocr(image)]

        self._scoreboard_region = scoreboard_region
        self._read_cell = read_cell
        self._previous_fingerprint: tuple[bytes, ...] | None = None
        self._previous_cell_values: tuple[tuple[int, ...], ...] | None = None

    def recognize(self, frame: object) -> ScoreboardState | None:
        candidates = self.recognize_candidates(frame)
        return candidates[0] if candidates else None

    def recognize_candidates(self, frame: object) -> list[ScoreboardState]:
        """Return distinct OCR readings so progression can resolve ambiguities."""
        region = self._crop_region(frame)
        cells = self._score_cells(region)
        fingerprint = self._fingerprint(cells)
        if (
            self._fingerprints_match(
                fingerprint,
                self._previous_fingerprint
            )
            and self._previous_cell_values is not None
        ):
            return self._states_from_cell_values(
                self._previous_cell_values
            )

        cell_values = (
            [list(values) for values in self._previous_cell_values]
            if self._previous_cell_values is not None
            else [[] for _ in cells]
        )
        changed_cells = (
            range(len(cells))
            if self._previous_fingerprint is None
            else [
                index
                for index, (current, previous) in enumerate(
                    zip(
                        fingerprint,
                        self._previous_fingerprint,
                        strict=True
                    )
                )
                if sum(left != right for left, right in zip(
                    current,
                    previous,
                    strict=True
                )) > 45
            ]
        )
        for index in changed_cells:
            parsed_values = []
            for text in self._read_cell(cells[index]):
                digits = "".join(
                    character
                    for character in text
                    if character.isdigit()
                )
                if digits and len(digits) <= 2:
                    value = int(digits)
                    if value not in parsed_values:
                        parsed_values.append(value)
            if parsed_values:
                cell_values[index] = parsed_values

        if any(not values for values in cell_values):
            return []

        self._previous_fingerprint = fingerprint
        self._previous_cell_values = tuple(
            tuple(values) for values in cell_values
        )
        return self._states_from_cell_values(self._previous_cell_values)

    @staticmethod
    def _states_from_cell_values(
        cell_values: tuple[tuple[int, ...], ...]
    ) -> list[ScoreboardState]:
        states = []
        seen_signatures: set[tuple[tuple[str, int, int], ...]] = set()
        for top_sets, top_points, bottom_sets, bottom_points in (
            itertools.product(*cell_values)
        ):
            state = ScoreboardState(
                teams=(
                    TeamScore("top", top_sets, top_points),
                    TeamScore("bottom", bottom_sets, bottom_points)
                )
            )
            if state.score_key not in seen_signatures:
                seen_signatures.add(state.score_key)
                states.append(state)
        return states

    def _crop_region(self, frame: object) -> object:
        crop = getattr(frame, "crop", None)
        width = getattr(frame, "width", None)
        height = getattr(frame, "height", None)
        if not callable(crop) or not isinstance(width, int) or not isinstance(
            height, int
        ):
            raise TypeError("Tesseract OCR requires a PIL-compatible image.")
        x_min, y_min, x_max, y_max = self._scoreboard_region
        return crop(
            (
                round(x_min * width),
                round(y_min * height),
                round(x_max * width),
                round(y_max * height)
            )
        )

    @staticmethod
    def _score_cells(region: object) -> list[object]:
        width = getattr(region, "width")
        height = getattr(region, "height")
        boxes = (
            (0.68, 0.06, 0.80, 0.43),
            (0.84, 0.06, 0.96, 0.43),
            (0.68, 0.57, 0.80, 0.94),
            (0.84, 0.57, 0.96, 0.94)
        )
        cells = []
        for x_min, y_min, x_max, y_max in boxes:
            cell = region.crop(
                (
                    round(x_min * width),
                    round(y_min * height),
                    round(x_max * width),
                    round(y_max * height)
                )
            )
            resize = getattr(cell, "resize")
            cells.append(resize((cell.width * 5, cell.height * 5)))
        return cells

    @staticmethod
    def _fingerprint(cells: list[object]) -> tuple[bytes, ...]:
        try:
            from PIL import ImageOps
        except ImportError as error:
            raise RuntimeError(
                "Scoreboard recognition requires Pillow. Install it with "
                "`python -m pip install -r requirements-video.txt`."
            ) from error

        fingerprints = []
        for index, cell in enumerate(cells):
            left = round(cell.width * 0.1)
            top = round(cell.height * 0.1)
            right = round(cell.width * 0.9)
            bottom = round(cell.height * 0.9)
            normalized = ImageOps.grayscale(
                cell.crop((left, top, right, bottom))
            )
            threshold = 200 if index % 2 == 0 else 70
            is_foreground = (
                (lambda value: 255 if value > threshold else 0)
                if index % 2 == 0
                else (lambda value: 255 if value < threshold else 0)
            )
            normalized = normalized.point(is_foreground).resize((24, 24))
            fingerprints.append(normalized.tobytes())
        return tuple(fingerprints)

    @staticmethod
    def _fingerprints_match(
        current: tuple[bytes, ...],
        previous: tuple[bytes, ...] | None
    ) -> bool:
        return previous is not None and all(
            sum(left != right for left, right in zip(
                current_cell,
                previous_cell,
                strict=True
            )) <= 45
            for current_cell, previous_cell in zip(
                current,
                previous,
                strict=True
            )
        )


class _ScoreboardRegionNotFoundError(RuntimeError):
    pass


class ScoreboardChangeRecognizer:
    """Detect score-digit changes without OCR or a pretrained model."""

    def __init__(
        self,
        scoreboard_region: tuple[float, float, float, float] | None = None,
        change_threshold: int = 8
    ) -> None:
        if scoreboard_region is not None:
            x_min, y_min, x_max, y_max = scoreboard_region
            if not (
                0 <= x_min < x_max <= 1
                and 0 <= y_min < y_max <= 1
            ):
                raise ValueError(
                    "scoreboard_region must be normalized bounds within "
                    "the image."
                )
        if change_threshold < 1:
            raise ValueError("change_threshold must be positive.")
        self._scoreboard_region = scoreboard_region
        self._detected_region: tuple[int, int, int, int] | None = None
        self._pending_cell_region: tuple[int, int, int, int] | None = None
        self._pending_cell_region_count = 0
        self._nearby_cell_region: tuple[int, int, int, int] | None = None
        self.change_threshold = change_threshold

    def cell_fingerprints(self, frame: object) -> tuple[bytes, ...]:
        """Return normalized binary glyph images for the four score cells."""
        try:
            from PIL import Image, ImageOps
        except ImportError as error:
            raise RuntimeError(
                "Scoreboard change detection requires Pillow. Install it "
                "with `python -m pip install -r requirements-video.txt`."
            ) from error

        region = self._crop_region(frame)
        fingerprints = []
        resampling = getattr(Image, "Resampling", Image).BOX
        for index, cell in enumerate(self._score_cells(region)):
            grayscale = ImageOps.grayscale(cell)
            left = round(cell.width * 0.2)
            top = round(cell.height * 0.15)
            right = round(cell.width * 0.8)
            bottom = round(cell.height * 0.85)
            glyph = grayscale.crop((left, top, right, bottom)).resize(
                (24, 24),
                resampling
            )
            threshold = self._otsu_threshold(glyph.histogram())
            foreground_is_light = index % 2 == 0
            fingerprints.append(bytes(
                255 if (
                    value > threshold
                    if foreground_is_light
                    else value < threshold
                ) else 0
                for value in glyph.tobytes()
            ))
        return tuple(fingerprints)

    def cell_digit_masks(
        self,
        frame: object
    ) -> tuple[tuple[bytes, ...], ...]:
        """Extract normalized digit glyphs from the four numeric cells."""
        try:
            import cv2
            import numpy as np
            from PIL import Image, ImageOps
        except ImportError as error:
            raise RuntimeError(
                "Digit classification requires OpenCV, NumPy, and Pillow. "
                "Install them with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error

        if self._scoreboard_region is None:
            try:
                self._detected_region = self._detect_region(frame)
            except _ScoreboardRegionNotFoundError as error:
                if self._detected_region is None:
                    raise
                logger.debug(
                    "Could not redetect scoreboard region; reusing the last "
                    "region: %s",
                    error
                )
        region = self._crop_region(frame)
        resampling = getattr(Image, "Resampling", Image).BOX
        cell_masks: list[tuple[bytes, ...]] = []
        for index, cell in enumerate(self._score_cells(region)):
            cell = cell.resize(
                (max(1, cell.width // 5), max(1, cell.height // 5)),
                resampling
            )
            grayscale = ImageOps.grayscale(cell).resize(
                (24, 24),
                resampling
            )
            threshold = self._otsu_threshold(grayscale.histogram())
            pixels = np.asarray(grayscale)
            foreground = pixels > threshold if index % 2 == 0 else (
                pixels < threshold
            )
            component_count, _, stats, _ = cv2.connectedComponentsWithStats(
                foreground.astype(np.uint8),
                connectivity=8
            )
            components = [
                (int(x), int(y), int(width), int(height))
                for x, y, width, height, area in stats[1:component_count]
                if area >= 8
                and height >= 7
                and width >= 2
                and x + width <= 18
            ]
            components.sort(key=lambda component: component[0])
            cell_masks.append(tuple(
                _normalize_digit_mask(
                    foreground[y:y + height, x:x + width],
                    cv2,
                    np
                )
                for x, y, width, height in components
            ))
        return tuple(cell_masks)

    def cell_templates(self, frame: object) -> tuple[bytes, ...]:
        """Extract whole numeric-cell images without segmenting the digits."""
        self._nearby_cell_region = None
        if self._scoreboard_region is None:
            try:
                detected_region = self._detect_region(frame)
                if self._detected_region is None:
                    self._detected_region = detected_region
                else:
                    left, top, right, bottom = self._detected_region
                    horizontal_tolerance = max(3, (right - left) * 0.02)
                    vertical_tolerance = max(3, (bottom - top) * 0.02)
                    tolerances = (
                        horizontal_tolerance, vertical_tolerance,
                        horizontal_tolerance, vertical_tolerance
                    )
                    if any(
                        abs(new - old) > tolerance
                        for new, old, tolerance in zip(
                            detected_region, self._detected_region,
                            tolerances, strict=True
                        )
                    ):
                        if (
                            self._pending_cell_region is not None
                            and all(
                                abs(new - old) <= tolerance
                                for new, old, tolerance in zip(
                                    detected_region, self._pending_cell_region,
                                    tolerances, strict=True
                                )
                            )
                        ):
                            self._pending_cell_region_count += 1
                        else:
                            self._pending_cell_region = detected_region
                            self._pending_cell_region_count = 1
                        if self._pending_cell_region_count >= 3:
                            self._detected_region = detected_region
                            self._pending_cell_region = None
                            self._pending_cell_region_count = 0
                    else:
                        self._nearby_cell_region = detected_region
                        self._pending_cell_region = None
                        self._pending_cell_region_count = 0
            except _ScoreboardRegionNotFoundError:
                self._pending_cell_region = None
                self._pending_cell_region_count = 0
                if self._detected_region is None:
                    raise
        region = self._crop_region(frame)
        return self._cell_templates_from_region(region)

    def nearby_cell_templates(self, frame: object) -> tuple[tuple[bytes, ...], ...]:
        """Re-extract from a nearby detected crop when the stable crop is unreadable."""
        current = self._detected_region
        nearby = self._nearby_cell_region
        if current is None or nearby is None or current == nearby:
            return ()
        crop = getattr(frame, "crop")
        horizontal = (nearby[0], current[1], nearby[2], current[3])
        bounds = {nearby, horizontal} - {current}
        return tuple(
            self._cell_templates_from_region(crop(region))
            for region in sorted(bounds)
        )

    def cell_template_variants(
        self, frame: object
    ) -> tuple[tuple[bytes, ...], ...]:
        """Create nearby crop variants for a labeled training frame."""
        baseline = self.cell_templates(frame)
        if self._scoreboard_region is None:
            bounds = self._detected_region
            if bounds is None:
                raise _ScoreboardRegionNotFoundError("Scoreboard crop is unavailable.")
        else:
            width = getattr(frame, "width")
            height = getattr(frame, "height")
            left, top, right, bottom = self._scoreboard_region
            bounds = (
                round(left * width), round(top * height),
                round(right * width), round(bottom * height)
            )
        left, top, right, bottom = bounds
        crop = getattr(frame, "crop")
        variants = [baseline]
        for right_offset in (-2, 0, 2):
            for bottom_offset in (-1, 0, 1):
                if right_offset == 0 and bottom_offset == 0:
                    continue
                variants.append(self._cell_templates_from_region(crop((
                    left, top, right + right_offset, bottom + bottom_offset
                ))))
        return tuple(variants)

    def _cell_templates_from_region(self, region: object) -> tuple[bytes, ...]:
        try:
            import numpy as np
            from PIL import Image, ImageOps
        except ImportError as error:
            raise RuntimeError(
                "Cell template recognition requires NumPy and Pillow. "
                "Install them with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error
        resampling = getattr(Image, "Resampling", Image).BOX
        templates = []
        for index, cell in enumerate(self._score_cells(region)):
            grayscale = ImageOps.grayscale(cell).resize((32, 32), resampling)
            threshold = self._otsu_threshold(grayscale.histogram())
            pixels = np.asarray(grayscale)
            foreground = pixels > threshold if index % 2 == 0 else (
                pixels < threshold
            )
            foreground[:, 25:] = False
            templates.append(np.packbits(foreground).tobytes())
        return tuple(templates)

    def changed_cells(
        self,
        previous: tuple[bytes, ...],
        current: tuple[bytes, ...]
    ) -> set[int]:
        if len(previous) != 4 or len(current) != 4:
            raise ValueError("A scoreboard fingerprint must contain four cells.")
        return {
            index
            for index, (old_cell, new_cell) in enumerate(
                zip(previous, current, strict=True)
            )
            if sum(old != new for old, new in zip(
                old_cell,
                new_cell,
                strict=True
            )) > self.change_threshold
        }

    @staticmethod
    def _otsu_threshold(histogram: list[int]) -> int:
        total = sum(histogram)
        weighted_total = sum(
            level * count for level, count in enumerate(histogram)
        )
        background_weight = 0
        background_total = 0
        best_variance = -1.0
        best_threshold = 0
        for threshold, count in enumerate(histogram):
            background_weight += count
            background_total += threshold * count
            foreground_weight = total - background_weight
            if background_weight == 0 or foreground_weight == 0:
                continue
            background_mean = background_total / background_weight
            foreground_mean = (
                weighted_total - background_total
            ) / foreground_weight
            variance = (
                background_weight
                * foreground_weight
                * (background_mean - foreground_mean) ** 2
            )
            if variance > best_variance:
                best_variance = variance
                best_threshold = threshold
        return best_threshold

    def _crop_region(self, frame: object) -> object:
        crop = getattr(frame, "crop", None)
        width = getattr(frame, "width", None)
        height = getattr(frame, "height", None)
        if not callable(crop) or not isinstance(width, int) or not isinstance(
            height, int
        ):
            raise TypeError(
                "Scoreboard change detection requires a PIL-compatible image."
            )
        if self._scoreboard_region is None:
            if self._detected_region is None:
                self._detected_region = self._detect_region(frame)
            return crop(self._detected_region)

        x_min, y_min, x_max, y_max = self._scoreboard_region
        return crop(
            (
                round(x_min * width),
                round(y_min * height),
                round(x_max * width),
                round(y_max * height)
            )
        )

    @staticmethod
    def _detect_region(frame: object) -> tuple[int, int, int, int]:
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "Automatic scoreboard detection requires OpenCV and NumPy. "
                "Install them with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error

        width = getattr(frame, "width")
        height = getattr(frame, "height")
        search_width = round(width * 0.4)
        search_height = round(height * 0.35)
        search_image = frame.convert("RGB").crop(
            (0, 0, search_width, search_height)
        )
        rgb = np.asarray(search_image)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        blue_mask = cv2.inRange(
            hsv,
            np.array((80, 40, 40), dtype=np.uint8),
            np.array((140, 255, 255), dtype=np.uint8)
        )
        row_counts = np.count_nonzero(blue_mask, axis=1)
        active_rows = np.flatnonzero(
            row_counts > max(10, round(search_width * 0.015))
        )
        bands: list[list[int]] = []
        for row in active_rows:
            if not bands or row > bands[-1][-1] + 2:
                bands.append([int(row)])
            else:
                bands[-1].append(int(row))

        blue_rows: list[tuple[int, int, int, int]] = []
        for band in bands:
            top, bottom = band[0], band[-1]
            row_height = bottom - top + 1
            if row_height < 5 or row_height > search_height * 0.25:
                continue
            column_counts = np.count_nonzero(
                blue_mask[top:bottom + 1],
                axis=0
            )
            active_columns = np.flatnonzero(
                column_counts >= max(3, round(row_height * 0.4))
            )
            if not len(active_columns):
                continue
            groups: list[list[int]] = []
            for column in active_columns:
                if not groups or column > groups[-1][-1] + 2:
                    groups.append([int(column)])
                else:
                    groups[-1].append(int(column))
            groups = [
                group
                for group in groups
                if search_width * 0.06 < len(group) < search_width * 0.4
            ]
            if groups:
                group = max(groups, key=len)
                blue_rows.append(
                    (top, bottom, group[0], group[-1] + 1)
                )

        candidates: list[
            tuple[float, tuple[int, int, int, int]]
        ] = []
        for index, first in enumerate(blue_rows):
            first_height = first[1] - first[0] + 1
            first_width = first[3] - first[2]
            for second in blue_rows[index + 1:]:
                second_height = second[1] - second[0] + 1
                second_width = second[3] - second[2]
                row_distance = second[0] - first[0]
                if not (
                    0.65 <= second_height / first_height <= 1.5
                    and 1.2 * max(first_height, second_height)
                    <= row_distance
                    <= 2.5 * max(first_height, second_height)
                    and 0.4 <= second_width / first_width <= 2.5
                    and abs(first[2] - second[2])
                    <= min(first_width, second_width) * 0.15
                ):
                    continue

                panel_width = min(first_width, second_width)
                board_width = panel_width / 0.30
                board_height = row_distance / 0.54
                if not 2.4 <= board_width / board_height <= 4.2:
                    continue
                right = round(min(first[3], second[3]) + board_width * 0.04)
                left = round(right - board_width)
                top = round(first[0] - board_height * 0.05)
                bottom = round(top + board_height)
                if not (
                    -board_width * 0.05 <= left <= width * 0.2
                    and 0 <= top <= height * 0.2
                    and right <= width
                    and bottom <= height
                ):
                    continue
                score = panel_width
                candidates.append(
                    (score, (left, top, right, bottom))
                )

        if not candidates:
            raise _ScoreboardRegionNotFoundError(
                "Could not automatically locate the two-row blue scoreboard "
                "in the upper-left of the video. Set video_scoreboard."
                "scoreboard_region to a tight normalized crop."
            )

        _, region = max(candidates, key=lambda candidate: candidate[0])
        left, top, right, bottom = region
        return (
            max(0, left),
            max(0, top),
            min(width, right),
            min(height, bottom)
        )

    @staticmethod
    def _score_cells(region: object) -> list[object]:
        width = getattr(region, "width")
        height = getattr(region, "height")
        boxes = (
            (0.68, 0.06, 0.80, 0.43),
            (0.84, 0.06, 0.96, 0.43),
            (0.68, 0.57, 0.80, 0.94),
            (0.84, 0.57, 0.96, 0.94)
        )
        cells = []
        for x_min, y_min, x_max, y_max in boxes:
            cell = region.crop(
                (
                    round(x_min * width),
                    round(y_min * height),
                    round(x_max * width),
                    round(y_max * height)
                )
            )
            cells.append(cell.resize((cell.width * 5, cell.height * 5)))
        return cells


class ScoreboardDigitClassifier:
    """Classify scoreboard digits from labeled, OCR-free image templates."""

    _template_shape = (24, 20)
    _template_size = _template_shape[0] * _template_shape[1]

    def __init__(
        self,
        templates: Mapping[int, Iterable[bytes]],
        maximum_distance: int = 80,
        minimum_margin: int = 7
    ) -> None:
        if maximum_distance < 0:
            raise ValueError("maximum_distance must not be negative.")
        if minimum_margin < 0:
            raise ValueError("minimum_margin must not be negative.")
        normalized_templates: dict[int, tuple[bytes, ...]] = {}
        for digit, digit_templates in templates.items():
            if not isinstance(digit, int) or isinstance(digit, bool) or not (
                0 <= digit <= 9
            ):
                raise ValueError("Digit template labels must be integers 0-9.")
            values = tuple(digit_templates)
            if any(
                not isinstance(template, bytes)
                or len(template) != self._template_size
                for template in values
            ):
                raise ValueError(
                    f"Templates for digit {digit} must contain "
                    f"{self._template_size} pixels."
                )
            if values:
                normalized_templates[digit] = values

        missing_digits = sorted(set(range(10)) - normalized_templates.keys())
        if missing_digits:
            raise ValueError(
                f"Digit templates are missing labels: {missing_digits}."
            )
        self._templates = normalized_templates
        self._encoded_templates = {
            digit: tuple(
                int.from_bytes(template, "little")
                for template in digit_templates
            )
            for digit, digit_templates in normalized_templates.items()
        }
        self.maximum_distance = maximum_distance
        self.minimum_margin = minimum_margin

    @property
    def template_count(self) -> int:
        return sum(
            len(digit_templates)
            for digit_templates in self._templates.values()
        )

    @classmethod
    def train(
        cls,
        samples: Iterable[
            tuple[
                tuple[tuple[bytes, ...], ...],
                tuple[int, int, int, int]
            ]
        ],
        maximum_distance: int = 80,
        minimum_margin: int = 7
    ) -> ScoreboardDigitClassifier:
        templates: dict[int, list[bytes]] = {
            digit: [] for digit in range(10)
        }
        sample_count = 0
        for cell_masks, scores in samples:
            if len(cell_masks) != 4 or len(scores) != 4:
                raise ValueError(
                    "Each training sample must contain four cells and "
                    "four score values."
                )
            sample_count += 1
            for cell_index, (glyphs, score) in enumerate(
                zip(cell_masks, scores, strict=True)
            ):
                if (
                    not isinstance(score, int)
                    or isinstance(score, bool)
                    or score < 0
                ):
                    raise ValueError("Training scores must be non-negative integers.")
                maximum_score = 3 if cell_index in (0, 2) else 30
                if score > maximum_score:
                    raise ValueError(
                        f"Training score for cell {cell_index} exceeds "
                        f"{maximum_score}."
                    )
                digits = str(score)
                if len(glyphs) != len(digits):
                    raise ValueError(
                        f"Training sample {sample_count}, cell {cell_index} "
                        f"has {len(glyphs)} glyphs for score {score}."
                    )
                for digit, glyph in zip(digits, glyphs, strict=True):
                    templates[int(digit)].append(glyph)

        if not sample_count:
            raise ValueError("At least one labeled training sample is required.")
        return cls(
            templates,
            maximum_distance=maximum_distance,
            minimum_margin=minimum_margin
        )

    def classify_digit(self, glyph: bytes) -> int | None:
        if len(glyph) != self._template_size:
            raise ValueError(
                f"A normalized digit glyph must contain "
                f"{self._template_size} pixels."
            )
        if any(pixel not in (0, 255) for pixel in glyph):
            raise ValueError("A normalized digit glyph must be binary.")
        encoded_glyph = int.from_bytes(glyph, "little")
        distances = sorted(
            (
                min(
                    (encoded_glyph ^ template).bit_count() // 8
                    for template in self._encoded_templates[digit]
                ),
                digit
            )
            for digit in self._encoded_templates
        )
        best_distance, best_digit = distances[0]
        second_best_distance = distances[1][0]
        if (
            best_distance > self.maximum_distance
            or second_best_distance - best_distance < self.minimum_margin
        ):
            return None
        return best_digit

    def classify_cell(self, glyphs: tuple[bytes, ...]) -> int | None:
        if not 1 <= len(glyphs) <= 2:
            return None
        digits = [self.classify_digit(glyph) for glyph in glyphs]
        if any(digit is None for digit in digits):
            return None
        value = int("".join(str(digit) for digit in digits))
        return value if value <= 30 else None

    def save(self, path: str | Path) -> None:
        model_path = Path(path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": 1,
            "maximum_distance": self.maximum_distance,
            "minimum_margin": self.minimum_margin,
            "templates": {
                str(digit): [
                    base64.b64encode(template).decode("ascii")
                    for template in digit_templates
                ]
                for digit, digit_templates in self._templates.items()
            }
        }
        model_path.write_text(
            json.dumps(payload, separators=(",", ":")) + "\n",
            encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> ScoreboardDigitClassifier:
        model_path = Path(path)
        if not model_path.is_file():
            raise FileNotFoundError(
                f"Scoreboard digit classifier not found: {model_path}"
            )
        try:
            payload = json.loads(model_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid scoreboard digit classifier: {model_path}"
            ) from error
        if not isinstance(payload, Mapping) or payload.get(
            "format_version"
        ) != 1:
            raise ValueError(
                f"Unsupported scoreboard digit classifier format: "
                f"{model_path}"
            )
        encoded_templates = payload.get("templates")
        if not isinstance(encoded_templates, Mapping):
            raise ValueError(
                f"Scoreboard digit classifier has no templates: {model_path}"
            )
        templates = {
            int(digit): tuple(
                base64.b64decode(encoded, validate=True)
                for encoded in values
            )
            for digit, values in encoded_templates.items()
            if isinstance(values, list)
        }
        return cls(
            templates,
            maximum_distance=payload.get("maximum_distance", 80),
            minimum_margin=payload.get("minimum_margin", 7)
        )


def _normalize_digit_mask(
    component: object,
    cv2_module: Any,
    numpy_module: Any
) -> bytes:
    ys, xs = numpy_module.nonzero(component)
    if not len(xs):
        raise ValueError("Cannot normalize an empty digit mask.")
    cropped = component[
        ys.min():ys.max() + 1,
        xs.min():xs.max() + 1
    ].astype(numpy_module.uint8)
    height, width = cropped.shape
    scale = min(22 / height, 18 / width)
    normalized_height = max(1, round(height * scale))
    normalized_width = max(1, round(width * scale))
    resized = cv2_module.resize(
        cropped,
        (normalized_width, normalized_height),
        interpolation=cv2_module.INTER_NEAREST
    )
    canvas = numpy_module.zeros((24, 20), dtype=numpy_module.uint8)
    left = (20 - normalized_width) // 2
    canvas[
        24 - normalized_height:24,
        left:left + normalized_width
    ] = resized * 255
    return canvas.tobytes()


class ScoreboardDigitRecognizer:
    """Recognize the four visible scores with a trained digit classifier."""

    def __init__(
        self,
        classifier: ScoreboardDigitClassifier,
        scoreboard_region: tuple[float, float, float, float] | None = None
    ) -> None:
        self._classifier = classifier
        self._cell_reader = ScoreboardChangeRecognizer(scoreboard_region)

    def recognize(self, frame: object) -> ScoreboardState | None:
        cell_masks = self._cell_reader.cell_digit_masks(frame)
        values = tuple(
            self._classifier.classify_cell(glyphs)
            for glyphs in cell_masks
        )
        if any(value is None for value in values):
            return None
        top_sets, top_points, bottom_sets, bottom_points = values
        if (
            top_sets is None
            or top_points is None
            or bottom_sets is None
            or bottom_points is None
        ):
            return None
        if (
            top_sets > 3
            or bottom_sets > 3
            or top_points > 30
            or bottom_points > 30
        ):
            return None
        return ScoreboardState(
            teams=(
                TeamScore("top", top_sets, top_points),
                TeamScore("bottom", bottom_sets, bottom_points)
            )
        )


class ScoreboardCellClassifier:
    """Classify a complete numeric cell against labeled image examples."""

    template_size = 128

    def __init__(
        self,
        templates: Mapping[str, Mapping[int, Iterable[bytes]]],
        maximum_distance: int = 25,
        minimum_margin: int = 8
    ) -> None:
        if maximum_distance < 0 or minimum_margin < 0:
            raise ValueError("Template distance and margin must be non-negative.")
        self.maximum_distance = maximum_distance
        self.minimum_margin = minimum_margin
        self._templates: dict[str, dict[int, tuple[int, ...]]] = {}
        self._raw_templates: dict[str, dict[int, tuple[bytes, ...]]] = {}
        for kind in ("sets", "points"):
            cells = templates.get(kind, {})
            if not cells:
                raise ValueError(f"No {kind} cell templates were supplied.")
            self._raw_templates[kind] = {}
            self._templates[kind] = {}
            for value, examples in cells.items():
                if not isinstance(value, int) or value < 0:
                    raise ValueError("Cell template values must be non-negative integers.")
                raw = tuple(examples)
                if not raw or any(
                    not isinstance(example, bytes)
                    or len(example) != self.template_size
                    for example in raw
                ):
                    raise ValueError("Each cell template must be a 32 by 32 binary image.")
                self._raw_templates[kind][value] = raw
                self._templates[kind][value] = tuple(
                    int.from_bytes(example, "big") for example in raw
                )

    @classmethod
    def train(
        cls,
        samples: Iterable[
            tuple[tuple[bytes, ...], tuple[int, int, int, int]]
        ]
    ) -> ScoreboardCellClassifier:
        templates: dict[str, dict[int, list[bytes]]] = {
            "sets": {},
            "points": {}
        }
        for cells, scores in samples:
            if len(cells) != 4 or len(scores) != 4:
                raise ValueError("Each sample must have four cells and scores.")
            for index, (cell, score) in enumerate(zip(cells, scores, strict=True)):
                kind = "sets" if index % 2 == 0 else "points"
                templates[kind].setdefault(score, []).append(cell)
        return cls(templates)

    def classify_cell(self, cell: bytes, kind: str) -> int | None:
        if len(cell) != self.template_size:
            raise ValueError("A cell template must be a 32 by 32 binary image.")
        if kind not in self._templates:
            raise ValueError(f"Unknown score cell type: {kind}")
        encoded = int.from_bytes(cell, "big")
        distances = sorted(
            (min((encoded ^ example).bit_count() for example in examples), value)
            for value, examples in self._templates[kind].items()
        )
        best_distance, best_value = distances[0]
        if best_distance > self.maximum_distance:
            return None
        if (
            len(distances) > 1
            and distances[1][0] - best_distance < self.minimum_margin
        ):
            return None
        return best_value

    def save(self, path: str | Path) -> None:
        model_path = Path(path)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": 1,
            "kind": "whole_cell",
            "maximum_distance": self.maximum_distance,
            "minimum_margin": self.minimum_margin,
            "templates": {
                kind: {
                    str(value): [base64.b64encode(cell).decode("ascii") for cell in cells]
                    for value, cells in values.items()
                }
                for kind, values in self._raw_templates.items()
            }
        }
        model_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ScoreboardCellClassifier:
        model_path = Path(path)
        if not model_path.is_file():
            raise FileNotFoundError(f"Scoreboard cell template file not found: {model_path}")
        try:
            payload = json.loads(model_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("format_version") != 1
                or payload.get("kind") != "whole_cell"
                or not isinstance(payload.get("templates"), dict)
            ):
                raise ValueError("Unsupported cell template format.")
            templates = {
                kind: {
                    int(value): tuple(
                        base64.b64decode(example, validate=True)
                        for example in examples
                    )
                    for value, examples in values.items()
                }
                for kind, values in payload["templates"].items()
            }
            return cls(
                templates,
                maximum_distance=payload["maximum_distance"],
                minimum_margin=payload["minimum_margin"]
            )
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Invalid scoreboard cell template file: {model_path}") from error


class ScoreboardCellRecognizer:
    """Read scoreboard values using labeled whole-cell image templates."""

    def __init__(
        self,
        classifier: ScoreboardCellClassifier,
        scoreboard_region: tuple[float, float, float, float] | None = None
    ) -> None:
        self._classifier = classifier
        self._cell_reader = ScoreboardChangeRecognizer(scoreboard_region)

    def recognize(self, frame: object) -> ScoreboardState | None:
        try:
            cells = self._cell_reader.cell_templates(frame)
        except _ScoreboardRegionNotFoundError as error:
            logger.debug("Scoreboard not visible in this frame: %s", error)
            return None
        def classify(cells: tuple[bytes, ...]) -> tuple[int | None, ...]:
            return tuple(
                self._classifier.classify_cell(
                    cell, "sets" if index % 2 == 0 else "points"
                )
                for index, cell in enumerate(cells)
            )

        values = classify(cells)
        if any(value is None for value in values):
            alternatives = {
                candidate
                for candidate in (
                    classify(cells)
                    for cells in self._cell_reader.nearby_cell_templates(frame)
                )
                if all(value is not None for value in candidate)
                and all(
                    original is None or original == replacement
                    for original, replacement in zip(values, candidate, strict=True)
                )
            }
            if len(alternatives) == 1:
                values = alternatives.pop()
        top_sets, top_points, bottom_sets, bottom_points = values
        if (
            top_sets is None or top_points is None
            or bottom_sets is None or bottom_points is None
            or top_sets > 3 or bottom_sets > 3
        ):
            return None
        return ScoreboardState(teams=(
            TeamScore("top", top_sets, top_points),
            TeamScore("bottom", bottom_sets, bottom_points)
        ))


class ScoreboardRecognizer(Protocol):
    def recognize(self, frame: object) -> ScoreboardState | None:
        """Return the score visible in a frame, or None when unreadable."""


@runtime_checkable
class ScoreboardFingerprintRecognizer(Protocol):
    def cell_fingerprints(self, frame: object) -> tuple[bytes, ...]:
        """Return visual fingerprints for the scoreboard's numeric cells."""

    def changed_cells(
        self,
        previous: tuple[bytes, ...],
        current: tuple[bytes, ...]
    ) -> set[int]:
        """Return the numeric cells that differ between two frames."""


@runtime_checkable
class ScoreboardCandidateRecognizer(ScoreboardRecognizer, Protocol):
    def recognize_candidates(
        self,
        frame: object
    ) -> Iterable[ScoreboardState]:
        """Return possible score readings from a frame."""


class VideoFrameSource(Protocol):
    def iter_frames(
        self,
        video_path: Path,
        sample_interval_seconds: float,
        start_time_seconds: float = 0.0
    ) -> Iterable[tuple[float, object]]:
        """Yield (video timestamp in seconds, frame) pairs."""


class OpenCvFrameSource:
    """Read a video at a fixed interval and convert frames to RGB images."""

    def iter_frames(
        self,
        video_path: Path,
        sample_interval_seconds: float,
        start_time_seconds: float = 0.0
    ) -> Iterable[tuple[float, object]]:
        try:
            import cv2
            from PIL import Image
        except ImportError as error:
            raise RuntimeError(
                "Video reading requires the optional video dependencies. "
                "Install them with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error

        if not video_path.is_file():
            raise FileNotFoundError(f"Video not found: {video_path}")
        if (
            not math.isfinite(start_time_seconds)
            or start_time_seconds < 0
        ):
            raise ValueError("start_time_seconds must be finite and non-negative.")

        capture = cv2.VideoCapture(str(video_path))
        try:
            if not capture.isOpened():
                raise ValueError(f"Could not open video: {video_path}")

            frames_per_second = capture.get(cv2.CAP_PROP_FPS)
            if not math.isfinite(frames_per_second) or frames_per_second <= 0:
                raise ValueError(
                    f"Video has an invalid frame rate: {video_path}"
                )

            frame_step = max(
                1,
                round(frames_per_second * sample_interval_seconds)
            )
            start_frame = round(start_time_seconds * frames_per_second)
            frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if math.isfinite(frame_count) and frame_count > 0:
                for frame_index in range(
                    start_frame,
                    int(frame_count),
                    frame_step
                ):
                    timestamp = frame_index / frames_per_second
                    if not capture.set(
                        cv2.CAP_PROP_POS_MSEC,
                        timestamp * 1000
                    ):
                        raise ValueError(
                            f"Could not seek to {timestamp:.3f}s "
                            f"in video: {video_path}"
                        )
                    read_successfully, frame = capture.read()
                    if not read_successfully:
                        break
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    yield frame_index / frames_per_second, Image.fromarray(
                        rgb_frame
                    )
            else:
                if start_frame and not capture.set(
                    cv2.CAP_PROP_POS_MSEC,
                    start_time_seconds * 1000
                ):
                    raise ValueError(
                        f"Could not seek to {start_time_seconds:.3f}s "
                        f"in video: {video_path}"
                    )
                frame_index = start_frame
                while capture.grab():
                    if (frame_index - start_frame) % frame_step == 0:
                        read_successfully, frame = capture.retrieve()
                        if not read_successfully:
                            raise ValueError(
                                f"Could not decode video frame {frame_index} "
                                f"from {video_path}"
                            )
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        yield frame_index / frames_per_second, Image.fromarray(
                            rgb_frame
                        )
                    frame_index += 1
        finally:
            capture.release()


class HuggingFaceScoreboardRecognizer:
    """Run a local image-to-text model fine-tuned to emit scoreboard JSON."""

    def __init__(
        self,
        model_path: str | Path,
        pipeline: Any | None = None,
        crop_region: tuple[float, float, float, float] = (
            0.0, 0.0, 0.6, 0.35
        )
    ) -> None:
        x_min, y_min, x_max, y_max = crop_region
        if not (
            0 <= x_min < x_max <= 1
            and 0 <= y_min < y_max <= 1
        ):
            raise ValueError(
                "crop_region must be normalized bounds within the image."
            )
        if pipeline is None:
            if not Path(model_path).is_dir():
                raise FileNotFoundError(
                    f"Local scoreboard model directory not found: "
                    f"{model_path}"
                )
            try:
                from transformers import pipeline as create_pipeline
            except ImportError as error:
                raise RuntimeError(
                    "Model inference requires the optional video "
                    "dependencies. Install them with `python -m pip install "
                    "-r requirements-video.txt`."
                ) from error
            pipeline = create_pipeline(
                "image-to-text",
                model=str(model_path)
            )
        self._pipeline = pipeline
        self._crop_region = crop_region

    def recognize(self, frame: object) -> ScoreboardState | None:
        output = self._pipeline(self._crop_frame(frame))
        if (
            not isinstance(output, list)
            or not output
            or not isinstance(output[0], Mapping)
        ):
            raise ValueError(
                "The vision model output must be a non-empty list of objects."
            )

        generated_text = output[0].get("generated_text")
        if not isinstance(generated_text, str):
            raise ValueError(
                "The vision model output must contain generated_text."
            )

        try:
            payload = _decode_json_object(generated_text)
            return ScoreboardState.from_mapping(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            logger.warning(
                "Ignoring unreadable scoreboard model output: %s",
                error
            )
            return None

    def _crop_frame(self, frame: object) -> object:
        crop = getattr(frame, "crop", None)
        width = getattr(frame, "width", None)
        height = getattr(frame, "height", None)
        if not callable(crop) or not isinstance(width, int) or not isinstance(
            height, int
        ):
            raise TypeError("The vision model requires a PIL-compatible image.")

        x_min, y_min, x_max, y_max = self._crop_region
        return crop(
            (
                round(x_min * width),
                round(y_min * height),
                round(x_max * width),
                round(y_max * height)
            )
        )


class TimeoutDetector(Protocol):
    def detect(self, frame: object) -> tuple[bool | None, bool | None]:
        """Return banner visibility for each scoreboard row, if readable."""


class TimeoutBannerDetector:
    """Match a timeout-label image in extensions of the scoreboard rows."""

    def __init__(
        self,
        template_path: str | Path,
        scoreboard_region: tuple[float, float, float, float] | None = None
    ) -> None:
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError(
                "Timeout detection requires OpenCV. Install the optional "
                "dependencies with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error
        path = Path(template_path)
        if not path.is_file():
            raise FileNotFoundError(f"Timeout banner template not found: {path}")
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None or min(template.shape) < 3 or template.std() < 1:
            raise ValueError(f"Invalid timeout banner template image: {path}")
        self._template = template
        self._scoreboard_region = scoreboard_region

    def detect(self, frame: object) -> tuple[bool | None, bool | None]:
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "Timeout detection requires OpenCV and NumPy. Install the "
                "optional dependencies with `python -m pip install "
                "-r requirements-video.txt`."
            ) from error

        width = getattr(frame, "width")
        height = getattr(frame, "height")
        if self._scoreboard_region is None:
            try:
                left, top, right, bottom = ScoreboardChangeRecognizer._detect_region(
                    frame
                )
            except _ScoreboardRegionNotFoundError:
                return (None, None)
        else:
            x1, y1, x2, y2 = self._scoreboard_region
            left, top, right, bottom = (
                round(x1 * width), round(y1 * height),
                round(x2 * width), round(y2 * height)
            )
        board_width, board_height = right - left, bottom - top
        x1 = round(right + board_width * 0.02)
        x2 = round(right + board_width * 0.35)
        if x1 < 0 or x2 > width or x2 <= x1:
            return (None, None)
        results: list[bool | None] = []
        crop = getattr(frame, "crop")
        for y1, y2 in ((0.09, 0.40), (0.60, 0.91)):
            box = (
                x1, round(top + y1 * board_height),
                x2, round(top + y2 * board_height)
            )
            if box[1] < 0 or box[3] > height or box[3] <= box[1]:
                results.append(None)
                continue
            rgb = np.asarray(crop(box).convert("RGB"))
            hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
            color_area = hsv[:, :round(hsv.shape[1] * 0.65)]
            blue = (
                (color_area[:, :, 0] >= 80) & (color_area[:, :, 0] <= 140)
                & (color_area[:, :, 1] >= 40) & (color_area[:, :, 2] >= 40)
            )
            blue_fraction = float(np.mean(blue))
            if blue_fraction < 0.15:
                results.append(False)
                continue
            if blue_fraction < 0.65:
                results.append(None)
                continue
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            matches = []
            for fraction in (0.13, 0.145, 0.16, 0.175, 0.19, 0.205):
                template_width = round(board_width * fraction)
                template_height = round(
                    template_width * self._template.shape[0]
                    / self._template.shape[1]
                )
                if (
                    template_width < 3 or template_height < 3
                    or template_width > gray.shape[1]
                    or template_height > gray.shape[0]
                ):
                    continue
                template = cv2.resize(
                    self._template, (template_width, template_height)
                )
                matches.append(float(cv2.matchTemplate(
                    gray, template, cv2.TM_CCOEFF_NORMED
                ).max()))
            if not matches:
                results.append(None)
            elif max(matches) >= 0.84:
                results.append(True)
            elif max(matches) <= 0.80:
                results.append(False)
            else:
                results.append(None)
        return (results[0], results[1])


@dataclass(frozen=True)
class TimeoutEvent:
    event_type: str
    team_index: int
    timestamp_seconds: float | None
    order_seconds: float


class _TimeoutRowTracker:
    def __init__(self, team_index: int, stable_observations: int) -> None:
        self.team_index = team_index
        self.stable_observations = stable_observations
        self.stable: bool | None = None
        self.candidate: bool | None = None
        self.candidate_count = 0
        self.candidate_timestamp = 0.0
        self.candidate_has_boundary = False
        self.previous: bool | None = None
        self.previous_timestamp = -1.0

    def observe(
        self, value: bool | None, timestamp: float, interval: float
    ) -> TimeoutEvent | None:
        if value is None:
            self.candidate = None
            self.candidate_count = 0
            self.previous = None
            return None
        if value != self.candidate or (
            self.previous_timestamp >= 0
            and timestamp - self.previous_timestamp > interval * 1.5
        ):
            self.candidate = value
            self.candidate_count = 1
            self.candidate_timestamp = timestamp
            self.candidate_has_boundary = (
                self.previous is not None
                and self.previous != value
                and timestamp - self.previous_timestamp <= interval * 1.5
            )
        else:
            self.candidate_count += 1
        self.previous = value
        self.previous_timestamp = timestamp
        if self.candidate_count < self.stable_observations or value == self.stable:
            return None
        previous_stable = self.stable
        self.stable = value
        if previous_stable is None and not value:
            return None
        return TimeoutEvent(
            "timeout_start" if value else "timeout_end",
            self.team_index,
            self.candidate_timestamp if self.candidate_has_boundary else None,
            self.candidate_timestamp
        )

    def finish(self) -> TimeoutEvent | None:
        if self.stable:
            return TimeoutEvent("timeout_end", self.team_index, None, math.inf)
        return None


class VideoScoreboardAnalyzer:
    """Sample a video, stabilize recognized scores, and write a CSV log."""

    def __init__(
        self,
        recognizer: ScoreboardRecognizer | ScoreboardFingerprintRecognizer,
        frame_source: VideoFrameSource | None = None,
        sample_interval_seconds: float = 0.5,
        stable_observations: int = 2,
        progression_guide: ScoreProgressionGuide | None = None,
        start_time_seconds: float = 0.0,
        timeout_detector: TimeoutDetector | None = None
    ) -> None:
        if (
            not math.isfinite(sample_interval_seconds)
            or sample_interval_seconds <= 0
        ):
            raise ValueError("sample_interval_seconds must be positive.")
        if stable_observations < 1:
            raise ValueError("stable_observations must be at least one.")
        if (
            not math.isfinite(start_time_seconds)
            or start_time_seconds < 0
        ):
            raise ValueError("start_time_seconds must be finite and non-negative.")

        self._recognizer = recognizer
        self._frame_source = frame_source or OpenCvFrameSource()
        self._sample_interval_seconds = sample_interval_seconds
        self._stable_observations = stable_observations
        self._progression_guide = progression_guide
        if (
            progression_guide is not None
            and isinstance(recognizer, ScoreboardCellRecognizer)
        ):
            progression_guide.use_match_team_order_for_rows()
        self._start_time_seconds = start_time_seconds
        self._timeout_detector = timeout_detector
        self.timeout_events: tuple[TimeoutEvent, ...] = ()
        self._guided_candidate_window_seconds = max(
            30.0,
            sample_interval_seconds * stable_observations * 5
        )

    def analyze(
        self,
        video_path: str | Path,
        log_path: str | Path
    ) -> list[ScoreChange]:
        video_path = Path(video_path)
        log_path = Path(log_path)
        self.timeout_events = ()
        changes: list[ScoreChange] = []
        last_emitted_key: tuple[tuple[str, int, int], ...] | None = None
        candidate: ScoreboardState | None = None
        candidate_timestamp = 0.0
        candidate_has_transition_evidence = False
        candidate_previous_state: ScoreboardState | None = None
        candidate_count = 0
        previous_timestamp = -1.0
        previous_reading: ScoreboardState | None = None
        previous_reading_timestamp = -1.0
        last_confirmed_timestamp = self._start_time_seconds
        last_written_rally_index = -1
        visual_baseline: tuple[bytes, ...] | None = None
        set_reset_cells_seen: set[str] = set()
        set_reset_complete = False
        timeout_events: list[TimeoutEvent] = []
        timeout_trackers = [
            _TimeoutRowTracker(index, self._stable_observations)
            for index in range(2)
        ]
        timeout_visible_samples = 0

        log_path.parent.mkdir(parents=True, exist_ok=True)
        teams = (
            tuple(team.name for team in self._progression_guide.rally_states[0].teams)
            if self._progression_guide is not None else ("", "")
        )
        log = _ScoreboardCsvLog(log_path, teams)

        for timestamp, frame in self._frame_source.iter_frames(
            video_path,
            self._sample_interval_seconds,
            self._start_time_seconds
        ):
            if (
                not math.isfinite(timestamp)
                or timestamp < 0
                or timestamp <= previous_timestamp
            ):
                raise ValueError(
                    "Video frame timestamps must be finite and increasing."
                )
            if (
                isinstance(self._recognizer, ScoreboardCellRecognizer)
                and previous_timestamp >= 0
                and timestamp - previous_timestamp
                > self._sample_interval_seconds * 1.5
            ):
                candidate = None
                candidate_count = 0
            previous_timestamp = timestamp
            if self._timeout_detector is not None:
                timeout_readings = self._timeout_detector.detect(frame)
                if all(value is not None for value in timeout_readings):
                    timeout_visible_samples += 1
                for tracker, reading in zip(
                    timeout_trackers, timeout_readings, strict=True
                ):
                    event = tracker.observe(
                        reading, timestamp, self._sample_interval_seconds
                    )
                    if event is not None:
                        timeout_events.append(event)
                        log.add_timeout(event)

            if isinstance(
                self._recognizer,
                ScoreboardFingerprintRecognizer
            ):
                if self._progression_guide is None:
                    raise ValueError(
                        "Image-change recognition requires point-by-point "
                        "progression data."
                    )
                current_fingerprint = self._recognizer.cell_fingerprints(
                    frame
                )
                if visual_baseline is None:
                    visual_baseline = current_fingerprint
                    continue

                changed_cells = self._recognizer.changed_cells(
                    visual_baseline,
                    current_fingerprint
                )
                expected_state = self._progression_guide.next_rally_state
                expected_cell = (
                    self._progression_guide.next_rally_cell_index
                )
                if expected_state is None or expected_cell is None:
                    continue

                if (
                    self._progression_guide.next_rally_starts_set
                    and not set_reset_complete
                ):
                    if 0 in changed_cells or 2 in changed_cells:
                        set_reset_cells_seen.add("sets")
                    if 1 in changed_cells or 3 in changed_cells:
                        set_reset_cells_seen.add("points")
                    if set_reset_cells_seen != {"sets", "points"}:
                        visual_baseline = current_fingerprint
                        candidate = None
                        candidate_count = 0
                        continue
                    visual_baseline = current_fingerprint
                    set_reset_complete = True
                    candidate = None
                    candidate_count = 0
                    continue

                if expected_cell not in changed_cells:
                    candidate = None
                    candidate_count = 0
                    continue

                if (
                    candidate is not None
                    and candidate.score_key == expected_state.score_key
                ):
                    candidate_count += 1
                else:
                    candidate = expected_state
                    candidate_timestamp = timestamp
                    candidate_count = 1

                if candidate_count < self._stable_observations:
                    continue

                accepted = self._progression_guide.accept_next_rally()
                change = ScoreChange(candidate_timestamp, accepted)
                changes.append(change)
                log.add_rally(change.state, change.timestamp)
                visual_baseline = current_fingerprint
                candidate = None
                candidate_count = 0
                set_reset_cells_seen.clear()
                set_reset_complete = False
                continue

            if (
                self._progression_guide is not None
                and candidate is not None
                and timestamp - candidate_timestamp
                > self._guided_candidate_window_seconds
            ):
                candidate = None
                candidate_count = 0

            if self._progression_guide is not None:
                observed_states: Iterable[ScoreboardState | None] = (
                    self._recognizer.recognize_candidates(frame)
                    if isinstance(
                        self._recognizer,
                        ScoreboardCandidateRecognizer
                    )
                    else (self._recognizer.recognize(frame),)
                )
                state = None
                transition_evidence = False
                previous_matched_state = None
                for observed_state in observed_states:
                    if observed_state is None:
                        continue
                    if isinstance(self._recognizer, ScoreboardCellRecognizer):
                        transition_evidence = (
                            previous_reading is not None
                            and previous_reading.score_key != observed_state.score_key
                            and timestamp - previous_reading_timestamp
                            <= self._sample_interval_seconds * 1.5
                        )
                        if transition_evidence and previous_reading is not None:
                            previous_matched_state = self._progression_guide.match(
                                previous_reading
                            )
                        previous_reading = observed_state
                        previous_reading_timestamp = timestamp
                    matched_state = self._progression_guide.match(
                        observed_state,
                        allow_resynchronization=(
                            timestamp - last_confirmed_timestamp >= 60
                        )
                    )
                    if (
                        matched_state is not None
                        and matched_state.score_key != last_emitted_key
                    ):
                        state = matched_state
                        break
                if state is None:
                    if isinstance(self._recognizer, ScoreboardCellRecognizer):
                        candidate = None
                        candidate_count = 0
                    continue
            else:
                state = self._recognizer.recognize(frame)
                if state is None:
                    candidate = None
                    candidate_count = 0
                    continue

            if state.score_key == last_emitted_key:
                if (
                    self._progression_guide is None
                    or isinstance(self._recognizer, ScoreboardCellRecognizer)
                ):
                    candidate = None
                    candidate_count = 0
                continue

            if candidate is not None and state.score_key == candidate.score_key:
                candidate_count += 1
            else:
                candidate = state
                candidate_timestamp = timestamp
                candidate_has_transition_evidence = (
                    transition_evidence
                    if self._progression_guide is not None
                    and isinstance(self._recognizer, ScoreboardCellRecognizer)
                    else True
                )
                candidate_previous_state = (
                    previous_matched_state
                    if self._progression_guide is not None
                    and isinstance(self._recognizer, ScoreboardCellRecognizer)
                    else None
                )
                candidate_count = 1

            if candidate_count < self._stable_observations:
                continue

            if (
                self._progression_guide is not None
                and isinstance(self._recognizer, ScoreboardCellRecognizer)
                and candidate_has_transition_evidence
                and all(team.points == 0 for team in candidate.teams)
            ):
                final = self._progression_guide.set_point_for_reset(
                    candidate, candidate_previous_state
                )
                if final is not None and candidate_previous_state is not None:
                    self._progression_guide.accept(final)
                    for missing_index in range(
                        last_written_rally_index + 1,
                        self._progression_guide.last_accepted_rally_index
                    ):
                        log.add_rally(
                            self._progression_guide.rally_states[missing_index],
                            "hh:mm:ss.ms"
                        )
                    last_written_rally_index = (
                        self._progression_guide.last_accepted_rally_index
                    )
                    display_state = ScoreboardState(
                        teams=(
                            TeamScore(
                                final.teams[0].name,
                                candidate_previous_state.teams[0].sets,
                                final.teams[0].points
                            ),
                            TeamScore(
                                final.teams[1].name,
                                candidate_previous_state.teams[1].sets,
                                final.teams[1].points
                            )
                        ),
                        serving_team=final.serving_team
                    )
                    change = ScoreChange(candidate_timestamp, display_state)
                    changes.append(change)
                    log.add_rally(change.state, change.timestamp)
                    last_confirmed_timestamp = timestamp
            if self._progression_guide is not None:
                self._progression_guide.accept(candidate)
            if any(team.points != 0 for team in candidate.teams):
                if self._progression_guide is not None:
                    accepted_index = (
                        self._progression_guide.last_accepted_rally_index
                    )
                    for missing_index in range(
                        last_written_rally_index + 1,
                        accepted_index
                    ):
                        log.add_rally(
                            self._progression_guide.rally_states[missing_index],
                            "hh:mm:ss.ms"
                        )
                    last_written_rally_index = accepted_index
                if (
                    self._progression_guide is not None
                    and not candidate_has_transition_evidence
                ):
                    self._progression_guide.mark_current_rally_untimed()
                    log.add_rally(candidate, "hh:mm:ss.ms")
                else:
                    change = ScoreChange(candidate_timestamp, candidate)
                    changes.append(change)
                    log.add_rally(change.state, change.timestamp)
                last_confirmed_timestamp = timestamp
            last_emitted_key = candidate.score_key
            candidate = None
            candidate_count = 0

        if (
            self._progression_guide is not None
            and not isinstance(self._recognizer, ScoreboardFingerprintRecognizer)
        ):
            for state in self._progression_guide.rally_states[
                last_written_rally_index + 1:
            ]:
                log.add_rally(state, "hh:mm:ss.ms")

        if self._timeout_detector is not None:
            for tracker in timeout_trackers:
                event = tracker.finish()
                if event is not None:
                    timeout_events.append(event)
                    log.add_timeout(event)
            if not timeout_visible_samples:
                logger.warning(
                    "Timeout banners could not be inspected in any video frame."
                )
            if any(event.timestamp_seconds is None for event in timeout_events):
                logger.warning(
                    "Some timeout banner boundaries are unverified; see the "
                    "placeholder timestamps in %s.", log_path
                )
            self.timeout_events = tuple(timeout_events)
        return changes


def _required_team_name(team: Any) -> str:
    if not isinstance(team, Mapping):
        raise ValueError("Each team must be an object.")
    name = team.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Each team must have a non-empty name.")
    return name.strip()


def _required_score(team: Mapping[str, Any], key: str) -> int:
    value = team.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"Team {key} must be a non-negative integer.")
    return value


def _score_signature(
    teams: tuple[TeamScore, TeamScore]
) -> tuple[tuple[int, int], ...]:
    return tuple((team.sets, team.points) for team in teams)


def _decode_json_object(text: str) -> Mapping[str, Any]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    value, _ = decoder.raw_decode(text[start:])
    if not isinstance(value, Mapping):
        raise TypeError("The generated JSON must be an object.")
    return value


class _ScoreboardCsvLog:
    fieldnames = (
        "timestamp", "serving_team", "team_a", "team_a_sets", "team_a_points",
        "team_b", "team_b_sets", "team_b_points", "event_type", "timeout_team",
        "start_timestamp", "end_timestamp"
    )

    def __init__(self, path: Path, teams: tuple[str, ...]) -> None:
        self.path = path
        self.teams = teams
        self.rallies: list[dict[str, str]] = []
        self.events: list[TimeoutEvent] = []
        self._write()

    def add_rally(self, state: ScoreboardState, timestamp: str) -> None:
        team_a, team_b = state.teams
        self.rallies.append({
            "timestamp": timestamp,
            "serving_team": state.serving_team or "",
            "team_a": team_a.name,
            "team_a_sets": str(team_a.sets),
            "team_a_points": str(team_a.points),
            "team_b": team_b.name,
            "team_b_sets": str(team_b.sets),
            "team_b_points": str(team_b.points),
            "event_type": "rally",
            "timeout_team": "",
            "start_timestamp": "hh:mm:ss.ms",
            "end_timestamp": timestamp
        })
        self._write()

    def add_timeout(self, event: TimeoutEvent) -> None:
        self.events.append(event)
        self._write()

    def _write(self) -> None:
        _write_merged_csv(
            self.path, self.fieldnames, self.rallies, self.events, self.teams
        )


def _write_merged_csv(
    log_path: Path,
    fieldnames: tuple[str, ...],
    rallies: list[dict[str, str]],
    events: list[TimeoutEvent],
    teams: tuple[str, ...]
) -> None:
    rows = [row.copy() for row in rallies]
    for event in sorted(events, key=lambda item: item.order_seconds):
        event_row = {name: "" for name in fieldnames}
        event_row["timestamp"] = (
            _format_timestamp(event.timestamp_seconds)
            if event.timestamp_seconds is not None else "hh:mm:ss.ms"
        )
        event_row["event_type"] = event.event_type
        event_row["timeout_team"] = teams[event.team_index]
        position = next(
            (
                index for index, row in enumerate(rows)
                if row["timestamp"] != "hh:mm:ss.ms"
                and _parse_timestamp(row["timestamp"]) > event.order_seconds
            ),
            len(rows)
        )
        rows.insert(position, event_row)
    previous_timestamp = "hh:mm:ss.ms"
    uncertain_rally = False
    active_timeouts: set[str] = set()
    for row in rows:
        event_type = row["event_type"]
        if event_type == "timeout_start":
            active_timeouts.add(row["timeout_team"])
            previous_timestamp = "hh:mm:ss.ms"
        elif event_type == "timeout_end":
            active_timeouts.discard(row["timeout_team"])
            if not active_timeouts and not uncertain_rally:
                previous_timestamp = row["timestamp"]
        elif event_type == "rally":
            row["start_timestamp"] = previous_timestamp
            row["end_timestamp"] = row["timestamp"]
            uncertain_rally = row["timestamp"] == "hh:mm:ss.ms"
            if active_timeouts:
                previous_timestamp = "hh:mm:ss.ms"
            else:
                previous_timestamp = row["timestamp"]
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=log_path.parent,
            prefix=f".{log_path.name}.", suffix=".tmp", delete=False
        ) as file:
            temporary_path = file.name
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, log_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            os.unlink(temporary_path)


def _parse_timestamp(timestamp: str) -> float:
    hours, minutes, seconds = timestamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
