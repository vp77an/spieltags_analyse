"""Train an OCR-free scoreboard digit classifier from labeled video frames."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Literal, overload

from video_scoreboard import (
    ScoreboardCellClassifier,
    ScoreboardChangeRecognizer,
    ScoreboardDigitClassifier
)


_LABEL_COLUMNS = (
    "timestamp_seconds",
    "top_sets",
    "top_points",
    "bottom_sets",
    "bottom_points"
)


@overload
def train_classifier(
    video_path: str | Path,
    labels_path: str | Path,
    model_path: str | Path,
    scoreboard_region: tuple[float, float, float, float] | None = None,
    *,
    whole_cells: Literal[False] = False
) -> ScoreboardDigitClassifier: ...


@overload
def train_classifier(
    video_path: str | Path,
    labels_path: str | Path,
    model_path: str | Path,
    scoreboard_region: tuple[float, float, float, float] | None = None,
    *,
    whole_cells: Literal[True]
) -> ScoreboardCellClassifier: ...


@overload
def train_classifier(
    video_path: str | Path,
    labels_path: str | Path,
    model_path: str | Path,
    scoreboard_region: tuple[float, float, float, float] | None = None,
    *,
    whole_cells: bool
) -> ScoreboardDigitClassifier | ScoreboardCellClassifier: ...


def train_classifier(
    video_path: str | Path,
    labels_path: str | Path,
    model_path: str | Path,
    scoreboard_region: tuple[float, float, float, float] | None = None,
    *,
    whole_cells: bool = False
) -> ScoreboardDigitClassifier | ScoreboardCellClassifier:
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Classifier training requires the optional video dependencies. "
            "Install them with `python -m pip install "
            "-r requirements-video.txt`."
        ) from error

    video_path = Path(video_path)
    labels_path = Path(labels_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not labels_path.is_file():
        raise FileNotFoundError(f"Training labels not found: {labels_path}")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise ValueError(f"Could not open video: {video_path}")

    recognizer = ScoreboardChangeRecognizer(scoreboard_region)
    cell_samples: list[
        tuple[tuple[bytes, ...], tuple[int, int, int, int]]
    ] = []
    digit_samples: list[
        tuple[tuple[tuple[bytes, ...], ...], tuple[int, int, int, int]]
    ] = []
    previous_timestamp = -1.0
    try:
        with labels_path.open(encoding="utf-8", newline="") as label_file:
            reader = csv.DictReader(label_file)
            if reader.fieldnames is None or any(
                column not in reader.fieldnames
                for column in _LABEL_COLUMNS
            ):
                raise ValueError(
                    "Training labels must have these columns: "
                    + ", ".join(_LABEL_COLUMNS)
                )

            for row_number, row in enumerate(reader, start=2):
                timestamp = float(row["timestamp_seconds"])
                if (
                    not math.isfinite(timestamp)
                    or timestamp < 0
                    or timestamp <= previous_timestamp
                ):
                    raise ValueError(
                        f"Training timestamps must be finite, non-negative, "
                        f"and increasing (line {row_number})."
                    )
                previous_timestamp = timestamp
                parsed_scores = [
                    _parse_score(row[column], column, row_number)
                    for column in _LABEL_COLUMNS[1:]
                ]
                scores = (
                    parsed_scores[0], parsed_scores[1],
                    parsed_scores[2], parsed_scores[3]
                )

                if not capture.set(
                    cv2.CAP_PROP_POS_MSEC,
                    timestamp * 1000
                ):
                    raise ValueError(
                        f"Could not seek to {timestamp:.3f}s in {video_path}."
                    )
                success, frame = capture.read()
                if not success:
                    raise ValueError(
                        f"Could not read the training frame at "
                        f"{timestamp:.3f}s from {video_path}."
                    )
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(np.ascontiguousarray(rgb_frame))
                if whole_cells:
                    cell_samples.extend(
                        (cells, scores)
                        for cells in recognizer.cell_template_variants(image)
                    )
                else:
                    digit_samples.append((recognizer.cell_digit_masks(image), scores))
    finally:
        capture.release()

    if whole_cells:
        classifier: ScoreboardDigitClassifier | ScoreboardCellClassifier = (
            ScoreboardCellClassifier.train(cell_samples)
        )
    else:
        classifier = ScoreboardDigitClassifier.train(digit_samples)
    classifier.save(model_path)
    return classifier


def _parse_score(value: str, column: str, row_number: int) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{column} must be a non-negative integer (line {row_number})."
        ) from error
    if score < 0:
        raise ValueError(
            f"{column} must be a non-negative integer (line {row_number})."
        )
    return score


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Train an OCR-free digit template classifier from labeled "
            "scoreboard frames."
        )
    )
    parser.add_argument("--video", required=True, help="Video containing the scoreboard")
    parser.add_argument("--labels", required=True, help="CSV file with labeled timestamps")
    parser.add_argument("--model", required=True, help="Output classifier JSON file")
    parser.add_argument(
        "--whole-cells",
        action="store_true",
        help="Train whole-cell image templates instead of digit templates"
    )
    parser.add_argument(
        "--scoreboard-region",
        nargs=4,
        type=float,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Optional normalized scoreboard bounds"
    )
    arguments = parser.parse_args()
    model = train_classifier(
        arguments.video,
        arguments.labels,
        arguments.model,
        tuple(arguments.scoreboard_region)
        if arguments.scoreboard_region is not None
        else None,
        whole_cells=arguments.whole_cells
    )
    print(
        f"Saved OCR-free scoreboard classifier to {arguments.model}."
    )
