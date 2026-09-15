# AGENTS

How this repository changes, as good and bad examples.

## Principle: Misses become types or rules, never checklists

When the grill misses something important, the fix is a node type or a closing rule that prevents the miss structurally. Never add a checklist item.

**Good:** "capability nodes close only on fetched docs or a run command."

**Bad:** "before the plan, reflect on ten fundamentals."

## Principle: Relied-on artifacts have a schema, a section, and a check — not sentences

Anything the harness relies on (dependencies, blocking risks, trace records, verdicts) must have:

1. A schema — one model per artifact, in `scripts/schemas.py`. It is the only definition of the artifact's shape.
2. A section in the artifact (`plan.md`, `ticket.md`, `trace.jsonl`).
3. A script that checks it — by loading through the schema, never by parsing on its own.

Never rely on a sentence in a skill file alone. Never let two scripts read the same artifact two different ways.

**Good:** `TraceRecord` model carries the node-type closing rules; `lint_trace.py` and `render_board.py` both load through it.

**Bad:** "Remember to check dependencies" in SKILL.md — or `render_board.py` splitting frontmatter with string operations.

## Principle: Skill changes replay on golden traces — pending, not yet in force

The intent: every skill change is replayed on `traces/golden/` before tagging, and a change that
breaks a golden trace is either wrong, or the trace needs updating with a documented reason.

**This is not in force today.** `traces/golden/` does not exist, so `scripts/replay.py` prints
`no golden set` and exits clean — a skill change can be tagged with nothing replayed. Two things
block it, and both are open:

1. `replay.py` prompts `/grill --replay`, but grill has no `--replay` mode. A replay today runs
   the live grill, which asks the human functional decisions and writes a plan.
2. The golden set is empty. Seeding it before (1) would record a trace from a grill that asks
   questions, which is not replayable.

Until both close, skill changes are verified by `make ci` and by reading the diff. Do not cite
this principle as though it ran.

## Principle: No client/system/dataset names

Nothing in this repo names a client, system, or dataset. Examples are generic or use placeholders.

**Good:** "capability: PostgreSQL supports JSONB columns — evidence: docs.postgresql.org/jsonb.html"

**Bad:** "Check if Acme Corp's CRM supports custom fields"

This keeps the plugin general-purpose and reusable.
