import csv
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from video_scoreboard import (
    HuggingFaceScoreboardRecognizer,
    OpenCvFrameSource,
    ScoreboardCellClassifier,
    ScoreboardCellRecognizer,
    ScoreboardChangeRecognizer,
    ScoreboardDigitClassifier,
    ScoreboardDigitRecognizer,
    ScoreProgressionGuide,
    ScoreboardState,
    TimeoutBannerDetector,
    TimeoutEvent,
    TesseractScoreboardRecognizer,
    TeamScore,
    VideoScoreboardAnalyzer
)
from train_video_scoreboard_classifier import train_classifier


def test_opencv_frame_source_seeks_to_only_sampled_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeCapture:
        def __init__(self) -> None:
            self.frame_index = -1
            self.retrieved: list[int] = []
            self.released = False

        def isOpened(self) -> bool:
            return True

        def get(self, property_id: int) -> float:
            return 2.0 if property_id == 0 else 5.0

        def grab(self) -> bool:
            self.frame_index += 1
            return self.frame_index < 5

        def set(self, _: int, timestamp_ms: float) -> bool:
            self.frame_index = round(timestamp_ms / 500)
            return True

        def read(self) -> tuple[bool, int]:
            self.retrieved.append(self.frame_index)
            return True, self.frame_index

        def release(self) -> None:
            self.released = True

    capture = FakeCapture()
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FPS=0,
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_POS_MSEC=2,
        COLOR_BGR2RGB=1,
        VideoCapture=lambda _: capture,
        cvtColor=lambda frame, _: frame
    )
    fake_pil = SimpleNamespace(Image=SimpleNamespace(fromarray=lambda frame: frame))
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    video_path = tmp_path / "match.mp4"
    video_path.touch()

    frames = list(
        OpenCvFrameSource().iter_frames(
            video_path,
            1.0,
            start_time_seconds=1.0
        )
    )

    assert frames == [(1.0, 2), (2.0, 4)]
    assert capture.retrieved == [2, 4]
    assert capture.released


def _state(
    team_a_sets: int,
    team_a_points: int,
    team_b_sets: int = 0,
    team_b_points: int = 0
) -> ScoreboardState:
    return ScoreboardState(
        teams=(
            TeamScore("TSV Ansbach III", team_a_sets, team_a_points),
            TeamScore("TV Altdorf III", team_b_sets, team_b_points)
        )
    )


class FakeFrameSource:
    def __init__(self, frames: list[tuple[float, str]]) -> None:
        self._frames = frames

    def iter_frames(
        self,
        video_path: Path,
        sample_interval_seconds: float,
        start_time_seconds: float = 0.0
    ) -> list[tuple[float, str]]:
        return self._frames


class FakeRecognizer:
    def __init__(self, states: dict[str, ScoreboardState | None]) -> None:
        self._states = states

    def recognize(self, frame: object) -> ScoreboardState | None:
        return self._states[str(frame)]


class FakeImage:
    width = 100
    height = 80

    def __init__(self) -> None:
        self.crop_box: tuple[int, int, int, int] | None = None

    def crop(self, box: tuple[int, int, int, int]) -> "FakeImage":
        self.crop_box = box
        return self


def test_analyzer_writes_stable_score_changes_with_video_timestamps(
    tmp_path: Path
) -> None:
    zero = _state(0, 0)
    one_zero = _state(0, 1)
    two_zero = _state(0, 2)
    next_set = _state(1, 0, 0, 0)
    frames = [
        (0.0, "zero-a"),
        (0.5, "zero-b"),
        (1.0, "one-a"),
        (1.5, "one-b"),
        (2.0, "one-c"),
        (2.5, "noisy-two"),
        (3.0, "one-d"),
        (3.5, "two-a"),
        (4.0, "two-b"),
        (5.0, "set-a"),
        (5.5, "set-b")
    ]
    recognizer = FakeRecognizer(
        {
            "zero-a": zero,
            "zero-b": zero,
            "one-a": one_zero,
            "one-b": one_zero,
            "one-c": one_zero,
            "noisy-two": two_zero,
            "one-d": one_zero,
            "two-a": two_zero,
            "two-b": two_zero,
            "set-a": next_set,
            "set-b": next_set
        }
    )
    log_path = tmp_path / "nested" / "score_log.csv"
    analyzer = VideoScoreboardAnalyzer(
        recognizer,
        FakeFrameSource(frames),
        stable_observations=2
    )

    changes = analyzer.analyze("match.mp4", log_path)

    assert [
        (change.timestamp, change.state.teams[0].points)
        for change in changes
    ] == [
        ("00:00:01.000", 1),
        ("00:00:03.500", 2)
    ]
    with log_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert set(rows[0]) == {
        "timestamp", "serving_team", "team_a", "team_a_sets", "team_a_points",
        "team_b", "team_b_sets", "team_b_points", "event_type",
        "timeout_team", "start_timestamp", "end_timestamp"
    }
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000",
        "00:00:03.500"
    ]
    assert [
        (row["start_timestamp"], row["end_timestamp"])
        for row in rows
    ] == [
        ("hh:mm:ss.ms", "00:00:01.000"),
        ("00:00:01.000", "00:00:03.500")
    ]


def test_analyzer_flushes_score_changes_while_running(
    tmp_path: Path
) -> None:
    output_file = tmp_path / "progress.csv"
    snapshots: list[list[str]] = []

    class ObservingFrameSource:
        def iter_frames(
            self,
            video_path: Path,
            sample_interval_seconds: float,
            start_time_seconds: float = 0.0
        ) -> Iterable[tuple[float, object]]:
            yield 0.0, "point"
            yield 1.0, "point"
            snapshots.append(
                output_file.read_text(encoding="utf-8").splitlines()
            )

    analyzer = VideoScoreboardAnalyzer(
        FakeRecognizer({"point": _state(0, 1)}),
        ObservingFrameSource(),
        stable_observations=2
    )

    changes = analyzer.analyze("match.mp4", output_file)

    assert len(changes) == 1
    assert snapshots[0][0].startswith("timestamp,serving_team")
    assert len(snapshots[0]) == 2
    assert snapshots[0][1].startswith("00:00:00.000,")


def test_timeout_events_are_ordered_and_do_not_change_rallies(
    tmp_path: Path
) -> None:
    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": 1, "B": 0}},
        {"serving_team": "A", "score_after": {"A": 2, "B": 0}}
    ]}]}

    def score(points: int) -> ScoreboardState:
        return ScoreboardState((TeamScore("A", 0, points), TeamScore("B", 0, 0)))

    frames = [
        (0.0, ("zero", False)), (1.0, ("one", False)),
        (2.0, ("one", False)), (3.0, ("one", True)),
        (4.0, ("one", True)), (5.0, ("one", True)),
        (6.0, ("one", False)), (7.0, ("one", False)),
        (8.0, ("two", False)), (9.0, ("two", False))
    ]

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return frames

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState:
            assert isinstance(frame, tuple)
            return {"zero": score(0), "one": score(1), "two": score(2)}[frame[0]]

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert isinstance(frame, tuple)
            return (False, frame[1])

    guide = ScoreProgressionGuide(point_data)
    path = tmp_path / "events.csv"
    analyzer = VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1, timeout_detector=Detector()
    )
    changes = analyzer.analyze("match.mp4", path)
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["event_type"] for row in rows] == [
        "rally", "timeout_start", "timeout_end", "rally"
    ]
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000", "00:00:03.000",
        "00:00:06.000", "00:00:08.000"
    ]
    assert [row["timeout_team"] for row in rows] == ["", "B", "B", ""]
    assert rows[1]["team_a_points"] == rows[2]["team_a_points"] == ""
    assert rows[0]["start_timestamp"] == "hh:mm:ss.ms"
    assert rows[0]["end_timestamp"] == "00:00:01.000"
    assert rows[1]["start_timestamp"] == rows[2]["end_timestamp"] == ""
    assert rows[3]["start_timestamp"] == "00:00:06.000"
    assert rows[3]["end_timestamp"] == "00:00:08.000"
    assert len(changes) == 2
    assert not guide.unverified_rally_numbers
    assert [event.event_type for event in analyzer.timeout_events] == [
        "timeout_start", "timeout_end"
    ]


