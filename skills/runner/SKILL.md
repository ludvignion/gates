---
name: runner
description: >-
  /harness-plugin:runner <id> | plan <n>. Run one ticket, or a plan's tickets in depends_on
  order, through the headless runner (build → ci → verdict), relay its phases from the state
  file, print the result block, then take the Gate 2 decision in plain words. Use whenever the
  user says run, runner, or run plan with a ticket or plan id. The session starts one process
  and calls kanban_ops.py; it builds nothing, judges nothing, and touches no file.
disable-model-invocation: true
argument-hint: "<ticket id> | plan <n>"
---
# Runner
One seat, one door, one view. The runner owns the run: branch, CI, the build session, the
verdict, the close-out, the commits, the state file, the result block, the board.
`kanban_ops.py` owns Gate 2. This session starts the runner once, relays its phase lines from
`traces/runs/<id>.state`, prints `traces/runs/<id>.result` when the run is done, and turns the
human's words at Gate 2 into `kanban_ops.py` calls. Nothing else happens here.

## Start: `/harness-plugin:runner <id>` or `plan <n>`
One Bash call, the only command in this skill besides the Gate 2 table:
```
mkdir -p traces/runs && nohup python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runner.py <id> --cwd . > traces/runs/<id>.log 2>&1 &   # a plan: `--plan <n>` in place of `<id>`, log and state are traces/runs/plan-<n>.{log,state}; when the runner refused a plan stamped backend: session and the human then says "override", the same line again with `--override backend=runner` after the id
```
Then print two lines: the log path, and `watch traces/board.html`. The board renders after every
phase; it shows the running ticket's phase, elapsed time and last builder lines.

If the runner refuses — a plan stamped `backend: session`, a dirty tree, a ticket the order did
not name — its message is in the log's first lines: print it as printed and stop. The one
permitted variation is the `--override backend=runner` form above, and only after the human
says `override`; it restamps the plan and logs the router miss.

## Follow
Wait 30 seconds (`sleep 30`, the one permitted command besides the Start line and the Gate 2
table). Read `traces/runs/<id>.state` (for a plan `traces/runs/plan-<n>.state`; the
Read tool is fine). Each line is `<hh:mm:ss> <+m:ss> <phase> [detail]`. For every line not yet
relayed print one chat line and nothing more:

    <id> · <phase> (+m:ss)

Phases, in the runner's own words: `branch · ci-pre · build <n> · build <n> close-out ·
build <n> skipped · tests-commit · feat-commit · ci · verdict · close-out · done <decision>`.
Silence between phases: no summary of the builder's output, no acting on it, no opening the
tree. Repeat until a line starting with `done` lands.

In a plan's state file the plan-level lines are `ticket <id>` (that ticket starts), `gate2 <id>`
(the walk waits for the ship) and the final `done <summary>`; print those with `plan <n>` in
front. The ticket's own phase lines appear verbatim in between and are printed with the ticket
id.

## The result
On a ticket's `done` line print `traces/runs/<id>.result` verbatim. The runner wrote it from
`verdict.json` and the Gate 2 page's computed recommendation; nothing in it comes from this
session. Its shape:

    <id> · verdict: SHIP|REJECT · N blocks · N warns · $cost · Ns
    F1 block (AC-2) src/app/extract.py:36 — "<finding text>" → child 2.2.1
    F2 warn (charter-3) tests/test_page.py:118 — "<text>" → home 2.3
    F3 warn (—) — "<text>" → waive
    Recommended: ship, child F1 → 2.2.1, home F2 → 2.3, waive F3
    <path of the Gate 2 page>

One line per finding in verdict order: number, severity, citation, `file:line` when the finding
names one (`—` otherwise), the finding's text, and the arrow's action — the recommended action
the Gate 2 page already computed for that finding. A `done error <reason>` line (ci red, retry
cap, needs context, permission denied) has no verdict: print that line and whatever result the
runner left, then stop; the human decides.

## Gate 2
In this session, in the same words the result recommends. The human reads the page and says one
of the lines below; each is exactly one command, `--who` set to `git config user.name` when it
is set. Print the command's output and nothing more. A non-zero exit is the board's refusal,
quoted as printed; never work around it.

| The human says | The session runs |
|---|---|
| `ship` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py ship <id> --who <name>` |
| `reject: <reason>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py reject <id> <reason> --who <name>` |
| `child from F#` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py child <id> F# --who <name>` |
| `home F# to <target>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py home <id> F# <target> --who <name>` |
| `waive F#: <reason>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py waive <id> F# <reason> --who <name>` |

The human answers with the recommended words or changes one. `ship` merges the ticket branch
into base, pushes, deletes the branch and closes the gate. `reject` sends the ticket back to
`in_progress` and leaves the branch checked out; `/harness-plugin:runner <id>` again is the
retry. `child`, `home` and `waive` leave the gate open: the human says the next line. Words
that match none of the five are a question back to the human, not an action.

## A plan
The runner walks the plan's tickets in `depends_on` order by itself, one ticket at a time, and
pauses at `gate2 <id>` until that ticket's status is `done`. So: the same Start line with
`--plan <n>`, the same Follow loop on `plan-<n>.state`, the same result block on each ticket's
`done` line, Gate 2 in this session. After `ship` the runner resumes on its own: keep
following. A `reject` ends the walk (`done stopped <id> rejected`), as does any runner error;
print the plan's `done <summary>` line and stop.

## Never
- Build, judge, or fix anything. The runner's build session builds; the runner's verdict seat
  judges; findings are the human's to route.
- Run any command but the Start line, `sleep 30`, and the Gate 2 table. No second process, no exploring a
  URL, no branch of your own, no tail of the log beyond a refusal.
- Read code. The state file and the result file are the whole view.
- Change a ticket, a plan, a Log, a status, or a verdict by any means but the `kanban_ops.py`
  commands above. There is no other path from this session to `kanban/` or `traces/`.
- Invoke the build or verdict skills. The runner runs them where they belong.
- Work around a refusal. A session stamp, a dirty tree, a ticket outside the order: print the
  runner's message and stop. Only the human's `override` word unlocks the session stamp.
- Guess at Gate 2. No human line, no command.
