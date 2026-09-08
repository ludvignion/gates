---
name: runner
description: >-
  /harness-plugin:runner <id> | plan <n>. Run one ticket, or a plan's tickets in depends_on
  order, through the headless runner (build → ci → verdict), stream its phase and heartbeat
  lines from the state file, print the result block, then take the Gate 2 decision in plain
  words. Use whenever the user says run, runner, or run plan with a ticket or plan id. The
  session starts one process, watches one file and calls kanban_ops.py; it builds nothing,
  judges nothing, and touches no file.
disable-model-invocation: true
argument-hint: "<ticket id> | plan <n>"
---
# Runner
One seat, one door, one view. The runner owns the run: branch, CI, the build session, the
verdict, the close-out, the commits, the state file, the result block, the board.
`kanban_ops.py` owns Gate 2. This session starts the runner once, streams its lines from
`traces/runs/<id>.state`, prints `traces/runs/<id>.result` when the run is done, and turns the
human's words at Gate 2 into `kanban_ops.py` calls. Nothing else happens here.

## Start: `/harness-plugin:runner <id>` or `plan <n>`
One Bash call, the first of the three commands this skill allows (Start, Watch, Gate 2 table):
```
rm -f traces/runs/<id>.state traces/runs/<id>.pid && mkdir -p traces/runs && nohup python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runner.py <id> --cwd . > traces/runs/<id>.log 2>&1 &   # a plan: `--plan <n>` in place of `<id>`, log, state and pid are traces/runs/plan-<n>.{log,state,pid}; when the runner refused a plan stamped backend: session and the human then says "override", the same line again with `--override backend=runner` after the id
```
The `rm -f` clears the previous run's state and pid so the watch below cannot follow a stale
run. Then print one line, the log path, and go straight to the watch.

If the runner refuses, its message is in the log's first lines: print it as printed and stop.
Refusals, each one line from the runner (E20, E21, E23, E28):
- a plan stamped `backend: session` — the one permitted variation is the `--override
  backend=runner` form above, and only after the human says `override`; it restamps the plan
  and logs the router miss;
- a dirty tree; a ticket the order did not name;
- `<id> is done; nothing to run` — the ticket is already shipped;
- `ticket/<id> is already merged into <base>` — the branch is an ancestor of the base branch;
- `nothing to judge` — no included file changed against the base branch, so no verdict;
- `packet too big: ~N tokens (cap 40000)` — narrow the ticket or the human raises
  `--packet-cap`;
- `invalid verdict: num_turns=N (the seat must be one turn)` — the verdict seat answered in
  more than one turn; not retried.
The last three land after the build, as a `done error <reason>` state line; the first four
land before any branch exists.

## Watch
One Bash line, the second allowed command. It streams: Claude Code shows a running command's
output as it appears, so every state line reaches the chat the moment the runner writes it.
```
sh -c 'while [ ! -f traces/runs/<id>.pid ]; do sleep 1; done; tail -n +1 -f --pid=$(cat traces/runs/<id>.pid) traces/runs/<id>.state & t=$!; while ! tail -n 1 traces/runs/<id>.state | grep -qE "^[^ ]+ [^ ]+ (done|gate2) "; do sleep 2; done; sleep 2; kill $t 2>/dev/null'
```
How it works: the runner writes its pid to `traces/runs/<id>.pid` right after truncating the
state file, so the first loop waits for a fresh run; `tail` follows the state file and exits
when the runner does (`--pid`); the second loop returns when the LAST line's phase is `done` or
`gate2` (a plan's file also carries each ticket's own `done` line, followed at once by
`gate2 <id>`, so only the last line counts), and the final `kill` ends the follower when the runner is still alive at a gate. For a
plan the file names are `traces/runs/plan-<n>.{pid,state}`.

The Bash tool returns after 10 minutes at most. When it returns without a `done` or `gate2`
line the run is still on: run the same watch line again with `-n 0` in place of `-n +1`, so
nothing already shown is replayed. Print nothing yourself while the watch runs: no summary of
the builder's output, no acting on it, no opening the tree.

Every state line is `<hh:mm:ss> <+m:ss> <phase> [detail]`. Phases, in the runner's own words:
`branch · ci-pre · build <n> · build <n> close-out · build <n> skipped · tests-commit ·
feat-commit · ci · verdict · close-out · done <decision>`. Every 30 seconds while the build
session streams the runner adds a heartbeat line (E25):

    <hh:mm:ss> <+m:ss> build 1 · running · last <hh:mm:ss> · <last builder line, 80 chars>