def test_partial_log_contains_confirmed_timeouts_and_rally_starts(
    tmp_path: Path
) -> None:
    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": n, "B": 0}}
        for n in range(1, 4)
    ]}]}
    path = tmp_path / "partial.csv"
    snapshots: list[list[dict[str, str]]] = []

    class Source:
        def iter_frames(self, *args: object) -> Iterable[tuple[float, object]]:
            for timestamp, frame in enumerate([
                (0, False), (1, False), (1, False), (1, True), (1, True),
                (1, False), (1, False), (2, False), (2, False),
                (3, False), (3, False)
            ]):
                yield float(timestamp), frame
                if timestamp in (4, 6, 8, 10):
                    with path.open(encoding="utf-8", newline="") as file:
                        snapshots.append(list(csv.DictReader(file)))
            raise RuntimeError("interrupted")

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState:
            assert isinstance(frame, tuple)
            return ScoreboardState(
                (TeamScore("A", 0, frame[0]), TeamScore("B", 0, 0))
            )

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert isinstance(frame, tuple)
            return (False, frame[1])

    analyzer = VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), sample_interval_seconds=1,
        progression_guide=ScoreProgressionGuide(point_data),
        timeout_detector=Detector()
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        analyzer.analyze("match.mp4", path)
    assert [r["event_type"] for r in snapshots[0]] == ["rally", "timeout_start"]
    assert [r["event_type"] for r in snapshots[1]] == [
        "rally", "timeout_start", "timeout_end"
    ]
    assert snapshots[1][-1]["timestamp"] == "00:00:05.000"
    assert [r["event_type"] for r in snapshots[2]] == [
        "rally", "timeout_start", "timeout_end", "rally"
    ]
    assert snapshots[2][-1]["start_timestamp"] == "00:00:05.000"
    assert snapshots[3][-1]["start_timestamp"] == "00:00:07.000"
    with path.open(encoding="utf-8", newline="") as file:
        assert list(csv.DictReader(file)) == snapshots[3]


def test_timeout_ending_after_rally_keeps_rally_start_unknown(
    tmp_path: Path
) -> None:
    path = tmp_path / "late-timeout.csv"
    snapshots: list[list[dict[str, str]]] = []

    class Source:
        def iter_frames(self, *args: object) -> Iterable[tuple[float, object]]:
            for timestamp, frame in enumerate([
                (0, False), (1, False), (1, False), (1, True), (1, True),
                (2, True), (2, False), (2, False)
            ]):
                yield float(timestamp), frame
                if timestamp in (6, 7):
                    with path.open(encoding="utf-8", newline="") as file:
                        snapshots.append(list(csv.DictReader(file)))

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState:
            assert isinstance(frame, tuple)
            return ScoreboardState(
                (TeamScore("A", 0, frame[0]), TeamScore("B", 0, 0))
            )

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert isinstance(frame, tuple)
            return (False, frame[1])

    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": n, "B": 0}}
        for n in range(1, 3)
    ]}]}
    VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), sample_interval_seconds=1,
        progression_guide=ScoreProgressionGuide(point_data),
        timeout_detector=Detector()
    ).analyze("match.mp4", path)
    assert snapshots[0][-1]["start_timestamp"] == "hh:mm:ss.ms"
    assert snapshots[1][-2]["event_type"] == "rally"
    assert snapshots[1][-2]["start_timestamp"] == "hh:mm:ss.ms"


def test_timeout_unreadable_boundaries_are_placeholders(tmp_path: Path) -> None:
    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [(0, True), (1, True), (2, None), (3, False), (4, False),
                    (5, True), (6, True)]

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert frame is None or isinstance(frame, bool)
            return (frame, False)

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return None

    path = tmp_path / "uncertain-timeouts.csv"
    VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), sample_interval_seconds=1,
        timeout_detector=Detector()
    ).analyze("match.mp4", path)
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["event_type"] for row in rows] == [
        "timeout_start", "timeout_end", "timeout_start", "timeout_end"
    ]
    assert [row["timestamp"] for row in rows] == [
        "hh:mm:ss.ms", "hh:mm:ss.ms", "00:00:05.000", "hh:mm:ss.ms"
    ]


@pytest.mark.parametrize("scenario", ["missing_rally", "missing_timeout_end"])
def test_rally_start_remains_unknown_after_uncertain_boundary(
    tmp_path: Path, scenario: str
) -> None:
    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": n, "B": 0}}
        for n in range(1, 4)
    ]}]}

    def score(points: int) -> ScoreboardState:
        return ScoreboardState((TeamScore("A", 0, points), TeamScore("B", 0, 0)))

    observations: list[tuple[float, tuple[int, bool | None]]] = [
        (0, (0, False)), (1, (1, False)), (2, (1, False)),
        (3, (1, True)), (4, (1, True)),
        (5, (1, None if scenario == "missing_timeout_end" else False)),
        (6, (1, False)), (7, (1, False)),
        (8, (3 if scenario == "missing_rally" else 2, False)),
        (9, (3 if scenario == "missing_rally" else 2, False))
    ]

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return observations

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState:
            assert isinstance(frame, tuple)
            return score(frame[0])

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert isinstance(frame, tuple)
            return (False, frame[1])

    path = tmp_path / "uncertain-start.csv"
    VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), sample_interval_seconds=1,
        progression_guide=ScoreProgressionGuide(point_data),
        timeout_detector=Detector()
    ).analyze("match.mp4", path)
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    observed_rally = next(
        row for row in rows
        if row["event_type"] == "rally" and row["timestamp"] == "00:00:08.000"
    )
    assert observed_rally["start_timestamp"] == "hh:mm:ss.ms"


def test_timeout_tracker_ignores_flicker_and_tracks_each_team(tmp_path: Path) -> None:
    observations = [
        (False, False), (False, False), (True, False),
        (False, False), (False, False), (True, False),
        (True, True), (True, True), (False, True),
        (False, True), (False, False), (False, False)
    ]

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [(float(i), value) for i, value in enumerate(observations)]

    class Detector:
        def detect(self, frame: object) -> tuple[bool | None, bool | None]:
            assert isinstance(frame, tuple)
            return frame

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return None

    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": 1, "B": 0}}
    ]}]}
    path = tmp_path / "overlapping-timeouts.csv"
    VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), sample_interval_seconds=1,
        progression_guide=ScoreProgressionGuide(point_data),
        timeout_detector=Detector()
    ).analyze("match.mp4", path)
    with path.open(encoding="utf-8", newline="") as file:
        rows = [row for row in csv.DictReader(file) if row["event_type"] != "rally"]
    assert [
        (row["event_type"], row["timeout_team"], row["timestamp"])
        for row in rows
    ] == [
        ("timeout_start", "A", "00:00:05.000"),
        ("timeout_start", "B", "00:00:06.000"),
        ("timeout_end", "A", "00:00:08.000"),
        ("timeout_end", "B", "00:00:10.000")
    ]


def test_timeout_banner_detects_sample_video_boundaries() -> None:
    cv2 = pytest.importorskip("cv2")
    image_module = pytest.importorskip("PIL.Image")
    video_path = (
        Path(__file__).parents[1] / "input/D3_Ansbach_Altdorf"
        / "TSV Ansbach3_ TV Altdorf3_17.10.2026.mp4"
    )
    if not video_path.is_file():
        pytest.skip("Optional match video is unavailable")
    capture = cv2.VideoCapture(str(video_path))
    template = (
        Path(__file__).parents[1] / "training_data/scoreboard/bvv"
        / "timeout_banner_template.png"
    )
    if not template.is_file():
        pytest.skip("Optional timeout banner template is unavailable")
    detector = TimeoutBannerDetector(template)
    try:
        for timestamp, expected in (
            (757, (False, False)), (760, (False, True)),
            (787, (False, True)), (788, (False, False)),
            (1827, (False, False)), (1830, (False, True)),
            (1857, (False, True)), (1860, (False, False)),
            (1030, (True, False)), (3739, (False, False))
        ):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            assert detector.detect(image) == expected
            if timestamp in (760, 1030, 3739):
                smaller = cv2.resize(frame, None, fx=0.5, fy=0.5)
                smaller_image = image_module.fromarray(
                    cv2.cvtColor(smaller, cv2.COLOR_BGR2RGB)
                )
                assert detector.detect(smaller_image) == expected
    finally:
        capture.release()


def test_timeout_template_must_be_a_readable_image(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    with pytest.raises(FileNotFoundError, match="Timeout banner template"):
        TimeoutBannerDetector(tmp_path / "missing.png")
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="Invalid timeout banner"):
        TimeoutBannerDetector(invalid)


def test_config_accepts_timeout_template(tmp_path: Path) -> None:
    config_path = tmp_path / "match.yaml"
    config_path.write_text(json.dumps({
        "pdf_file": "match.pdf",
        "teams": [{"name": "A"}, {"name": "B"}],
        "video_scoreboard": {
            "video_file": "match.mp4",
            "timeout_template_file": "timeout.png"
        }
    }), encoding="utf-8")
    assert (
        main._load_config(config_path)["video_scoreboard"]["timeout_template_file"]
        == "timeout.png"
    )


