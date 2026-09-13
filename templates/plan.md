---
brief: <n>
status: draft            # draft | approved
base: main             # the branch tickets branch from and ship into
approved:                # <name> <date> — filled by the human, not the agent
---

# <n> <title>

## Outcome
I want <X> so that <Y>.

## Acceptance criteria
- AC-1 (behavioral): Given <state>, When <action>, Then <outcome>
- AC-2 (property): For all <input class>, <invariant> holds
- AC-3 (critical): ...
- AC-4 (human): Given <state>, a person confirms <outcome>  # a person confirms at Gate 2

## Out of scope
- ...

## Modules
- `src/<pkg>/<module>/` — <one sentence, no "and">

## Dependencies
| Capability | Provided by | Provider evidence | Access evidence |
|-----------|-------------|-------------------|-----------------|

## Blocking risks
- <capability nodes without evidence; empty means none>

## Assumptions I resolved myself
| # | Assumption | Evidence |
|---|-----------|----------|
| 1 | ... | `path` |

## Technical decisions I made
| # | Decision | Closed by |
|---|----------|-----------|
| 1 | ... | `path` or `AGENTS.md` invariant |

## Decisions the human made
| # | Question | Answer |
|---|----------|--------|

## Gate 1 notes
- <one line each, informational: a runtime dependency, a licence, a paid service, data leaving the repo; "none" when none>

## Verdict must attack
- <failure modes carried over from the brief's "Not this">

## Decisions worth an ADR
- ...

## Slices
1. `<n>.1` — tracer bullet: ...
2. `<n>.2` — ...
