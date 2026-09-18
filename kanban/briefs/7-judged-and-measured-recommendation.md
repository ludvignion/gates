# Brief: The Gate 2 recommendation is judged, and measured

## What I want

Every finding the Gate 2 block shows me is weighed by the seat before I see it, including the
mechanical ones. A mechanical `block` stays verbatim and unwaivable — that is the rail. A
mechanical `warn` becomes the seat's to judge: it gets a verb, a consequence line, or it gets
folded into a group. Nothing reaches option A that no one has read.

The recap sentence comes from the seat that judged the diff, not from a second, cheaper call
that never saw it.

And the recommendation is measured, not trusted: `verdict_eval.py` grows metrics for the
recommendation itself, and a golden packet exists so a prompt change has something to regress
against.

## Why it matters

Plan 6 built the rail for the seat's own findings. It left the other two paths into the block
unjudged, and ticket 6.1's own verdict shows what that costs.

6.1 came back with 14 findings. Ten of them — C1 to C10 — were one fact, nested one to four
levels deep, the longest 623 characters. The payload at the bottom of every one of them is the
same single finding from 5.1: `AC-4 test asserts only two substrings; tree, questions, plan
page, Gate 1 unpinned. tests/test_grill_skill.py:126`. Option A recommended a child ticket for
each. Eleven tickets from one finding, which is the plan-5 failure brief 6 was written about,
arriving again through a different door.

Three specific causes:

- `scripts/lint_kanban.py:148` writes its own message as a finding *title*. On the next run,
  `:132` parses that message back out as a finding and re-emits it wrapped one level deeper.
  Routing it makes it bigger. That is the nesting.
- `scripts/verdict_checks.py:108` pipes whole-repo `lint_kanban.lint()` into a per-ticket
  verdict as warns. Not scoped to the diff, unbounded, and it was 6.6 KB of 6.1's packet and 10
  of its 14 findings.
- `scripts/verdict_prep.py:278` titles that packet section `Mechanical findings (copy verbatim
  into findings)`. The seat is instructed not to weigh them. That is why C1 to C10 carry an
  empty `impact` and no `consequence` while the seat's own F1 to F4 carry both.

The recap has the same shape of problem. `render_verdict.py:131` hands Haiku the close-out
entry, the diff stat and the verdict JSON — never the diff. For 6.1 it was given a close-out
naming every file and every AC it satisfied, and returned "scripts enhance output to type
action/why verbatim". That is $0.049 a ticket to lose information. Meanwhile the `review` field
from the same call, which correctly collapsed the ten into one clause, never reaches the result
block at all: `render_verdict.py:452` keeps only the first sentence of `built`, and `review` is
rendered on the HTML page only (`:542`).

## What I already know / have decided

- Brief 6's split holds and extends: the seat proposes, code validates. Mechanical warns join
  the things the seat may judge. Mechanical blocks never do — a block can never come back as a
  waive, and that is not negotiable here either.
- The nesting is a defect at source, not something to filter downstream. A lint message must
  never be re-parsable as a finding. Fix it in `lint_kanban.py`; do not teach the verdict to
  recognise its own output.
- Repo-wide lint does not belong in a per-ticket verdict. Either scope it to the diff or drop it
  from the packet entirely. If it is a real signal, it belongs in `make lint` and CI, where it
  is reported once, not once per ticket and growing.
- The recap is the judging seat's job. One seat, one pass, with the diff in hand.
- `scripts/verdict_eval.py` already replays archived packets and already runs in CI over
  `tests/fixtures/project`. Its metrics today are `block_count`, `finding_count`,
  `citation_compliance`, `decision_agreement`, `wall_seconds` — counts and a decision match,
  nothing about the recommendation. The metrics I want added:
  - every open finding carries both a verb and a consequence reason (coverage)
  - the stance verb equals `decision` (this must be 100%; disagreement means one of them is wrong)
  - the rail overrule rate — how often the seat proposes something code refuses. Brief 6 asked
    for this rate by name and nothing reports it yet
  - duplicate-collapse rate: findings folded over findings emitted
  - the recap names files or ACs the diff actually contains
- `traces/golden/` has never existed (`traces/harness-findings.md`, 2026-09-15 11:31). The
  prompt sha is part of the verdict seam metadata, so a prompt change today has nothing to
  regress against.
- This depends on plan 6 being shipped: 6.2's stance, 6.3's duplicate collapse and 6.4's prompt
  rewrite all land in the same seam. Do not start it mid-walk — changing what the packet hands
  the seat would mean 6.2 and 6.3 are judged under one contract and re-judged under another.
- The files this names: `scripts/lint_kanban.py`, `scripts/verdict_checks.py`,
  `scripts/verdict_prep.py`, `scripts/render_verdict.py`, `scripts/verdict_eval.py`,
  `skills/verdict/verdict-prompt.md`. Lane by `triage.py` as usual; I have not pre-checked it.

## What I'm unsure about

- Whether the recap becomes a field the seat fills in `verdict.json`, or stays a separate call
  that is finally given the diff. The first is one pass and one bill; the second keeps a cheap
  model on a cheap job and keeps the judging prompt shorter.
- Whether mechanical warns keep their `C` ids once the seat has weighed them, or become
  ordinary findings. The ids are how I tell code-found from seat-found today, and I may still
  want that distinction after they are judged.
- Where the rail overrule rate is recorded so I actually read it: `verdict.json`, the Gate 2
  page, the eval's summary line, or all three.
- Whether the golden packet is one packet or one per arm, given a blind packet cannot be widened.
- Whether dropping repo-wide lint from the packet loses a signal I currently rely on without
  noticing, and whether moving it to `make lint` is enough to keep it.
- Whether a mechanical warn the seat folds should still be countable at Gate 2 — I do not want
  "collapsed" to become a way for something real to disappear.