def test_scoreboard_format_selects_assets_and_supports_overrides(
    tmp_path: Path
) -> None:
    config_path = tmp_path / "match.yaml"
    config = {
        "pdf_file": "match.pdf",
        "teams": [{"name": "A"}, {"name": "B"}],
        "video_scoreboard": {
            "video_file": "match.mp4", "backend": "cell_classifier"
        }
    }

    def load() -> dict:
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return main._load_config(config_path)["video_scoreboard"]

    base = Path(main.__file__).parent / "training_data/scoreboard"
    result = load()
    assert result["scoreboard_format"] == "bvv"
    assert result["classifier_file"] == str(base / "bvv/scoreboard_cell_templates.json")
    assert result["timeout_template_file"] == str(base / "bvv/timeout_banner_template.png")
    config["video_scoreboard"]["scoreboard_format"] = "other"
    result = load()
    assert result["classifier_file"] == str(base / "other/scoreboard_cell_templates.json")
    assert result["timeout_template_file"] == str(base / "other/timeout_banner_template.png")
    config["video_scoreboard"]["classifier_file"] = "custom-score.json"
    config["video_scoreboard"]["timeout_template_file"] = "custom-timeout.png"
    result = load()
    assert result["classifier_file"] == "custom-score.json"
    assert result["timeout_template_file"] == "custom-timeout.png"
    config["video_scoreboard"]["scoreboard_format"] = "../other"
    with pytest.raises(ValueError, match="scoreboard_format"):
        load()


def test_progression_guide_filters_and_canonicalizes_scores() -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 0}
                        },
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 1}
                        },
                        {
                            "serving_team": "Team B",
                            "score_after": {"Team A": 2, "Team B": 1}
                        }
                    ]
                },
                {
                    "points": [
                        {
                            "serving_team": "Team B",
                            "score_after": {"Team A": 0, "Team B": 1}
                        }
                    ]
                }
            ]
        }
    )

    assert guide.match(_state(0, 9, 0, 9)) is None
    first_score = guide.match(
        ScoreboardState(
            teams=(
                TeamScore("Overlay bottom", 0, 0),
                TeamScore("Overlay top", 0, 1)
            )
        )
    )
    assert first_score == ScoreboardState(
        teams=(
            TeamScore("Team A", 0, 1),
            TeamScore("Team B", 0, 0)
        ),
        serving_team="Team A"
    )
    guide.accept(first_score)

    tie = guide.match(
        ScoreboardState(
            teams=(
                TeamScore("Overlay bottom", 0, 1),
                TeamScore("Overlay top", 0, 1)
            )
        )
    )
    assert tie is not None
    assert tie.teams == (
        TeamScore("Team A", 0, 1),
        TeamScore("Team B", 0, 1)
    )
    guide.accept(tie)
    assert guide.match(
        ScoreboardState(
            teams=(
                TeamScore("Overlay bottom", 0, 0),
                TeamScore("Overlay top", 0, 1)
            )
        )
    ) is None

    next_score = guide.match(
        ScoreboardState(
            teams=(
                TeamScore("Overlay bottom", 0, 1),
                TeamScore("Overlay top", 0, 2)
            )
        )
    )
    assert next_score is not None
    assert next_score.teams == (
        TeamScore("Team A", 1, 2),
        TeamScore("Team B", 0, 1)
    )


def test_tesseract_recognizer_reads_score_cells_and_reuses_static_frame() -> None:
    image_module = pytest.importorskip("PIL.Image")
    frame = image_module.new("RGB", (1920, 1080), "white")
    readings = iter(["1", "10", "0", "4"])
    calls = []

    def fake_ocr(cell: object) -> str:
        calls.append(cell)
        return next(readings)

    recognizer = TesseractScoreboardRecognizer(ocr=fake_ocr)

    first_state = recognizer.recognize(frame)
    same_state = recognizer.recognize(frame)

    assert first_state == ScoreboardState(
        teams=(
            TeamScore("top", 1, 10),
            TeamScore("bottom", 0, 4)
        )
    )
    assert same_state == first_state
    assert len(calls) == 4


def test_tesseract_recognizer_ocr_only_reads_changed_score_cells() -> None:
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    frame = image_module.new("RGB", (1920, 1080), "white")
    readings = iter(["0", "0", "0", "0", "1"])
    calls = []

    def fake_ocr(cell: object) -> str:
        calls.append(cell)
        return next(readings)

    recognizer = TesseractScoreboardRecognizer(ocr=fake_ocr)
    original_state = recognizer.recognize(frame)
    draw_module.Draw(frame).rectangle((220, 86, 240, 110), fill="black")
    updated_state = recognizer.recognize(frame)

    assert original_state is not None
    assert updated_state is not None
    assert updated_state.teams[0].sets == 1
    assert updated_state.teams[0].points == 0
    assert len(calls) == 5


def test_tesseract_recognizer_skips_unreadable_digits() -> None:
    image_module = pytest.importorskip("PIL.Image")
    frame = image_module.new("RGB", (1920, 1080), "white")
    readings = iter(["1", "", "0", "4"])
    recognizer = TesseractScoreboardRecognizer(
        ocr=lambda cell: next(readings)
    )

    assert recognizer.recognize(frame) is None


def test_guided_analysis_recovers_after_a_score_was_not_visible(
    tmp_path: Path
) -> None:
    point_data = {
        "sets": [
            {
                "points": [
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 1, "Team B": 0}
                    },
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 2, "Team B": 0}
                    },
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 3, "Team B": 0}
                    }
                ]
            }
        ]
    }
    first_score = ScoreboardState(
        teams=(
            TeamScore("Team A", 0, 1),
            TeamScore("Team B", 0, 0)
        )
    )
    third_score = ScoreboardState(
        teams=(
            TeamScore("Team A", 0, 3),
            TeamScore("Team B", 0, 0)
        )
    )

    class FrameSource:
        def iter_frames(
            self,
            video_path: Path,
            sample_interval_seconds: float,
            start_time_seconds: float = 0.0
        ):
            yield 1.0, first_score
            yield 2.0, first_score
            yield 5.0, None
            yield 10.0, third_score
            yield 20.0, third_score

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return frame if isinstance(frame, ScoreboardState) else None

    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        Recognizer(),
        frame_source=FrameSource(),
        sample_interval_seconds=1,
        stable_observations=2,
        progression_guide=guide
    )
    changes = analyzer.analyze("match.mp4", tmp_path / "recovered.csv")

    assert [
        tuple(team.points for team in change.state.teams)
        for change in changes
    ] == [(1, 0), (3, 0)]
    assert [change.timestamp_seconds for change in changes] == [1, 10]
    assert guide.skipped_rally_numbers == (2,)
    with (tmp_path / "recovered.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000",
        "hh:mm:ss.ms",
        "00:00:10.000"
    ]
    assert [row["start_timestamp"] for row in rows] == [
        "hh:mm:ss.ms", "00:00:01.000", "hh:mm:ss.ms"
    ]
    assert [row["end_timestamp"] for row in rows] == [
        "00:00:01.000", "hh:mm:ss.ms", "00:00:10.000"
    ]
    assert rows[1]["team_a_points"] == "2"


def test_guided_analysis_writes_placeholders_for_unobserved_tail(
    tmp_path: Path
) -> None:
    point_data = {
        "sets": [{
            "points": [
                {"serving_team": "A", "score_after": {"A": 1, "B": 0}},
                {"serving_team": "A", "score_after": {"A": 2, "B": 0}},
                {"serving_team": "A", "score_after": {"A": 3, "B": 0}}
            ]
        }]
    }
    state = ScoreboardState((TeamScore("A", 0, 1), TeamScore("B", 0, 0)))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [(1.0, state), (2.0, state)]

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return frame if isinstance(frame, ScoreboardState) else None

    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), progression_guide=guide
    )
    analyzer.analyze("match.mp4", tmp_path / "tail.csv")
    with (tmp_path / "tail.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["team_a_points"] for row in rows] == ["1", "2", "3"]
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000", "hh:mm:ss.ms", "hh:mm:ss.ms"
    ]
    assert guide.unverified_rally_numbers == (2, 3)


