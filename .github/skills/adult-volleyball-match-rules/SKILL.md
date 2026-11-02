---
name: adult-volleyball-match-rules
description: "Use when parsing, reconstructing, testing, or analyzing adult volleyball matches, including rally scoring, six-player rotations, set endings, match length, and fifth-set tiebreaks."
---

# Adult Volleyball Match Rules

Use this skill when working on adult match parsing, reconstruction, scoring, or rotation behavior in this repository.

## Rules

- An adult match is best of five sets: the first team to win three sets wins the match.
- Sets 1–4 have a target of 25 points. Set 5 is the deciding tiebreak and has a target of 15 points.
- Every set, including the deciding set, must be won by at least two points. Play continues beyond the target while the score difference is less than two.
- Rally scoring applies: every rally awards one point to exactly one team.
- The team serving remains in its rotation when it wins a rally. When the receiving team wins a rally, it scores, rotates one position, and serves the next rally.
- The player in position I serves. The six-player rotation order is represented by the ordered rotation list; rotating advances its first player to the end.
- A player need not serve during a set. Missing or empty serve-round history for a player is not itself invalid.
- On the fifth-set score sheet, the court-side switch at 8 points can split one team's records between two panels. Merge that team's serve marks by player and validate the matching rotations; the panel split is not an extra rotation or a service change.

## Repository Guidance

- Match and deciding-set selection are centralized in [`match_rules.py`](../../../match_rules.py): an adult match wins at three set victories, and the deciding set is set 5.
- The shared six-player reconstruction is in [`match_reconstructor.py`](../../../match_reconstructor.py), especially `_reconstruct_set`.
- PDF panel parsing and side-switch merging are in [`pdf_score_sheet.py`](../../../pdf_score_sheet.py), especially `_parse_deciding_set` and `_parse_side_switch_set`.
- Treat implementation and tests as authoritative if this summary ever differs from current behavior. Preserve the two-point margin and allow extended scores; do not assume a set ends exactly at 25 or 15.

## When Changing Behavior

1. Determine whether the change concerns a regular set or the fifth-set tiebreak.
2. Keep the score-sheet panel layout separate from the on-court rotation: a court-side switch does not imply a rotation.
3. Check both parsed serve marks and reconstructed point-by-point serving/rotation state.
4. Run the relevant PDF fixture and reconstruction tests, including `tests/5-set`.
