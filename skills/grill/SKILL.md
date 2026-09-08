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
   - `capability` — a requirement on a tool, API, library, or data source. Closes only when
     BOTH hold: (a) provider — evidence the named provider does the thing: documentation you
     fetched or a command you ran; (b) access — evidence we can use it from this repo: a key
     present in `.env`/`.env.example` and a successful auth-level call, an installed package,
     or a plan tier confirmed. A human naming the tool closes neither. A capability with
     provider verified but access unverified is NOT closed — it goes to Options (rule 6).
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
   Rank the options against the repo's invariants first, cost second (G1): `AGENTS.md` names
   them — a mechanism over a second copy of a value, boring over elegant, no knobs. The
   recommendation names the invariant it preserves; the cheapest option is recommended only
   when it preserves them all.
5. **Stop when the frontier is empty.** No question count. If a node cannot be resolved by
   evidence or one human answer, it becomes an explicit assumption.
6. **Dependencies before the plan.** Every `capability` node becomes a row in the plan's
   Dependencies table: `capability | provided by | provider evidence | access evidence`.
   For each node that did not fully close, write an entry under the plan's **## Options**:
   the capability, then 2–3 routes with one line each on cost, delay, and which ACs change —
   always including (a) acquire the named access, (b) substitute with a capability we already
   hold access to, and (c) defer, naming the ticket that blocks. Recommend one. The human
   picks at gate 1; the choice is recorded as a `decision` node in the trace. A plan with an
   unchosen option cannot be approved.
7. **Write back, then wait.** Produce `<n>.plan.md` from `templates/plan.md`:
   outcome, ACs (Given/When/Then, tagged behavioral / property / critical / human — rule 11),
   out of scope, named modules, `base:` (the branch tickets branch from and ship into, `main`
   unless the human said otherwise), and the **assumptions list** — every node you resolved
   yourself, with its evidence. Stamp `signals:`, `scrutiny:`, and `backend:` per the Routing
   section. The human approves the plan, not the questions. When they say so, first record
   what they said about any option or recommendation in that turn (G3): one line per remark
   appended to `traces/grill-misses.jsonl`,
   `{"plan": n, "source": "approval", "said": "<their words>", "about": "<option or recommendation>", "ts": "<ISO time>"}`,
   before the approve runs — a remark that never lands in the file never tunes the grill.
   Then run
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py approve <n> --who <git config user.name>`
   and print its output; it sets `status:` and `approved:`, writes the `[human]` Log entry and
   commits. Never write those fields yourself. A refusal (missing routing stamp) is printed as
   is; fix the stamp, then approve again. Do not create tickets until the approve succeeded.
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
11. **Every AC is machine-verifiable, or tagged human** (E30). An AC holds when the diff and
   the tests show it: a test names it, or a file the ticket writes carries it. An AC only a
   person can confirm — a look at a page, a judgement of tone, a call to a live system — is
   tagged in the plan and the ticket: `- AC-7 (human): Given ..., a person confirms ...`.
   Human ACs stay out of the verdict packet; the Gate 2 page lists them under "Human checks"
   and the runner's result block prints one `human: AC-7 — ... (confirm with ship)` line each.
   The human confirms them by saying `ship`. An untagged AC no test can name is a warn on
   every verdict — tag it or rewrite it as something the diff shows.
12. **Hand off** (G2). After the tickets are written, compute the last line from the plan's
   stamp and the tickets, never from the shape of a previous plan:
   - `backend: runner` and more than one ticket `status: ready` →
     `Next: /harness-plugin:runner plan <n>`
   - `backend: runner` and one ready ticket → `Next: /harness-plugin:runner <id>`
   - `backend: session` → `Next: build from branch <base> (session backend by override)`,
     `<base>` being the plan's `base:`.
   Print it as the last line, alone. No menu, no second option.

## Routing

The human decides what gets built; the harness decides how it runs. Grill computes routing —
never asks it, and it is not part of gate 1 approval.

As nodes close, grill records four signals in the plan frontmatter. They are facts derived
from the tickets, with a one-line evidence note each:

    signals:
      spend: true|false           # any ticket calls a paid API or mutates external state
      partner_facing: true|false  # any ticket's output leaves the repo (client, partner, prod)
      parallel_ready: <n>         # count of tickets with disjoint writes: and no depends_on chain
      tickets: <n>

From the signals, stamp two derived fields, each with its derivation on the same line:

    scrutiny: light | full        # full iff spend or partner_facing
    backend: runner | workflow | session
                                  # workflow iff parallel_ready >= 3 (requires plan-tier support)
                                  # runner otherwise
                                  # session only when the human overrides to it (a router miss, logged)
                                  # if no workflow backend exists in the plugin, stamp runner instead and log the downgrade in the plan Log

The text after `#` on the stamp line is what the board shows as the rule, so write it as the
rule that fired: `backend: runner  # runner: default; session only by override` or
`backend: workflow  # workflow: parallel_ready >= 3`.

Scrutiny sets review cadence: light = tests-first, human reads diffs, ONE whole-branch verdict
before ship. Full = per-ticket verdict as ship gate for each slice. The charter and domain pack
apply identically in both.

Backend sets who owns the control flow: runner = scripts/runner.py drives, started by
/harness-plugin:runner (one ticket, or a plan in depends_on order, stopping at Gate 2; ticket
branch, status protocol, retries); session = the human drives interactively, only by override;
workflow = a generated orchestration script drives the DAG, invoking build and verdict per
ticket under the same contract: tickets in, traces out, stops at the gates.

Signals are re-derived whenever a ticket is added or edited, child tickets included. If a
derived field rises (light→full, runner→workflow), stamp the new value and append a dated line
to the plan Log naming the signal that flipped. Derived fields never fall.

The human may override by editing the stamped value. An override is a router miss: log it in
the plan Log and append a record to traces/grill-misses.jsonl with the signal set and the
override — it means the derivation rules are wrong, and they get tuned, not the plan.

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
Cite files, or directories with a trailing slash — a bare directory path without the slash
is not checked by lint. A `capability` node claiming a provider keeps or supports a set of
things is closed only by a probe that exercises every member named in the ACs.

## Brief shape
Refuse a brief whose Outcome is a code property — validation, typing, models, a refactor,
"add X across stages" — rather than a behaviour observable in the CLI or an output file. That is
a horizontal phase. Say so and name the vertical ticket that first needs it. Refuse likewise while
any ticket of an unshipped tracer-bullet plan is `in_progress` or `in_review`.

Next: the hand-off line from rule 12, computed from the stamp, as the session's last line.