def test_guided_analysis_resynchronizes_after_long_gap(
    tmp_path: Path
) -> None:
    point_data = {
        "sets": [{
            "points": [
                {"serving_team": "A", "score_after": {"A": n, "B": 0}}
                for n in range(1, 5)
            ]
        }]
    }
    first = ScoreboardState((TeamScore("A", 0, 1), TeamScore("B", 0, 0)))
    last = ScoreboardState((TeamScore("A", 0, 4), TeamScore("B", 0, 0)))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [(1.0, first), (2.0, first), (75.0, last), (76.0, last)]

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return frame if isinstance(frame, ScoreboardState) else None

    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), progression_guide=guide
    )
    changes = analyzer.analyze("match.mp4", tmp_path / "gap.csv")
    assert [change.timestamp_seconds for change in changes] == [1, 75]
    with (tmp_path / "gap.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["team_a_points"] for row in rows] == ["1", "2", "3", "4"]
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000", "hh:mm:ss.ms", "hh:mm:ss.ms", "00:01:15.000"
    ]
    assert guide.unverified_rally_numbers == (2, 3)


def test_missing_set_point_is_preserved_when_score_resets(
    tmp_path: Path
) -> None:
    point_data = {
        "sets": [
            {"points": [
                {"serving_team": "A", "score_after": {"A": 1, "B": 0}},
                {"serving_team": "A", "score_after": {"A": 2, "B": 0}}
            ]},
            {"points": [
                {"serving_team": "A", "score_after": {"A": 1, "B": 0}},
                {"serving_team": "A", "score_after": {"A": 2, "B": 0}}
            ]}
        ]
    }

    def score(sets: int, points: int) -> ScoreboardState:
        return ScoreboardState((TeamScore("A", sets, points), TeamScore("B", 0, 0)))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [
                (1, score(0, 1)), (2, score(0, 1)),
                (3, score(1, 0)), (4, score(1, 0)),
                (5, score(1, 1)), (6, score(1, 1))
            ]

    class Recognizer:
        def recognize(self, frame: object) -> ScoreboardState | None:
            return frame if isinstance(frame, ScoreboardState) else None

    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        Recognizer(), frame_source=Source(), progression_guide=guide
    )
    analyzer.analyze("match.mp4", tmp_path / "set-reset.csv")
    with (tmp_path / "set-reset.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000", "hh:mm:ss.ms", "00:00:05.000", "hh:mm:ss.ms"
    ]
    assert guide.unverified_rally_numbers == (2, 4)


def test_cell_classifier_round_trips_and_rejects_ambiguous_cells(
    tmp_path: Path
) -> None:
    zero = bytes(128)
    one = bytes([255]) * 128
    classifier = ScoreboardCellClassifier(
        {"sets": {0: [zero], 1: [one]}, "points": {0: [zero], 1: [one]}},
        maximum_distance=1024,
        minimum_margin=1
    )
    path = tmp_path / "cells.json"
    classifier.save(path)
    restored = ScoreboardCellClassifier.load(path)
    assert restored.classify_cell(zero, "points") == 0
    assert restored.classify_cell(one, "sets") == 1
    assert restored.classify_cell(bytes([255]) * 64 + bytes(64), "points") is None


def test_cell_crop_ignores_transient_detection_shift(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    image_module = pytest.importorskip("PIL.Image")
    frame = image_module.new("RGB", (640, 360), "white")
    original = (0, 20, 315, 114)
    shifted = (5, 20, 308, 114)
    regions = iter([original, shifted, shifted, original, shifted, shifted, shifted])
    reader = ScoreboardChangeRecognizer()
    monkeypatch.setattr(reader, "_detect_region", lambda frame: next(regions))
    for expected in [original, original, original, original, original, original, shifted]:
        reader.cell_templates(frame)
        assert reader._detected_region == expected


@pytest.mark.parametrize(
    ("fallbacks", "expected"),
    [
        ([(1, 2, 0, 3)], (1, 2, 0, 3)),
        ([(1, 2, 0, 3), (1, 2, 0, 4)], None),
        ([(1, 5, 0, 3)], None),
        ([(1, 2, None, 3)], None)
    ]
)
def test_cell_recognizer_uses_consistent_nearby_crop(
    monkeypatch: pytest.MonkeyPatch,
    fallbacks: list[tuple[int | None, ...]],
    expected: tuple[int, ...] | None
) -> None:
    blank = bytes(128)
    classifier = ScoreboardCellClassifier(
        {"sets": {0: [blank]}, "points": {0: [blank]}}
    )
    recognizer = ScoreboardCellRecognizer(classifier)

    def cells(values: tuple[int | None, ...]) -> tuple[bytes, ...]:
        return tuple(bytes([255 if value is None else value]) for value in values)

    monkeypatch.setattr(
        recognizer._cell_reader, "cell_templates",
        lambda frame: cells((1, 2, 0, None))
    )
    monkeypatch.setattr(
        recognizer._cell_reader, "nearby_cell_templates",
        lambda frame: tuple(cells(values) for values in fallbacks)
    )
    monkeypatch.setattr(
        classifier, "classify_cell",
        lambda cell, kind: None if cell[0] == 255 else cell[0]
    )
    state = recognizer.recognize(object())
    actual = (
        tuple(value for team in state.teams for value in (team.sets, team.points))
        if state is not None else None
    )
    assert actual == expected


def test_cell_recognizer_requires_observed_transition_for_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    point_data = {
        "sets": [{"points": [
            {"serving_team": "A", "score_after": {"A": n, "B": 0}}
            for n in range(1, 4)
        ]}]
    }

    def score(points: int) -> ScoreboardState:
        return ScoreboardState((TeamScore("A", 0, points), TeamScore("B", 0, 0)))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [
                (1, score(1)), (2, score(1)),
                (5, score(2)), (6, score(2)),
                (7, score(3)), (8, score(3))
            ]

    zero = bytes(128)
    classifier = ScoreboardCellClassifier(
        {"sets": {0: [zero]}, "points": {0: [zero]}}
    )
    recognizer = ScoreboardCellRecognizer(classifier)
    monkeypatch.setattr(
        recognizer, "recognize",
        lambda frame: frame if isinstance(frame, ScoreboardState) else None
    )
    guide = ScoreProgressionGuide(point_data)
    analyzer = VideoScoreboardAnalyzer(
        recognizer, frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1
    )
    changes = analyzer.analyze("match.mp4", tmp_path / "uncertain.csv")
    assert [change.timestamp_seconds for change in changes] == [7]
    with (tmp_path / "uncertain.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["timestamp"] for row in rows] == [
        "hh:mm:ss.ms", "hh:mm:ss.ms", "00:00:07.000"
    ]
    assert guide.unverified_rally_numbers == (1, 2)


@pytest.mark.parametrize("interruption", ["reversal", "unreadable"])
def test_cell_candidate_does_not_survive_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": 1, "B": 0}}
    ]}]}
    zero = ScoreboardState((TeamScore("A", 0, 0), TeamScore("B", 0, 0)))
    one = ScoreboardState((TeamScore("A", 0, 1), TeamScore("B", 0, 0)))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [
                (0, zero), (1, one),
                (2, zero if interruption == "reversal" else None),
                (3, one), (4, one)
            ]

    blank = bytes(128)
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier(
        {"sets": {0: [blank]}, "points": {0: [blank]}}
    ))
    monkeypatch.setattr(
        recognizer, "recognize",
        lambda frame: frame if isinstance(frame, ScoreboardState) else None
    )
    guide = ScoreProgressionGuide(point_data)
    changes = VideoScoreboardAnalyzer(
        recognizer, frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1
    ).analyze("match.mp4", tmp_path / "interrupted.csv")

    assert [change.timestamp_seconds for change in changes] == (
        [3] if interruption == "reversal" else []
    )
    assert guide.unverified_rally_numbers == (
        () if interruption == "reversal" else (1,)
    )


def test_cell_recognizer_respects_match_team_row_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_data = {"sets": [{"points": [
        {"serving_team": "A", "score_after": {"A": 1, "B": 0}}
    ]}]}

    def score(top: int, bottom: int) -> ScoreboardState:
        return ScoreboardState((
            TeamScore("top", 0, top), TeamScore("bottom", 0, bottom)
        ))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [
                (0, score(0, 0)), (1, score(0, 1)), (2, score(0, 1)),
                (3, score(0, 0)), (4, score(1, 0)), (5, score(1, 0))
            ]

    blank = bytes(128)
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier(
        {"sets": {0: [blank]}, "points": {0: [blank]}}
    ))
    monkeypatch.setattr(recognizer, "recognize", lambda frame: frame)
    guide = ScoreProgressionGuide(point_data)
    changes = VideoScoreboardAnalyzer(
        recognizer, frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1
    ).analyze("match.mp4", tmp_path / "row-order.csv")
    assert [change.timestamp_seconds for change in changes] == [4]
    assert not guide.unverified_rally_numbers


