---
name: grill
description: >-
  Turn a brief in kanban/briefs/ into an approved plan and vertical-slice tickets by interrogating
  the design tree — evidence first, human only for decisions. Use whenever the user says grill, has
  a new brief, wants a plan, or asks to start a feature. Never write product code from this skill.
disable-model-invocation: true
argument-hint: "<brief number>"
---

# Grill

Input: `kanban/briefs/<n>-<slug>.md`. Output: `kanban/plans/<n>.plan.md` (gate 1), then tickets
`kanban/tickets/<n>.<m>.<slug>.md`, and `traces/grill/<n>.jsonl`.

## Rules

1. **Build a design tree from the brief.** Root = the outcome. Children = decisions that must be
   made to reach it. A question exists only to resolve a node. No question without a node.
2. **Evidence before asking.** For every node, first search: `src/`, `tests/`, `docs/`, closed
   tickets in `kanban/tickets/`, `traces/blind-spots.md`. If the answer is there, resolve it and cite the
   path. Facts are your job. Decisions are the human's.
3. **Read `traces/blind-spots.md` first** if it exists. Those are categories the grill missed before.
   Check each against this brief explicitly.
4. **Ask the human only decisions** — intent, trade-offs, priority, scope. Each question names the
   node it unblocks and carries a recommended answer. Ask in rounds, numbered. Cap: 5 per round,
   2 rounds. If more remain, the brief is too big — say so and propose a split.
5. **Stop when the frontier is empty.** No question count. If a node cannot be resolved by
   evidence or one human answer, it becomes an explicit assumption.
6. **Write back, then wait.** Produce `<n>.plan.md` from `templates/plan.md`:
   outcome, ACs (Given/When/Then, tagged behavioral / property / critical), out of scope,
   named modules, and the **assumptions list** — every node you resolved yourself, with its evidence.
   The human approves the plan, not the questions. Do not create tickets until they say so.
7. **Then slice.** Vertical slices only — each ticket crosses every layer it touches and is
   demonstrable alone. Use `templates/ticket.md`. Declare `depends_on` and `writes`.
   The first ticket is the tracer bullet.
8. **Trace.** Append one record per node to `traces/grill/<n>.jsonl`:
   `{"brief": n, "node": "...", "resolved_by": "evidence|human|assumption", "evidence": "path or null", "question": "...", "answer": "..."}`.

## Do not

- Ask what the codebase can answer.
- Ask questions to reach a number.
- Restate the brief back as a question.
- Propose architecture the ACs don't require.
- Write code, tests, or ADRs. Note ADR candidates in the plan under "Decisions worth an ADR".

## Child tickets

A rejected verdict spawns `kanban/tickets/<n>.<m>.<p>.<slug>.md`. The rejection is the brief. Skip gate 1.
Run rules 1–3 and 8 only; no human round unless a decision is genuinely open.
