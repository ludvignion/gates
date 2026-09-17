# Brief: A judged recommendation at Gate 2

## What I want

The Gate 2 result block's option A should be a recommendation, not a
generated list. The verdict seat proposes what to do with each open finding and says why in one line;
`render_verdict.py` validates each proposal against the five Gate 2 verbs and overrules the
illegal ones. Every verb the board accepts, including `waive`, appears as a line I can type.
Repeat sightings of the same nit collapse into one line instead of one child ticket each.

## Why it matters

Plan 5 ran to the end and the recommendation never once told me anything I did not already know.
Worse, it actively misrouted: `render_verdict.py:366` flips a `waive` into a child ticket the
second time a finding is seen, so eleven duplicate noise findings on 5.3 became eleven child
tickets and the plan walk ran 72 minutes longer than the work needed. A `waive` is never typable
from option A at all — `answer_options()` drops it into prose ("F3 is logged open and unfixed")
while the Gate 2 table accepts `waive F#: <reason>`. So the option that calls itself recommended
is the one option I cannot follow as written.

The result block is the whole Gate 2 view. If its recommendation cannot weigh a finding, I am
doing the weighing from a list of severities, which is the work I wanted the seat to do.

## What I already know / have decided

- The split: the seat proposes, code validates. The seat gets judgement; code keeps the rail.
  This is the third option of three I considered. Seat-authored-and-rendered-verbatim was
  rejected: a seat could then recommend waiving a block and the result block would print it.
  Code-authored-but-better was rejected: it cannot say why one finding outranks ten.
- The validation rail, non-negotiable: a `block` finding can never come back as `waive`. Code
  overrules an illegal proposal and falls back to today's computed action. The overrule is
  recorded, not hidden — I want to see how often the seat proposes something the rail refuses,
  because that rate is what tells me whether to keep the rail.
- The five verbs are fixed: `ship`, `reject: <reason>`, `child from F#`, `home F# to <target>`,
  `waive F#: <reason>`. The seat proposes among these and nothing else. Gate 2 itself, the board
  calls and `kanban_ops.py` are untouched.
- The per-finding reason is one line and it is about consequence, not code path — the same
  standard `consequence` already holds in the result block. "Who is blocked from what."
- Duplicates: a finding already seen on an earlier ticket is evidence of noise, not of
  importance. The current second-sighting rule has it exactly backwards. Repeats collapse to one
  line naming the group.
- The recommendation is per-finding plus one overall stance. The stance is the thing I read
  first: ship, ship with waivers, or send it back, and the one sentence for why.
- Everything the seat emits for this lands in `verdict.json` through a schema in
  `scripts/schemas.py`. No script parses it on its own.
- The verdict prompt is rewritten, not patched. Two reasons: the routing guidance it carries
  today is written for a code-computed action that is going away, and I want the rewrite to
  follow the current guidance on prompting capable models — see the reference below.
- This brief names `scripts/` and `skills/`, so it triages `full` (charter item 1,
  `Applies to: scripts/*, hooks/*, skills/*`). Plan page and Gate 1 as usual. I already checked
  this with `triage.py`; do not re-litigate the lane.

## Reference: prompt-writing guidance to apply to the rewrite

From OpenAI's developer blog, "Rethinking skills and prompts for GPT-6 Astra"
(https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra). Its own
caveat is that guidance is model-family-specific — it says guidance tuned for one model may
overconstrain another, and our seats run the `claude` CLI, not GPT. So take the structural
advice and leave the model-specific tuning. What I want applied:

- **Overly specific guidance now hinders where it once helped.** `verdict-prompt.md` should say
  what a good recommendation is and what the rail refuses, not enumerate branches. Today's
  five-branch routing is the thing being replaced; do not re-encode it as prose in the prompt.
- **Define completion before starting.** The prompt states what a complete recommendation
  contains — a verb and a reason per open finding, one overall stance — so the seat knows when it
  is done rather than inferring it.
- **Progressive disclosure: a short root that routes.** The prompt keeps the judgement and the
  output shape; reference material moves to a sibling file the seat reads when it needs it. This
  is already an open harness finding for `grill` and `runner` SKILL.md
  (`traces/harness-findings.md`, 2026-09-15 11:17); apply it here and it stops being theory.
- **Grant explicit permission for safe work instead of hedged prohibitions.** Where the prompt
  today tells the seat what not to do, say what it may decide.
- **Revisit whether each instruction is still needed.** Every line the rewrite keeps should be
  there because it is still load-bearing, not because it was there before.

The blog is a reference, not an authority: where its advice and this harness's invariants
disagree, the invariants win, and the plan should say where that happened.

## What I'm unsure about

- Whether the seat's proposal is a new key per finding in the existing findings array, or one
  `recommendation` object beside them. The findings array's shape is fixed by
  `verdict-prompt.md` and round-trips through `Verdict.raw`; I do not know which costs less.
- Where the overrule is recorded: `verdict.json`, the Gate 2 page, the result block, or the
  ticket Log. I want it visible without cluttering the block.
- Whether the overall stance can disagree with `decision` in `verdict.json`, or is derived from
  it. If a seat can say "ship" while the decision is `reject`, one of them is wrong.
- Whether `recommendations()` survives as the fallback path or is deleted once the seat owns the
  proposal. Something must still produce a recommendation when the seat's proposal fails the
  schema.
- How the collapsed duplicate line reads when the repeats are spread over three earlier tickets.
- Whether the rewritten prompt needs a golden replay before it ships, given
  `traces/golden/` has never existed (`traces/harness-findings.md`, 2026-09-15 11:31) and the
  prompt sha is part of the verdict seam metadata.
- Whether one line of reason per finding is enough for a high-impact block, or that one case
  earns three.
