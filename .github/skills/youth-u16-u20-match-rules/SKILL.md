---
name: youth-u16-u20-match-rules
description: "Use when parsing, reconstructing, testing, or analyzing U16, U18, or U20 volleyball matches, including six-player rotations, scoring, deciding third sets, and match length."
---

# U16–U20 Volleyball Match Rules

Use this skill when working on U16, U18, or U20 match parsing, reconstruction, scoring, or rotation behavior in this repository.

## Rules

- U16, U18, and U20 matches are best of three sets: the first team to win two sets wins the match.
- Sets 1 and 2 have a target of 25 points. Set 3, when needed, is the deciding tiebreak and has a target of 15 points.
- Every set must be won by at least two points. Play continues beyond the target while the score difference is less than two.
- These matches use the regular six-player rotation and rally-scoring rules:
  - Every rally awards one point to exactly one team.
  - The serving team stays in its rotation when it wins a rally.
  - When the receiving team wins, it scores, rotates one position, and serves next.
  - The player in position I serves; the ordered rotation list advances by moving its first player to the end.
- A player need not serve during a set. Missing or empty serve-round history for a player is not itself invalid.
- The deciding-set court-side switch at 8 points may split one team's serve records across two score-sheet panels. Merge records by player and validate rotations; the panel split is not an extra rotation or a service change.

## Repository Guidance

- Match length and deciding-set numbering are centralized in [`match_rules.py`](../../../match_rules.py): U16/U18/U20 win at two sets, with set 3 as the deciding set.
- The shared six-player reconstruction is in [`match_reconstructor.py`](../../../match_reconstructor.py), especially `_reconstruct_set`.
- The parser routes the deciding third set through the same side-switch logic used for adult fifth sets. See [`pdf_score_sheet.py`](../../../pdf_score_sheet.py), especially `_parse_deciding_set` and `_parse_side_switch_set`.
- Treat implementation and tests as authoritative if this summary ever differs from current behavior. Keep the two-point margin and allow extended scores.

## When Changing Behavior

1. Verify match type detection and deciding-set numbering independently of score-sheet panel position.
2. Keep the court-side switch separate from on-court serving and rotation events.
3. Check that the split team's pre-switch and post-switch marks are combined by player.
4. Run the U18 PDF regression cases under `tests/u18` and `tests/u18-tiebreak`, as well as deciding-set validation tests.