It streams like a phase line and means the build is alive; a run with no new line for minutes
is stuck, not silent. In a plan's state file the plan-level lines are `ticket <id>` (that
ticket starts), `gate2 <id>` (the walk waits for the ship) and the final `done <summary>`; the
ticket's own lines appear verbatim in between.

## The result
On a ticket's `done` line print `traces/runs/<id>.result` verbatim. The runner wrote it from
`verdict.json`, `summary.json`, the packet and the Gate 2 page's computed recommendation;
nothing in it comes from this session. Its shape, in this order (E24, E27, E30):

    <what was built, one sentence from summary.json>
    <what the reviewer said, one sentence from summary.json>
    <id> · verdict: SHIP|REJECT · N blocks · N warns · verdict $cost/Ns · build $cost/Ns · run Ns
    F1 block (AC-2) src/app/extract.py:36 — "<finding text>" → child 2.2.1
    F2 warn (charter-3) tests/test_page.py:118 — "<text>" → home 2.3
    F3 warn (—) — "<text>" → waive
    charter: 2, 7 reachable — both held
    human: AC-7 — <text> (confirm with ship)
    changed vs main: 3 files +120/−8
      src/app/extract.py +90/−4
      tests/test_extract.py +30/−4
      docs/usage.md +0/−0
    Recommended: ship, child F1 → 2.2.1, home F2 → 2.3, waive F3
    <path of the Gate 2 page>

Line by line: the two summary sentences (absent when the summary errored); the header; one
line per open finding in verdict order — number, severity, citation, `file:line` when the
finding names one (`—` otherwise), the finding's text, and the arrow's action, the recommended
action the Gate 2 page already computed; the charter line — which charter items the diff can
reach and whether they held (`charter: 4 reachable — F3` names the finding against one;
`charter: none reachable` when the diff touches nothing a charter item applies to); one
`human:` line per AC tagged `(human)`, which no test can verify; the files changed against
the base branch (the word after `vs` is the base branch's name), one indented line per file;
the Recommended line; the page path. A `done error <reason>` line (ci red, retry cap, needs
context, permission denied, nothing to judge, packet too big, num_turns) has no verdict: print
that line and whatever result the runner left, then stop; the human decides.

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
into base, pushes, deletes the branch and closes the gate; it is also the human's confirmation
of every `human:` line in the result — the board logs each as confirmed by `--who`. `reject`
sends the ticket back to `in_progress` and leaves the branch checked out;
`/harness-plugin:runner <id>` again is the retry. `child`, `home` and `waive` leave the gate
open: the human says the next line. Words that match none of the five are a question back to
the human, not an action.

## A plan
The runner walks the plan's tickets in `depends_on` order by itself, one ticket at a time, and
pauses at `gate2 <id>` until that ticket's status is `done`. So: the same Start line with
`--plan <n>`, the same watch on `plan-<n>.state`, the same result block on each ticket's
`done` line, Gate 2 in this session. After `ship` the runner resumes on its own: run the watch
line again. A `reject` ends the walk (`done stopped <id> rejected`), as does any runner error;
print the plan's `done <summary>` line and stop.

## Hand-off
The session's last line is one of these, and nothing follows it:

| After | The last line |
|---|---|
| `ship` inside a plan walk | `Next: keep watching (the walk resumes)` |
| `ship` of a single ticket whose plan is `<n>` | `Next: /harness-plugin:runner plan <n>` |
| `ship` of a single ticket with no plan left to walk | `Next: /grill <next brief>` |
| `reject` | `Next: /harness-plugin:runner <id>` |
| a refusal or a `done error` | `Next: <the runner's own line>` — the human decides |

## Never
- Build, judge, or fix anything. The runner's build session builds; the runner's verdict seat
  judges; findings are the human's to route.
- Run any command but the Start line, the watch line, and the Gate 2 table. No second process,
  no exploring a URL, no branch of your own, no reading the log beyond a refusal, no sleeping
  loop of your own: the watch line waits.
- Read code. The state file and the result file are the whole view.
- Change a ticket, a plan, a Log, a status, or a verdict by any means but the `kanban_ops.py`
  commands above. There is no other path from this session to `kanban/` or `traces/`.
- Invoke the build or verdict skills. The runner runs them where they belong.
- Work around a refusal. A session stamp, a dirty tree, a ticket outside the order, a shipped
  ticket, a merged branch, an empty diff, an oversized packet, a multi-turn verdict: print the
  runner's message and stop. Only the human's `override` word unlocks the session stamp.
- Guess at Gate 2. No human line, no command.
- Print a menu. One hand-off line, from the table above.

Next: the one hand-off line from the table above, verbatim, as the session's last line.
