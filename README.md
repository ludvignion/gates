# harness-plugin

The operating system for agentic development. Seven skills, two human gates, hooks that enforce
ticket scope, scripts that render the only pages a human reads.

```
brief → /grill → plan page (gate 1) → tickets → /build → /verdict → verdict page (gate 2) → merge
                                    or: /harness-plugin:runner <id> | plan <n> → runner → gate 2 in words → ship
```

## Install

```
claude plugin marketplace add ludvignion/harness-plugin
claude plugin install harness-plugin@ludvignion
```

## Start a project

1. Install the plugin (above).
2. `/harness-plugin:init` in an empty git repo — the template ships inside the plugin.
3. Write `kanban/briefs/1-<slug>.md`, then `/harness-plugin:grill 1`.

## From a spec

1. Put the spec in `docs/spec/` (one file). Two shapes: markdown with headings (the id is the
   heading path, `contacts/call-log`), or CSV with an `id` column (ids verbatim). Convert pdf,
   docx, xlsx outside first.
2. `make intake F=docs/spec/<file>` writes `docs/spec/index.md`: one row per unit with its sha.
3. Paste `templates/decompose.md`, the spec and the index into a chat; rank the table it
   returns; paste the briefs and `docs/spec/deferred.md` it writes into the repo. All rows at
   once: the partition is the deliverable, later briefs stay thin until their grill.
4. `make coverage` green (every id in one brief or deferred, nothing drifted), then
   `/harness-plugin:grill 1`. When a requirement changes later, `make intake` again; coverage
   goes red with `drift: [<id>] changed since brief <n>` until you review that brief, edit it,
   and commit it together with the new index.

## What's here

| Path | Role |
|---|---|
| `skills/init` | `/harness-plugin:init [--name <n>]`: the project template, copied from `templates/project/` into an empty repo, placeholders filled, plugin pinned to this version, `make install && make ci`, one commit. `--update [--yes]` diffs the template-owned files (Makefile, .gitignore, .env.example, CI workflow, `docs/spec/.gitkeep`, plugin pin) and writes them only with `--yes`; never `kanban/`, the rest of `docs/`, `src/`, `tests/`, `traces/`. |
| `skills/grill` | brief → plan + tickets. Evidence first; human only for decisions; options ranked against the repo's invariants before cost. ACs are machine-verifiable or tagged `(human)` and confirmed at Gate 2 with `ship`; approval-turn remarks land in `traces/grill-misses.jsonl`; ends with one hand-off line computed from the routing stamp. |
| `skills/build` | tests-first, scope-bound implementation. CI is the authority. |
| `skills/verdict` | fresh-context review in one model call over a prepared input → verdict page. |
| `skills/briefing` | opt-in response style: what changed, then lettered options. |
| `skills/finding` | `/harness-plugin:finding <text>`: one dated line into `traces/harness-findings.md` with the plugin version and the active ticket — the human's notebook of what the harness got wrong. |
| `skills/runner` | `/harness-plugin:runner <id>` or `plan <n>`: starts `runner.py` in the background, watches the state file with a repeated short `tail` (phase lines and a 30-second heartbeat while the build runs), prints the result block (build and verdict time, tokens, what was built, findings with file:line and the Gate 2 words, charter items held or not judged, human ACs to confirm, files changed, the recommended words); Gate 2 in words (ship, reject, child, home, waive) through `kanban_ops.py`; ends with one hand-off line. |
| `hooks/guard_writes.py` | blocks writes to closed tickets, and outside the active ticket's `writes:`. |
| `hooks/trace_stop.py` | one JSONL line per agent turn into `traces/sessions.jsonl`. |
| `skills/verdict/verdict-prompt.md` | the judging prompt, copied into every packet; its sha is in the verdict stamp. |
| `scripts/verdict_prep.py` | the verdict packet: one self-contained input, `--arm blind\|packet\|repo` picks how much context. |
| `scripts/verdict_checks.py` | mechanical verdict findings, zero model tokens; validates the written verdict for its arm. |
| `scripts/render_verdict.py` | close-out and the Gate 2 page: what was built, what the reviewer said, recommended action (computed), stamp `meta`. |
| `scripts/verdict_eval.py` | archived packets → Opik dataset; one arm × one verdict command as an Opik experiment. |
| `scripts/verdict_canned.py` | verdict command that copies a prepared JSON to `{output}`; CI's model-free seat. |
| `scripts/render_board.py` | the board: routing stamp with its rule (Gate 1), progress per plan, status columns, dependency graph, and per running ticket its phase, elapsed time and last builder lines, auto-refreshing while a run is on. Runs on every Stop and after every runner phase. |
| `scripts/lint_kanban.py` | `make kanban`, in the template's `ci`: closed tickets immutable, findings have homes, no ship past an open block, approved plans carry a routing stamp, approved plans name every cited spec id in an AC. |
| `scripts/spec_intake.py` | `make intake F=`: the spec (markdown headings or CSV `id` column) → `docs/spec/index.md`, one row per unit: id, title, words, sha. Never edits the spec. |
| `scripts/coverage.py` | `make coverage`: every index id in exactly one brief's `spec_refs` or `docs/spec/deferred.md`; cited ids exist; no drift since the brief's commit (git is the record); `after` briefs exist, no cycle. Silent without an index. |
| `templates/decompose.md` | the prompt a human pastes into a chat with the spec and the index: one table (brief, outcome, spec_refs, after) plus deferred rows, every id once; then one file per row. |
| `scripts/grill_digest.py` | grill-misses → blind-spots the grill reads. |
| `scripts/replay.py` | re-run grill on golden briefs, diff escalations. |
| `scripts/runner.py` | headless build → ci → verdict state machine: loads the project's `.env`, branches in place and stays on the ticket branch after the verdict (`--parallel` for worktrees), streamed build that ends at close-out, phase lines, board after every phase; `--plan <n>` walks a plan in depends_on order and pauses at Gate 2; writes `traces/runs/<id>.{log,state,result}`; `--arm`, `--verdict-cmd` pick the verdict seat; `--override` restamps routing. |
| `scripts/board.py` | the Gate 1 and Gate 2 action functions — approve, override, ship, reject, child from finding, home, waive; no server. `kanban_ops.py` is the command line. |
| `scripts/kanban_ops.py` | the writes a human made by hand: Log entry, status, routing override, commit. The one Log writer. `kanban_ops.py ship\|reject\|child\|home\|waive\|order` is the board's Gate 2 from a shell. |
| `scripts/vendor.py` | one shell model call and how its JSON envelope is read; runner and render_verdict share it. |
| `scripts/init_project.py` | `init` and `init --update`: what the init skill runs. |
| `templates/` | brief (`spec_refs`, `after`), plan, ticket, ADR skeletons; `templates/project/` is the whole project skeleton `init` copies (`{{project_name}}`, `{{plugin_ref}}` are its only placeholders). |

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
