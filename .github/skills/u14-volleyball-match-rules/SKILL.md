---
name: u14-volleyball-match-rules
description: "Use when parsing, reconstructing, testing, or analyzing U14 volleyball matches, including four-player rotations, two-point service turns, set endings, match length, and deciding-set side switches."
---

# U14 Volleyball Match Rules

Use this skill when working on U14 match parsing, reconstruction, scoring, or rotation behavior in this repository. U14 has different rotation and service-turn behavior from adult and U16–U20 matches.

## Rules

- U14 matches are best of three sets: the first team to win two sets wins the match.
- Sets 1 and 2 have a target of 25 points. Set 3, when needed, has a target of 15 points.
- Every set must be won by at least two points. Play continues beyond the target while the score difference is less than two.
- Each team has four players in the ordered rotation. Do not apply a six-player rotation or setter-based Laeufer rules to U14.
- Rally scoring applies: every rally awards one point to exactly one team.
- The set header's `S`/`R` marks identify the opening serving team. The serving team's position-I player serves first.
- When a receiving team wins a rally, it scores, rotates one position, and serves next.
- During a service turn, the serving team may score up to two points before rotating:
  - If it scores two points, it rotates and continues serving.
  - If it scores zero or one point before losing the rally, it does not make this additional rotation.
- A player need not serve during a set. Empty serve-round lists are valid; they do not mean the player must be assigned a serve.
- A deciding-set court-side switch can split one team's service marks across score-sheet panels. Combine the two records by player, without treating the panel switch as an extra rotation or service change.

## Repository Guidance

- U14 set/match targets are shared through [`match_rules.py`](../../../match_rules.py), but U14 also uses its explicit `first_serving_team` mark and U14-specific reconstruction.
- U14 reconstruction is `_reconstruct_u14_set` in [`match_reconstructor.py`](../../../match_reconstructor.py). Its serve-round endpoint delta represents zero, one, or two points on a service turn.
- U14 PDF parsing and side-switch handling are in [`pdf_score_sheet.py`](../../../pdf_score_sheet.py), especially `_parse_u14_deciding_set` and `_parse_side_switch_set`.
- The core rules are also documented in [`README.md`](../../../README.md). Treat implementation and tests as authoritative if this summary ever differs from current behavior.

## When Changing Behavior

1. Keep U14 logic distinct from six-player adult/U16–U20 logic.
2. Test the opening-server marker, sideout rotation, and the zero/one/two-point service-turn cases.
3. Verify both regular U14 sets and the deciding set, including substitution events and split side-switch panels.
4. Run the `tests/u14` and `tests/u14-tiebreak` PDF and reconstruction regression tests.
