# AGENTS

How this repository changes, as good and bad examples.

## Principle: Misses become types or rules, never checklists

When the grill misses something important, the fix is a node type or a closing rule that prevents the miss structurally. Never add a checklist item.

**Good:** "capability nodes close only on fetched docs or a run command."

**Bad:** "before the plan, reflect on ten fundamentals."

## Principle: Relied-on artifacts have sections and checks, not sentences

Anything the harness relies on (dependencies, blocking risks, trace records) must have:
1.  A schema — one model per artifact, in scripts/schemas.py. It is the only
   definition of the artefact's shape.
2. A section in the artifact (plan.md, ticket.md, trace.jsonl).
3. A script that checks it — by loading through the schema, never by parsing on its own.


Never rely on a sentence in a skill file alone.

**Good:** Dependencies table in plan.md + lint_trace.py checks capability evidence.

**Bad:** "Remember to check dependencies" in SKILL.md.

## Principle: Skill changes replay on golden traces

Every skill change is replayed on `traces/golden/` before tagging. If a change breaks a golden trace, either:
1. The change is wrong, or
2. The golden trace needs updating (document why)

This prevents regressions and ensures backward compatibility.

## Principle: No client/system/dataset names

Nothing in this repo names a client, system, or dataset. Examples are generic or use placeholders.

**Good:** "capability: PostgreSQL supports JSONB columns — evidence: docs.postgresql.org/jsonb.html"

**Bad:** "Check if Acme Corp's CRM supports custom fields"

This keeps the plugin general-purpose and reusable.
