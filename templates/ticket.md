---
id: <n>.<m>
parent: <n>
status: ready            # ready | in_progress | in_review | done
depends_on: []
writes: []               # paths the build may touch; enforced by hook if set
---

# <n>.<m> <title>

## Outcome
<one sentence>

## Acceptance criteria
- AC-1 (behavioral): Given / When / Then
- AC-2 (critical): ...
- AC-3 (human): Given <state>, a person confirms <outcome>  # confirmed at Gate 2, no test names it

## Out of scope
- ...

## Findings (append-only)

## Log (append-only)
### [grill] <YYYY-MM-DD HH:MM> — created
