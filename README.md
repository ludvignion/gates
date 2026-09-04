# harness-plugin

The operating system for agentic development. Four skills, two human gates, hooks that enforce
ticket scope, scripts that render the only pages a human reads.

```
brief → /grill → plan page (gate 1) → tickets → /build → /verdict → verdict page (gate 2) → merge
```

## Install

```
claude plugin marketplace add ludvignion/harness-plugin
claude plugin install harness-plugin@ludvignion
```

Then create a project from [project-template](https://github.com/ludvignion/project-template).

## What's here

| Path | Role |
|---|---|
| `skills/grill` | brief → plan + tickets. Evidence first; human only for decisions. |
| `skills/build` | tests-first, scope-bound implementation. CI is the authority. |
| `skills/verdict` | fresh-context review in one model call over a prepared input → verdict page. |
| `skills/briefing` | opt-in response style: what changed, then lettered options. |
| `hooks/guard_writes.py` | blocks writes to closed tickets, and outside the active ticket's `writes:`. |
| `hooks/trace_stop.py` | one JSONL line per agent turn into `traces/sessions.jsonl`. |
| `skills/verdict/verdict-prompt.md` | the judging prompt, copied into every packet; its sha is in the verdict stamp. |
| `scripts/verdict_prep.py` | the verdict packet: one self-contained input, `--arm blind\|packet\|repo` picks how much context. |
| `scripts/verdict_checks.py` | mechanical verdict findings, zero model tokens; validates the written verdict for its arm. |
| `scripts/render_verdict.py` | close-out and the Gate 2 page: what was built, what the reviewer said, recommended action (computed), stamp `meta`. |
| `scripts/verdict_eval.py` | archived packets → Opik dataset; one arm × one verdict command as an Opik experiment. |
| `scripts/verdict_canned.py` | verdict command that copies a prepared JSON to `{output}`; CI's model-free seat. |
| `scripts/render_board.py` | Gate 1: routing stamp with its rule, progress per plan, status columns, dependency graph. Runs on every Stop and after every runner phase. |
| `scripts/lint_kanban.py` | CI check: closed tickets immutable, findings have homes, no ship past an open block, approved plans carry a routing stamp. |
| `scripts/grill_digest.py` | grill-misses → blind-spots the grill reads. |
| `scripts/replay.py` | re-run grill on golden briefs, diff escalations. |
| `scripts/runner.py` | headless build → ci → verdict state machine: branch in place (`--parallel` for worktrees), streamed build that ends at close-out, phase lines, board after every phase; `--arm`, `--verdict-cmd` pick the verdict seat; `--override` restamps routing. |
| `scripts/board.py` | `board.py serve`: the board with actions — approve, override, ship, reject, child from finding, home, waive, run a ticket or a plan. Localhost, no auth. |
| `scripts/kanban_ops.py` | the writes a human made by hand: Log entry, status, routing override, commit. The one Log writer. |
| `scripts/vendor.py` | one shell model call and how its JSON envelope is read; runner and render_verdict share it. |
| `templates/` | brief, plan, ticket, ADR skeletons. |

`scripts/` is the runtime the skills and project Makefiles call; `tests/` tests it. Neither is
invoked by users directly.

## Opik

Traces go to `traces/` locally. `runner.py` sends the one verdict call as one Opik trace when the
`opik` package is importable and `OPIK_URL_OVERRIDE` is set; otherwise nothing
changes. `verdict_eval.py` compares seats over a project's archived packets. For session-level
traces, install the official `opik-claude-code-plugin` alongside this one; tag runs with the
ticket id via `kanban/.active`.

## Rules baked in

- Skills are capabilities. Briefs are deliverables. Never put a deliverable in `skills/`.
- Nothing here names a client, system, or dataset. That lives in the project's `docs/domain-pack/`.
- Pin projects to a tag of this repo.
