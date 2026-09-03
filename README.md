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
| `scripts/render_plan.py` | gate 1 page. |
| `scripts/verdict_prep.py` | one prepared input for the single-call verdict. |
| `scripts/verdict_checks.py` | mechanical verdict findings, zero model tokens. |
| `scripts/render_verdict.py` | gate 2 page; archives the previous verdict for retries. |
| `scripts/render_board.py` | progress per plan, status columns, dependency graph. Runs on every Stop. |
| `scripts/lint_kanban.py` | CI check: closed tickets immutable, findings have homes, no ship past an open block, approved plans carry a routing stamp. |
| `scripts/grill_digest.py` | grill-misses → blind-spots the grill reads. |
| `scripts/replay.py` | re-run grill on golden briefs, diff escalations. |
| `scripts/runner.py` | headless build → ci → verdict state machine, retry cap 4. |
| `templates/` | brief, plan, ticket, ADR skeletons. |

## Opik

Traces go to `traces/` locally. For Opik, install the official `opik-claude-code-plugin` alongside
this one; tag runs with the ticket id via `kanban/.active`.

## Rules baked in

- Skills are capabilities. Briefs are deliverables. Never put a deliverable in `skills/`.
- Nothing here names a client, system, or dataset. That lives in the project's `docs/domain-pack/`.
- Pin projects to a tag of this repo.