def test_cell_recognizer_uses_set_reset_as_final_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_data = {"sets": [
        {"points": [
            {"serving_team": "A", "score_after": {"A": n, "B": 0}}
            for n in (1, 2)
        ]}
        for _ in range(2)
    ]}

    def score(sets: int, points: int) -> ScoreboardState:
        return ScoreboardState((
            TeamScore("top", sets, points), TeamScore("bottom", 0, 0)
        ))

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return [
                (0, score(0, 0)), (1, score(0, 1)), (2, score(0, 1)),
                (3, score(1, 0)), (4, score(1, 0)),
                (5, score(1, 1)), (6, score(1, 1))
            ]

    blank = bytes(128)
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier(
        {"sets": {0: [blank]}, "points": {0: [blank]}}
    ))
    monkeypatch.setattr(recognizer, "recognize", lambda frame: frame)
    guide = ScoreProgressionGuide(point_data)
    VideoScoreboardAnalyzer(
        recognizer, frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1
    ).analyze("match.mp4", tmp_path / "cell-set-reset.csv")
    with (tmp_path / "cell-set-reset.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        rows = list(csv.DictReader(file))
    assert [row["timestamp"] for row in rows] == [
        "00:00:01.000", "00:00:03.000",
        "00:00:05.000", "hh:mm:ss.ms"
    ]
    assert rows[1]["team_a_sets"] == "0"
    assert rows[1]["team_a_points"] == "2"
    assert guide.unverified_rally_numbers == (4,)


@pytest.mark.parametrize("scenario", ["missing_predecessor", "unreadable_gap", "shown_final"])
def test_cell_set_reset_requires_unambiguous_preceding_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    point_data = {"sets": [
        {"points": [
            {"serving_team": "A", "score_after": {"A": n, "B": 0}}
            for n in (1, 2, 3)
        ]},
        {"points": [{"serving_team": "A", "score_after": {"A": 1, "B": 0}}]}
    ]}

    def score(sets: int, points: int) -> ScoreboardState:
        return ScoreboardState((
            TeamScore("top", sets, points), TeamScore("bottom", 0, 0)
        ))

    frames: list[tuple[float, object]] = [
        (0, score(0, 0)), (1, score(0, 1)), (2, score(0, 1))
    ]
    if scenario != "missing_predecessor":
        frames += [(3, score(0, 2)), (4, score(0, 2))]
    if scenario == "unreadable_gap":
        frames += [(5, None)]
    elif scenario == "shown_final":
        frames += [(5, score(0, 3)), (6, score(0, 3))]
    reset_time = 3 if scenario == "missing_predecessor" else 7
    frames += [(reset_time, score(1, 0)), (reset_time + 1, score(1, 0))]

    class Source:
        def iter_frames(self, *args: object) -> list[tuple[float, object]]:
            return frames

    blank = bytes(128)
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier(
        {"sets": {0: [blank]}, "points": {0: [blank]}}
    ))
    monkeypatch.setattr(recognizer, "recognize", lambda frame: frame)
    guide = ScoreProgressionGuide(point_data)
    changes = VideoScoreboardAnalyzer(
        recognizer, frame_source=Source(), progression_guide=guide,
        sample_interval_seconds=1
    ).analyze("match.mp4", tmp_path / "set-reset-edge.csv")
    assert [change.timestamp_seconds for change in changes] == {
        "missing_predecessor": [1],
        "unreadable_gap": [1, 3],
        "shown_final": [1, 3, 5]
    }[scenario]
    assert guide.unverified_rally_numbers == {
        "missing_predecessor": (2, 3, 4),
        "unreadable_gap": (3, 4),
        "shown_final": (4,)
    }[scenario]


def test_whole_cell_classifier_handles_timeout_and_following_scores() -> None:
    cv2 = pytest.importorskip("cv2")
    image_module = pytest.importorskip("PIL.Image")
    match_dir = Path(__file__).parents[1] / "input/D3_Ansbach_Altdorf"
    video_path = match_dir / "TSV Ansbach3_ TV Altdorf3_17.10.2026.mp4"
    model_path = Path(__file__).parents[1] / "training_data/scoreboard/bvv/scoreboard_cell_templates.json"
    if not video_path.is_file() or not model_path.is_file():
        pytest.skip("Optional match video or cell templates are unavailable")
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier.load(model_path))
    capture = cv2.VideoCapture(str(video_path))
    try:
        for timestamp, expected in (
            (422, (0, 0, 0, 0)),
            (425, (0, 0, 0, 1)),
            (426, (0, 0, 0, 1)),
            (452, (0, 1, 0, 1)),
            (519, (0, 2, 0, 2)),
            (521, (0, 2, 0, 2)),
            (830, (0, 7, 0, 5)),
            (1137, (0, 9, 0, 12)),
            (1812, (0, 19, 0, 22)),
            (1813, (0, 19, 0, 22)),
            (1840, (0, 19, 0, 22)),
            (1885, (0, 20, 0, 22)),
            (1915, (0, 21, 0, 22)),
            (1955, (0, 21, 0, 23)),
            (2020, (0, 23, 0, 23))
        ):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if timestamp == 1840:
                assert ScoreboardChangeRecognizer._detect_region(image)
            state = recognizer.recognize(image)
            assert state is not None, f"Unreadable frame at {timestamp}s"
            assert tuple(
                score for team in state.teams for score in (team.sets, team.points)
            ) == expected

        fresh_recognizer = ScoreboardCellRecognizer(
            ScoreboardCellClassifier.load(model_path)
        )
        for timestamp, expected in (
            (1700, (0, 16, 0, 21)),
            (1840, (0, 19, 0, 22)),
            (1885, (0, 20, 0, 22))
        ):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            state = fresh_recognizer.recognize(image)
            assert state is not None, f"Unreadable frame at {timestamp}s"
            assert tuple(
                score for team in state.teams for score in (team.sets, team.points)
            ) == expected
    finally:
        capture.release()


def test_whole_cell_classifier_reads_sample_gap_transitions() -> None:
    cv2 = pytest.importorskip("cv2")
    image_module = pytest.importorskip("PIL.Image")
    match_dir = Path(__file__).parents[1] / "input/D3_Ansbach_Altdorf"
    video_path = match_dir / "TSV Ansbach3_ TV Altdorf3_17.10.2026.mp4"
    model_path = Path(__file__).parents[1] / "training_data/scoreboard/bvv/scoreboard_cell_templates.json"
    if not video_path.is_file() or not model_path.is_file():
        pytest.skip("Optional match video or cell templates are unavailable")
    capture = cv2.VideoCapture(str(video_path))
    recognizer = ScoreboardCellRecognizer(ScoreboardCellClassifier.load(model_path))
    recognizer._cell_reader._detected_region = (0, 75, 315, 169)
    try:
        for timestamp, expected in (
            (2573.141, (1, 2, 0, 2)),
            (2574.142, (1, 2, 0, 3)),
            (2619.187, (1, 3, 0, 3)),
            (2620.188, (1, 3, 0, 4)),
            (3743.304, (1, 24, 0, 12)),
            (3744.305, (2, 0, 0, 0)),
            (4309.867, (2, 3, 0, 5)),
            (4310.868, (2, 3, 0, 6)),
            (5562.112, (2, 24, 0, 19)),
            (5563.113, (3, 25, 0, 19))
        ):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            state = recognizer.recognize(image)
            assert state is not None, f"Unreadable frame at {timestamp}s"
            assert tuple(
                value for team in state.teams for value in (team.sets, team.points)
            ) == expected
    finally:
        capture.release()


def test_set_counter_change_is_one_canonical_rally_score() -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 0}
                        },
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 2, "Team B": 0}
                        }
                    ]
                }
            ]
        }
    )

    before_set_counter_updates = guide.match(_state(0, 2))
    after_set_counter_updates = guide.match(_state(1, 2))

    assert before_set_counter_updates is not None
    assert before_set_counter_updates == after_set_counter_updates
    assert before_set_counter_updates.teams[0].sets == 1


