from model import SetData
from rotation import RotationTracker

import pandas as pd


def analyze_rotations(set_data):

    tracker = RotationTracker(
        set_data.start_rotation
    )

    stats = {}

    for r in range(1, 7):

        stats[r] = {
            "own": 0,
            "opp": 0
        }

    current_rotation = tracker.setter_position()

    own_last = 0
    opp_last = 0

    for server in sorted(
        set_data.own_serve_rounds.keys()
    ):

        scores = set_data.own_serve_rounds[server]

        if len(scores) == 0:
            continue

        own_score = max(scores)

        own_delta = own_score - own_last

        stats[current_rotation]["own"] += own_delta

        own_last = own_score

        tracker.rotate()

        current_rotation = tracker.setter_position()

    return stats


def print_analysis(stats):

    rows = []

    for rot in sorted(stats):

        own = stats[rot]["own"]
        opp = stats[rot]["opp"]

        rows.append(
            {
                "Rotation": rot,
                "Eigene Punkte": own,
                "Gegnerpunkte": opp,
                "Diff": own - opp
            }
        )

    df = pd.DataFrame(rows)

    print(df)
