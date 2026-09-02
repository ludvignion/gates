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

1. **Build a typed design tree from the brief.** Root = the outcome. Children =
   nodes that must close to reach it. Every node has a type, and the type decides what
   closes it:
   - `fact` — closes on an evidence path in the repo or domain pack.
   - `capability` — a requirement on a tool, API, library, or data source. Closes only
     on evidence that the named provider does the thing: documentation you fetched, or a
     command you ran. A human naming the tool does not close it.
   - `decision` — intent, trade-off, priority, scope. Closes on a human answer.
   - `assumption` — could not be closed by evidence or one human answer. Never closes;
     it goes into the plan's assumptions table.
   A question exists only to close a `decision` node. No question without a node.
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
6. **Dependencies before the plan.** Every `capability` node becomes a row in the plan's
   Dependencies table: `capability | provided by | evidence`. Rows whose node did not close
   become **Blocking risks** in the plan.
7. **Write back, then wait.** Produce `<n>.plan.md` from `templates/plan.md`:
   outcome, ACs (Given/When/Then, tagged behavioral / property / critical), out of scope,
   named modules, and the **assumptions list** — every node you resolved yourself, with its evidence.
   The human approves the plan, not the questions. Do not create tickets until they say so.
8. **Success and failure lines.** If the brief has "Success looks like", it becomes at
   least one AC in the plan. If it has "Not this", it becomes an out-of-scope line AND is
   written into the plan under "## Verdict must attack" so the verdict's adversarial pass
   targets it. If either section is missing, do not ask for it — proceed.
9. **Then slice.** Vertical slices only — each ticket crosses every layer it touches and is
   demonstrable alone. Use `templates/ticket.md`. Declare `depends_on` and `writes`.
   The first ticket is the tracer bullet.
10. **Trace.** Append one record per node to `traces/grill/<n>.jsonl`:
   `{"brief": n, "node": "...", "type": "fact|capability|decision|assumption",
"resolved_by": "evidence|human|assumption|open", "evidence": "path|url|command|null",
"question": "...", "answer": "..."}`.

## Do not

- Ask what the codebase can answer.
- Ask questions to reach a number.
- Restate the brief back as a question.
- Propose architecture the ACs don't require.
- Write code, tests, or ADRs. Note ADR candidates in the plan under "Decisions worth an ADR".

## Child tickets

A rejected verdict spawns `kanban/tickets/<n>.<m>.<p>.<slug>.md`. The rejection is the brief. Skip gate 1.
Run rules 1–3 and 9 only; no human round unless a decision is genuinely open.

## Evidence discipline
`resolved_by: evidence` is allowed only after opening the source. For a file path, `evidence`
quotes its first line and its line count. For a schema, it quotes the header row or field list
as read, not as described in the brief. If the path does not exist, the node is
`resolved_by: human` with a question. A brief describing a file is not evidence the file exists.
## Brief shape
Refuse a brief whose Outcome is a code property — validation, typing, models, a refactor,
"add X across stages" — rather than a behaviour observable in the CLI or an output file. That is
a horizontal phase. Say so and name the vertical ticket that first needs it. Refuse likewise while
any ticket of an unshipped tracer-bullet plan is `in_progress` or `in_review`.