def test_guided_analyzer_logs_only_authoritative_rally_scores(
    tmp_path: Path
) -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 0}
                        }
                    ]
                },
                {
                    "points": [
                        {
                            "serving_team": "Team B",
                            "score_after": {"Team A": 0, "Team B": 1}
                        }
                    ]
                }
            ]
        }
    )
    observed_state = ScoreboardState(
        teams=(
            TeamScore("Overlay A", 0, 1),
            TeamScore("Overlay B", 0, 0)
        )
    )
    reset_state = ScoreboardState(
        teams=(
            TeamScore("Overlay A", 1, 0),
            TeamScore("Overlay B", 0, 0)
        )
    )
    second_set_state = ScoreboardState(
        teams=(
            TeamScore("Overlay A", 1, 0),
            TeamScore("Overlay B", 0, 1)
        )
    )
    frames = [
        (0.0, "initial"),
        (0.5, "initial"),
        (1.0, "point"),
        (1.5, "point"),
        (2.0, "reset"),
        (2.5, "reset"),
        (3.0, "second-set-point"),
        (3.5, "second-set-point")
    ]
    recognizer = FakeRecognizer(
        {
            "initial": _state(0, 0),
            "point": observed_state,
            "reset": reset_state,
            "second-set-point": second_set_state
        }
    )
    output_file = tmp_path / "guided.csv"
    analyzer = VideoScoreboardAnalyzer(
        recognizer,
        FakeFrameSource(frames),
        stable_observations=2,
        progression_guide=guide
    )

    changes = analyzer.analyze("match.mp4", output_file)

    assert [change.timestamp for change in changes] == [
        "00:00:01.000",
        "00:00:03.000"
    ]
    assert changes[0].state.teams == (
        TeamScore("Team A", 1, 1),
        TeamScore("Team B", 0, 0)
    )
    assert changes[1].state.teams == (
        TeamScore("Team A", 1, 0),
        TeamScore("Team B", 1, 1)
    )


def test_guided_analyzer_ignores_temporary_wrong_score_correction(
    tmp_path: Path
) -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Ansbach",
                            "score_after": {"Altdorf": 0, "Ansbach": 1}
                        },
                        {
                            "serving_team": "Ansbach",
                            "score_after": {"Altdorf": 1, "Ansbach": 1}
                        },
                        {
                            "serving_team": "Altdorf",
                            "score_after": {"Altdorf": 1, "Ansbach": 2}
                        },
                        {
                            "serving_team": "Ansbach",
                            "score_after": {"Altdorf": 2, "Ansbach": 2}
                        },
                        {
                            "serving_team": "Altdorf",
                            "score_after": {"Altdorf": 2, "Ansbach": 3}
                        },
                        {
                            "serving_team": "Ansbach",
                            "score_after": {"Altdorf": 2, "Ansbach": 4}
                        },
                        {
                            "serving_team": "Altdorf",
                            "score_after": {"Altdorf": 3, "Ansbach": 4}
                        }
                    ]
                }
            ]
        }
    )
    frames = [
        (0.0, "zero-a"), (0.5, "zero-b"),
        (1.0, "ansbach-1-a"), (1.5, "ansbach-1-b"),
        (2.0, "tie-a"), (2.5, "tie-b"),
        (3.0, "wrong-2-1-a"), (3.5, "wrong-2-1-b"),
        (4.0, "wrong-2-1-c"),
        (5.0, "corrected-tie-a"), (5.5, "corrected-tie-b"),
        (6.0, "corrected-tie-c"),
        (7.0, "ansbach-2-a"), (7.5, "ansbach-2-b"),
        (8.0, "tie-2-a"), (8.5, "tie-2-b"),
        (9.0, "ansbach-3-a"), (9.5, "ansbach-3-b"),
        (10.0, "ansbach-4-a"), (10.5, "ansbach-4-b")
    ]
    recognizer = FakeRecognizer(
        {
            "zero-a": _state(0, 0),
            "zero-b": _state(0, 0),
            "ansbach-1-a": _state(0, 1),
            "ansbach-1-b": _state(0, 1),
            "tie-a": _state(0, 1, 0, 1),
            "tie-b": _state(0, 1, 0, 1),
            "wrong-2-1-a": _state(0, 1, 0, 2),
            "wrong-2-1-b": _state(0, 1, 0, 2),
            "wrong-2-1-c": _state(0, 1, 0, 2),
            "corrected-tie-a": _state(0, 1, 0, 1),
            "corrected-tie-b": _state(0, 1, 0, 1),
            "corrected-tie-c": _state(0, 1, 0, 1),
            "ansbach-2-a": _state(0, 2, 0, 1),
            "ansbach-2-b": _state(0, 2, 0, 1),
            "tie-2-a": _state(0, 2, 0, 2),
            "tie-2-b": _state(0, 2, 0, 2),
            "ansbach-3-a": _state(0, 3, 0, 2),
            "ansbach-3-b": _state(0, 3, 0, 2),
            "ansbach-4-a": _state(0, 4, 0, 2),
            "ansbach-4-b": _state(0, 4, 0, 2)
        }
    )
    output_file = tmp_path / "corrected-score.csv"
    analyzer = VideoScoreboardAnalyzer(
        recognizer,
        FakeFrameSource(frames),
        stable_observations=2,
        progression_guide=guide
    )

    changes = analyzer.analyze("match.mp4", output_file)

    assert [change.state.score_key for change in changes] == [
        (("Altdorf", 0, 0), ("Ansbach", 0, 1)),
        (("Altdorf", 0, 1), ("Ansbach", 0, 1)),
        (("Altdorf", 0, 1), ("Ansbach", 0, 2)),
        (("Altdorf", 0, 2), ("Ansbach", 0, 2)),
        (("Altdorf", 0, 2), ("Ansbach", 0, 3)),
        (("Altdorf", 0, 2), ("Ansbach", 0, 4))
    ]
    assert [change.timestamp for change in changes] == [
        "00:00:01.000",
        "00:00:02.000",
        "00:00:07.000",
        "00:00:08.000",
        "00:00:09.000",
        "00:00:10.000"
    ]


def test_image_change_analyzer_skips_between_set_score_reset(
    tmp_path: Path
) -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 0}
                        }
                    ]
                },
                {
                    "points": [
                        {
                            "serving_team": "Team B",
                            "score_after": {"Team A": 0, "Team B": 1}
                        }
                    ]
                }
            ]
        }
    )
    blank = bytes(24 * 24)
    changed = bytes([255]) * (24 * 24)
    fingerprints = {
        "initial-a": (blank, blank, blank, blank),
        "initial-b": (blank, blank, blank, blank),
        "first-point-a": (blank, changed, blank, blank),
        "first-point-b": (blank, changed, blank, blank),
        "reset-a": (changed, blank, blank, blank),
        "reset-b": (changed, blank, blank, blank),
        "second-set-point-a": (changed, blank, blank, changed),
        "second-set-point-b": (changed, blank, blank, changed)
    }

    class FingerprintRecognizer:
        def cell_fingerprints(
            self,
            frame: object
        ) -> tuple[bytes, ...]:
            return fingerprints[str(frame)]

        def changed_cells(
            self,
            previous: tuple[bytes, ...],
            current: tuple[bytes, ...]
        ) -> set[int]:
            return ScoreboardChangeRecognizer().changed_cells(previous, current)

    frames = [
        (0.0, "initial-a"), (0.5, "initial-b"),
        (1.0, "first-point-a"), (1.5, "first-point-b"),
        (2.0, "reset-a"), (2.5, "reset-b"),
        (3.0, "second-set-point-a"), (3.5, "second-set-point-b")
    ]
    analyzer = VideoScoreboardAnalyzer(
        FingerprintRecognizer(),
        FakeFrameSource(frames),
        stable_observations=2,
        progression_guide=guide
    )

    changes = analyzer.analyze("match.mp4", tmp_path / "sets.csv")

    assert [change.timestamp for change in changes] == [
        "00:00:01.000",
        "00:00:03.000"
    ]
    assert [change.state.score_key for change in changes] == [
        (("Team A", 1, 1), ("Team B", 0, 0)),
        (("Team A", 1, 0), ("Team B", 1, 1))
    ]


