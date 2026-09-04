---
name: run
description: >-
  Run one ticket, or a plan's tickets in depends_on order, through the headless runner
  (build → ci → verdict), stream its phase lines, and then take the Gate 2 decision in plain
  words. Use whenever the user says run, or run plan, with a ticket or plan id. The session
  drives runner.py and kanban_ops.py; it builds nothing, judges nothing, and touches no file.
disable-model-invocation: true
argument-hint: "<ticket id> | plan <n>"
---
# Run
A thin wrapper around two scripts. `runner.py` owns the run: branch, CI, the build session, the
verdict, the close-out, the commits, the board. `kanban_ops.py` owns Gate 2: the same
`board.act` functions the board's buttons call. This session relays the runner's phase lines,
ends the run on one line, then turns the human's words at Gate 2 into `kanban_ops.py` calls.

## One ticket: `/run <id>`
1. Start the runner and stream it. Its stdout is the run's record, the file the board tails:
   ```
   mkdir -p traces/runs && python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runner.py <id> --cwd . > traces/runs/<id>.log 2>&1; echo "[runner] exit $?" >> traces/runs/<id>.log
   ```
   Run it in the background; follow the log (`tail -n +<last line seen>`) until the
   `[runner] exit <code>` line lands. Relay every `[runner …]` phase line as it appears
   (`branch|worktree · ci-pre · build attempt n · tests commit · feat commit · build close-out ·
   ci · verdict · close-out`), nothing else. Do not summarise the builder's output, do not act
   on it, do not open the tree.
2. End the run on one line: `<decision> — traces/verdict/<id>.html`. The decision is the
   runner's `[runner] verdict: <decision>` line. When the runner ended before a verdict (exit 1
   red baseline, 3 needs context or blocked, 4 permission denied), the line is the runner's own
   reason instead of a decision, and there is no page: stop there, the human decides.
3. Gate 2, in this session. The human reads the page and says one of the lines below; each is
   exactly one command, `--who` set to `git config user.name` when it is set. Print the
   command's output and nothing more. A non-zero exit is the board's refusal, quoted as printed;
   never work around it.

   | The human says | The session runs |
   |---|---|
   | `ship` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py ship <id> --who <name>` |
   | `reject: <reason>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py reject <id> <reason> --who <name>` |
   | `child from F#` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py child <id> F# --who <name>` |
   | `home F# to <target>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py home <id> F# <target> --who <name>` |
   | `waive F#: <reason>` | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py waive <id> F# <reason> --who <name>` |

   `ship` merges the ticket branch and closes the gate. `reject` sends the ticket back to
   `in_progress`; `/run <id>` again is the retry. `child`, `home` and `waive` leave the gate
   open: the human says the next line. Words that match none of the five are a question back
   to the human, not an action.

## A plan: `/run plan <n>`
1. The order is the script's, not yours:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/kanban_ops.py order <n>` prints the plan's open
   tickets, one per line, dependencies first.
2. For each ticket in that order, do the one-ticket sequence above in full: run, one line,
   Gate 2. Move to the next ticket only after `ship` succeeded. Any other runner exit, or a
   `reject`, stops the walk: say which ticket it stopped at and why, and end.

## Never
- Build, judge, or fix anything. The runner's build session builds; the runner's verdict seat
  judges; findings are the human's to route.
- Change a ticket, a plan, a Log, a status, or a verdict by any means but the `kanban_ops.py`
  commands above. There is no other path from this session to `kanban/` or `traces/`.
- Invoke `/build` or `/verdict`. The runner runs them where they belong.
- Start a second runner while one is running, or run a ticket the order did not name.
- Guess at Gate 2. No human line, no command.
