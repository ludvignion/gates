---
brief: <n>
status: draft            # draft | approved
approved:                # <name> <date> — filled by the human, not the agent
---

# <n> <title>

## Outcome
I want <X> so that <Y>.

## Acceptance criteria
- AC-1 (behavioral): Given <state>, When <action>, Then <outcome>
- AC-2 (property): For all <input class>, <invariant> holds
- AC-3 (critical): ...

## Out of scope
- ...

## Modules
- `src/<pkg>/<module>/` — <one sentence, no "and">

## Dependencies
| Capability | Provided by | Evidence |
|-----------|-------------|----------|

## Blocking risks
- <capability nodes without evidence; empty means none>

## Assumptions I resolved myself
| # | Assumption | Evidence |
|---|-----------|----------|
| 1 | ... | `path` |

## Decisions the human made
| # | Question | Answer |
|---|----------|--------|

## Verdict must attack
- <failure modes carried over from the brief's "Not this">

## Decisions worth an ADR
- ...

## Slices
1. `<n>.1` — tracer bullet: ...
2. `<n>.2` — ...