def test_image_change_detection_rejects_short_noise_but_accepts_small_changes(
    tmp_path: Path
) -> None:
    guide = ScoreProgressionGuide(
        {
            "sets": [
                {
                    "points": [
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 1, "Team B": 0}
                        },
                        {
                            "serving_team": "Team A",
                            "score_after": {"Team A": 2, "Team B": 0}
                        }
                    ]
                }
            ]
        }
    )
    blank = bytes(24 * 24)
    small_change = bytes([255]) * 12 + bytes(24 * 24 - 12)
    fingerprints = {
        "baseline-a": (blank, blank, blank, blank),
        "baseline-b": (blank, blank, blank, blank),
        "noise-a": (blank, small_change, blank, blank),
        "noise-b": (blank, small_change, blank, blank),
        "noise-ended": (blank, blank, blank, blank),
        "point-a": (blank, small_change, blank, blank),
        "point-b": (blank, small_change, blank, blank),
        "point-c": (blank, small_change, blank, blank),
        "point-d": (blank, small_change, blank, blank)
    }

    class FingerprintRecognizer:
        def cell_fingerprints(
            self,
            frame: object
        ) -> tuple[bytes, ...]:
            return fingerprints[str(frame)]

        def changed_cells(
            self,
            previous: tuple[bytes, ...],
            current: tuple[bytes, ...]
        ) -> set[int]:
            return ScoreboardChangeRecognizer().changed_cells(previous, current)

    frames = [
        (0.0, "baseline-a"),
        (1.0, "baseline-b"),
        (2.0, "noise-a"),
        (3.0, "noise-b"),
        (4.0, "noise-ended"),
        (5.0, "point-a"),
        (6.0, "point-b"),
        (7.0, "point-c"),
        (8.0, "point-d")
    ]
    analyzer = VideoScoreboardAnalyzer(
        FingerprintRecognizer(),
        FakeFrameSource(frames),
        stable_observations=4,
        progression_guide=guide
    )

    changes = analyzer.analyze("match.mp4", tmp_path / "stable.csv")

    assert [change.timestamp_seconds for change in changes] == [5.0]
    assert changes[0].state.score_key == (
        ("Team A", 0, 1),
        ("Team B", 0, 0)
    )


@pytest.mark.parametrize("scale", [0.7, 1.5])
def test_scoreboard_auto_detection_handles_scaled_overlays(
    scale: float
) -> None:
    cv2 = pytest.importorskip("cv2")
    image_module = pytest.importorskip("PIL.Image")
    video_path = (
        Path(__file__).parent
        / "fixtures"
        / "video_scoreboard"
        / "opening_points_6m50-10m20.mp4"
    )
    capture = cv2.VideoCapture(str(video_path))
    frames = []
    try:
        for timestamp in (12, 13):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            board = image_module.fromarray(
                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            ).resize((round(308 * scale), round(96 * scale)))
            full_frame = image_module.new("RGB", (1280, 720), (80, 80, 80))
            full_frame.paste(board, (30, 60))
            frames.append(full_frame)
    finally:
        capture.release()

    recognizer = ScoreboardChangeRecognizer()
    before = recognizer.cell_fingerprints(frames[0])
    after = recognizer.cell_fingerprints(frames[1])

    assert recognizer.changed_cells(before, after) == {3}


def test_opening_video_snippet_extracts_first_six_rallies(
    tmp_path: Path
) -> None:
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")

    video_path = (
        Path(__file__).parent
        / "fixtures"
        / "video_scoreboard"
        / "opening_points_6m50-10m20.mp4"
    )
    point_data = {"sets": [{"points": [
        {"score_after": {"TV Altdorf III": a, "TSV Ansbach III - a.K.": b}}
        for a, b in [(0, 1), (1, 1), (1, 2), (2, 2), (2, 3), (2, 4), (3, 4)]
    ]}]}
    selected_seconds = {
        12, 13, 14, 15, 16,
        41, 42, 43, 44, 45,
        78, 79, 80, 81, 82, 83,
        90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100,
        107, 108, 109, 110, 111,
        166, 167, 168, 169, 170, 171, 172,
        203, 204, 205, 206, 207, 208, 209
    }

    class SelectedVideoFrames:
        def iter_frames(
            self,
            path: Path,
            sample_interval_seconds: float,
            start_time_seconds: float = 0.0
        ):
            for timestamp, frame in OpenCvFrameSource().iter_frames(
                path,
                sample_interval_seconds,
                start_time_seconds
            ):
                if round(timestamp) in selected_seconds:
                    yield timestamp, frame

    analyzer = VideoScoreboardAnalyzer(
        ScoreboardChangeRecognizer(scoreboard_region=(0, 0, 1, 1)),
        frame_source=SelectedVideoFrames(),
        sample_interval_seconds=1,
        stable_observations=4,
        progression_guide=ScoreProgressionGuide(point_data)
    )

    changes = analyzer.analyze(video_path, tmp_path / "opening.csv")

    assert [change.state.score_key for change in changes] == [
        (("TV Altdorf III", 0, 0), ("TSV Ansbach III - a.K.", 0, 1)),
        (("TV Altdorf III", 0, 1), ("TSV Ansbach III - a.K.", 0, 1)),
        (("TV Altdorf III", 0, 1), ("TSV Ansbach III - a.K.", 0, 2)),
        (("TV Altdorf III", 0, 2), ("TSV Ansbach III - a.K.", 0, 2)),
        (("TV Altdorf III", 0, 2), ("TSV Ansbach III - a.K.", 0, 3)),
        (("TV Altdorf III", 0, 2), ("TSV Ansbach III - a.K.", 0, 4))
    ]
    assert [
        round(change.timestamp_seconds)
        for change in changes
    ] == pytest.approx([13, 42, 97, 108, 169, 204], abs=4)


def test_main_config_can_enable_guided_video_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "match.yaml"
    config_path.write_text(
        json.dumps(
            {
                "pdf_file": "match.pdf",
                "teams": [{"name": "Team A"}, {"name": "Team B"}],
                "video_scoreboard": {
                    "backend": "digit_classifier",
                    "video_file": "match.mp4",
                    "classifier_file": "digit-templates.json",
                    "start_time_seconds": 422
                }
            }
        ),
        encoding="utf-8"
    )
    config = main._load_config(config_path)
    assert config["video_scoreboard"]["scoreboard_format"] == "bvv"
    assert config["video_scoreboard"]["backend"] == "digit_classifier"
    assert config["video_scoreboard"]["scoreboard_region"] is None
    assert config["video_scoreboard"]["crop_region"] == (
        0,
        0,
        0.6,
        0.35
    )
    assert config["video_scoreboard"]["start_time_seconds"] == 422
    assert config["video_scoreboard"]["stable_observations"] == 2

    match_points = {
        "sets": [
            {
                "points": [
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 1, "Team B": 0}
                    },
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 2, "Team B": 0}
                    }
                ]
            }
        ]
    }
    captured: dict[str, object] = {}
    changes = [object(), object()]

    class FakeAnalyzer:
        def __init__(self, recognizer: object, **kwargs: object) -> None:
            captured["recognizer"] = recognizer
            captured.update(kwargs)
            self.timeout_events = (
                TimeoutEvent("timeout_start", 0, 5, 5),
                TimeoutEvent("timeout_end", 0, None, 7)
            )

        def analyze(self, video_file: str, output_file: str) -> list[object]:
            captured["video_file"] = video_file
            captured["output_file"] = output_file
            guide = captured["progression_guide"]
            assert isinstance(guide, ScoreProgressionGuide)
            for state in guide.rally_states:
                guide.accept(state)
            return changes

    monkeypatch.setattr(
        ScoreboardDigitClassifier,
        "load",
        lambda path: ("loaded classifier", path)
    )
    monkeypatch.setattr(
        main,
        "ScoreboardDigitRecognizer",
        lambda *args, **kwargs: ("digit_classifier", args, kwargs)
    )
    monkeypatch.setattr(main, "VideoScoreboardAnalyzer", FakeAnalyzer)
    monkeypatch.setattr(
        main, "TimeoutBannerDetector",
        lambda path, region: ("timeout detector", path, region)
    )

    assert main._run_video_scoreboard(
        config["video_scoreboard"],
        match_points
    )

    assert captured["recognizer"] == (
        "digit_classifier",
        (("loaded classifier", "digit-templates.json"),),
        {"scoreboard_region": None}
    )
    assert captured["video_file"] == "match.mp4"
    assert captured["output_file"] == "analysis/score_progression.csv"
    assert captured["start_time_seconds"] == 422
    assert isinstance(captured["progression_guide"], ScoreProgressionGuide)
    output = capsys.readouterr().out
    assert (
        "SUCCESS - matched and timestamped all 2 expected rallies"
    ) in output
    assert captured["timeout_detector"] == (
        "timeout detector",
        str(Path(main.__file__).parent / "training_data/scoreboard/bvv/timeout_banner_template.png"),
        None
    )
    assert "Timeout banners: 1 detected; 1 boundaries need review" in output
    config["video_scoreboard"]["timeout_template_file"] = "timeout.png"
    assert main._run_video_scoreboard(config["video_scoreboard"], match_points)
    assert captured["timeout_detector"] == ("timeout detector", "timeout.png", None)
    output = capsys.readouterr().out
    assert "Timeout banners: 1 detected; 1 boundaries need review" in output

    class UntimedAnalyzer(FakeAnalyzer):
        def analyze(self, video_file: str, output_file: str) -> list[object]:
            result = super().analyze(video_file, output_file)
            guide = captured["progression_guide"]
            assert isinstance(guide, ScoreProgressionGuide)
            guide.mark_current_rally_untimed()
            return result

    monkeypatch.setattr(main, "VideoScoreboardAnalyzer", UntimedAnalyzer)
    assert not main._run_video_scoreboard(config["video_scoreboard"], match_points)
    output = capsys.readouterr().out
    assert "FAILURE" in output
    assert "score or timestamp for rally number(s): 2" in output


def test_digit_classifier_trains_and_classifies_labeled_glyphs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch
) -> None:
    def glyph_template(digit: int) -> bytes:
        pixels = bytearray(24 * 20)
        start = digit * 24
        pixels[start:start + 24] = bytes([255]) * 24
        return bytes(pixels)

    glyphs = {digit: glyph_template(digit) for digit in range(10)}
    samples = [
        (
            (
                (glyphs[0],),
                (glyphs[digit],),
                (glyphs[0],),
                (glyphs[digit],)
            ),
            (0, digit, 0, digit)
        )
        for digit in range(10)
    ]
    classifier = ScoreboardDigitClassifier.train(
        samples,
        maximum_distance=0,
        minimum_margin=1
    )
    model_path = tmp_path / "digits.json"
    classifier.save(model_path)
    loaded_classifier = ScoreboardDigitClassifier.load(model_path)

    assert loaded_classifier.template_count == 40
    assert loaded_classifier.classify_cell(
        (glyphs[1], glyphs[0])
    ) == 10
    assert loaded_classifier.classify_cell(()) is None
    one_pixel_difference = bytearray(glyphs[1])
    one_pixel_difference[0] = 255
    assert loaded_classifier.classify_digit(
        bytes(one_pixel_difference)
    ) is None

    recognizer = ScoreboardDigitRecognizer(loaded_classifier)
    masks = (
        (glyphs[0],),
        (glyphs[1],),
        (glyphs[0],),
        (glyphs[2],)
    )
    monkeypatch.setattr(
        recognizer._cell_reader,
        "cell_digit_masks",
        lambda frame: masks
    )
    state = recognizer.recognize("frame")

    assert state == ScoreboardState(
        teams=(
            TeamScore("top", 0, 1),
            TeamScore("bottom", 0, 2)
        )
    )


def test_video_digit_classifier_reads_all_labeled_training_samples(
    tmp_path: Path
) -> None:
    cv2 = pytest.importorskip("cv2")
    image_module = pytest.importorskip("PIL.Image")
    repository = Path(__file__).parents[1]
    match_dir = repository / "input/D3_Ansbach_Altdorf"
    video_path = (
        match_dir
        / "TSV Ansbach3_ TV Altdorf3_17.10.2026.mp4"
    )
    labels_path = match_dir / "scoreboard_training_samples.csv"
    if not video_path.is_file() or not labels_path.is_file():
        pytest.skip("Optional match video or digit training samples are unavailable")
    model_path = tmp_path / "digit-templates.json"
    classifier = train_classifier(video_path, labels_path, model_path)
    recognizer = ScoreboardDigitRecognizer(
        ScoreboardDigitClassifier.load(model_path)
    )
    with labels_path.open(encoding="utf-8", newline="") as file:
        labels = list(csv.DictReader(file))
    capture = cv2.VideoCapture(str(video_path))
    try:
        for row in labels:
            timestamp = float(row["timestamp_seconds"])
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(
                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            )
            state = recognizer.recognize(image)
            assert state is not None, f"Unreadable sample at {timestamp}s"
            assert [
                (team.sets, team.points)
                for team in state.teams
            ] == [
                (int(row["top_sets"]), int(row["top_points"])),
                (int(row["bottom_sets"]), int(row["bottom_points"]))
            ]

        adaptive_recognizer = ScoreboardDigitRecognizer(
            ScoreboardDigitClassifier.load(model_path)
        )
        observed_states = []
        for timestamp in (422, 1499.073):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = capture.read()
            assert success
            image = image_module.fromarray(
                cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            )
            observed_states.append(adaptive_recognizer.recognize(image))
        assert observed_states[0] == ScoreboardState(
            teams=(
                TeamScore("top", 0, 0),
                TeamScore("bottom", 0, 0)
            )
        )
        assert observed_states[1] == ScoreboardState(
            teams=(
                TeamScore("top", 0, 15),
                TeamScore("bottom", 0, 16)
            )
        )
    finally:
        capture.release()


def test_incomplete_video_scoreboard_analysis_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    point_data = {
        "sets": [
            {
                "points": [
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 1, "Team B": 0}
                    },
                    {
                        "serving_team": "Team A",
                        "score_after": {"Team A": 2, "Team B": 0}
                    }
                ]
            }
        ]
    }

    class FakeAnalyzer:
        def __init__(self, recognizer: object, **kwargs: object) -> None:
            self.timeout_events: tuple[TimeoutEvent, ...] = ()

        def analyze(self, video_file: str, output_file: str) -> list[object]:
            return [object()]

    monkeypatch.setattr(
        main,
        "ScoreboardChangeRecognizer",
        lambda **kwargs: ("image_changes", kwargs)
    )
    monkeypatch.setattr(main, "VideoScoreboardAnalyzer", FakeAnalyzer)
    monkeypatch.setattr(main, "TimeoutBannerDetector", lambda *args: object())
    video_config = {
        "backend": "image_changes",
        "video_file": "match.mp4",
        "output_file": str(tmp_path / "partial.csv"),
        "scoreboard_region": None,
        "timeout_template_file": "timeout.png",
        "sample_interval_seconds": 1.0,
        "stable_observations": 4,
        "start_time_seconds": 422
    }

    assert not main._run_video_scoreboard(video_config, point_data)
    assert (
        "FAILURE - image-change detection observed 1 candidate transitions"
        in capsys.readouterr().out
    )


def test_model_recognizer_reads_json_from_generated_text() -> None:
    payload = {
        "teams": [
            {"name": "TV Altdorf III", "sets": 1, "points": 10},
            {"name": "TSV Ansbach III", "sets": 0, "points": 4}
        ],
        "serving_team": "TV Altdorf III"
    }
    recognizer = HuggingFaceScoreboardRecognizer(
        "local-model",
        pipeline=lambda frame: [
            {"generated_text": f"Scoreboard: {json.dumps(payload)}"}
        ],
        crop_region=(0, 0, 0.5, 0.5)
    )
    frame = FakeImage()

    state = recognizer.recognize(frame)

    assert state == ScoreboardState.from_mapping(payload)
    assert frame.crop_box == (0, 0, 50, 40)


def test_model_recognizer_skips_invalid_model_output(caplog) -> None:
    recognizer = HuggingFaceScoreboardRecognizer(
        "local-model",
        pipeline=lambda frame: [{"generated_text": "not a scoreboard"}]
    )

    assert recognizer.recognize(FakeImage()) is None
    assert "Ignoring unreadable scoreboard model output" in caplog.text


def test_model_recognizer_requires_a_local_model_directory(
    tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match="Local scoreboard model"):
        HuggingFaceScoreboardRecognizer(tmp_path / "missing-model")


@pytest.mark.parametrize(
    "payload",
    [
        {"teams": [{"name": "Only one team", "sets": 0, "points": 0}]},
        {
            "teams": [
                {"name": "A", "sets": -1, "points": 0},
                {"name": "B", "sets": 0, "points": 0}
            ]
        },
        {
            "teams": [
                {"name": "A", "sets": 0, "points": 0},
                {"name": "B", "sets": 0, "points": 0}
            ],
            "serving_team": "C"
        }
    ]
)
def test_scoreboard_state_rejects_invalid_data(
    payload: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        ScoreboardState.from_mapping(payload)


@pytest.mark.parametrize(
    "timestamps",
    [(-0.5, 0.0), (1.0, 1.0), (1.0, 0.5)]
)
def test_analyzer_rejects_invalid_timestamps(
    timestamps: tuple[float, float],
    tmp_path: Path
) -> None:
    analyzer = VideoScoreboardAnalyzer(
        FakeRecognizer({"a": _state(0, 1), "b": _state(0, 1)}),
        FakeFrameSource(
            [(timestamps[0], "a"), (timestamps[1], "b")]
        ),
        stable_observations=1
    )

    with pytest.raises(ValueError, match="timestamps"):
        analyzer.analyze("match.mp4", tmp_path / "scores.csv")
