# Changelog

## 0.6.4 — 2026-09-04

- `skills/run/SKILL.md` — new. `/run <id>` or `/run plan <n>`. A thin wrapper: the session starts
  `runner.py` for the ticket (or for each ticket of the plan in `depends_on` order, pausing at
  every Gate 2), relays its phase lines, and ends the run on one line: decision and the Gate 2
  page path. Then, in the same session, Gate 2 is plain language — `ship`, `reject: <reason>`,
  `child from F#`, `home F# to <id>`, `waive F#: <reason>` — each one `kanban_ops.py` command,
  the same `board.act` the board's buttons call. The session builds, judges and edits nothing.
  `/build` and `/verdict` are unchanged.
- `scripts/kanban_ops.py` — a command line: `ship|reject|child|home|waive <id> ...` (`--who`,
  `--cwd`) dispatches through `board.act` with the board's form; `order <n>` prints a plan's
  open tickets in `depends_on` order. A refusal is the board's message on stderr, exit 1.
- `.claude-plugin/plugin.json` — the five skills listed under `skills`; version 0.6.4.
- `tests/test_run_skill.py` — the skill text names `runner.py` and `kanban_ops.py` and carries no
  file-editing instruction; the CLI's reject, waive, order and refusal on a git copy of the
  fixture project.

Consuming projects (project-template): pin `v0.6.4`.

## 0.6.3 — 2026-09-04

The board is the interface. Evidence from the text-render pilot (5 tickets, plan 1) drives
every item; each slice is one tag (v0.6.3-s1, -s2, -s3), v0.6.3 on the last.

### Slice 1 — the record is in the repo, and every run ends on a readable page (v0.6.3-s1)

- `scripts/runner.py` — after the verdict the runner commits
  `traces/verdict/<id>.{input.md,json,summary.json,html}` on the ticket branch (`git add -f`:
  projects ignore the html) as `docs(<id>): verdict <decision>`, author `harness-runner`, before
  Gate 2 (E11: four runner-path verdicts vanished with their worktrees). It prints
  `[runner] opik: tracing to <url>` or `opik: untraced (<reason>)` first thing and stamps the
  same line into `meta.opik` (E12). `--summary-model` (default `haiku`, `none` skips).
- `scripts/render_verdict.py` — the Gate 2 page opens with **What was built** (≤3 sentences
  from the close-out Log entry and the diff stat), **What the reviewer said** (decision, held,
  each block and where it belongs, warns; plain language plus the facts), and **Recommended
  action**, computed from the findings and never by a model: open block with `spawn_child` →
  "ship, create child <id>.<n> from F#"; open block without → "rework in place"; each open warn
  → "home to <id>" when another open ticket's `writes:` cover the file the warn names, else
  "waive". The two prose sections are one cheap model call (`claude -p --model haiku --tools ""`)
  cached in `traces/verdict/<id>.summary.json` by input sha; the runner puts it in the Opik
  trace output. Seat line: vendor · seat · cost · tokens in/out · seconds · traced/untraced.
  "Notes" is gone. Close-out sets `ticket` from the packet (E4: a session wrote "1" for 1.1) and
  the default vendor for the skill path is `claude-session`, cost null.
- `scripts/verdict_checks.py` — `validate(verdict, arm, packet)`: every AC and charter item
  the packet showed must be in `held` or cited by a finding, else a C-warn
  `unaccounted: <id>` is appended, once (E6). The one-caller check exempts files new in the
  diff (E5).
- `scripts/verdict_prep.py` — files under `tests/fixtures/` and any file over 2000 diff lines
  are one stat line in the packet (E3). Refuses to build a packet when
  `docs/domain-pack/charter.md` is missing or has no items.
- `scripts/schemas.py` — `VerdictMeta.seconds`, `VerdictMeta.opik`.
- `scripts/vendor.py` — new. The one place a shell model call is run and its JSON envelope read;
  `runner.py` and `render_verdict.py` share it.
- `tests/` — 9 new tests.

### Slice 2 — runner behaves like a runner (v0.6.3-s2)

- `scripts/runner.py` — **branch in place by default**: `ticket/<id>` checked out in the main
  checkout (tree must be clean; original branch restored at the end); `--parallel` keeps a
  worktree under `.worktrees/<id>/`, listed in `.git/info/exclude`, removed by the board's ship
  (E9). **Build skipped** when the ticket is `in_review` and the branch carries the
  `test(<id>)`, `feat(<id>)` and close-out commits: straight to CI + verdict (E1). **The build
  session ends at the close-out**: `--append-system-prompt` says so, the session streams
  (`--output-format stream-json`), and once the status line is committed any
  further tool call is logged on the ticket as `### [runner] … — orbit after close-out: <cmd>`
  (committed; the verdict shows it as a C-warn), the session is terminated, and the runner
  proceeds. Permission denials are fatal (exit 4) only when the close-out was not reached (E2).
  **Phase lines** `[runner hh:mm:ss +m:ss] branch|worktree · ci-pre · build attempt n · tests
  commit · feat commit · build close-out · ci · verdict · close-out`, the builder's text and tool
  calls streamed under them. **Board re-rendered into the main checkout after every phase**,
  from the worktree's kanban under `--parallel` (E7). **Build traced to Opik** per attempt: input
  = ticket + plan ACs, output = commits + CI + close-out/orbit/denials, metadata = attempt, cost,
  seconds per phase. **Gate 2 decision recorded**: `verdict_eval.record_decision` appends or
  replaces the Opik dataset item for the packet with expected = the decision; no model call
  (E10). **`--override backend=<x>|scrutiny=<y>`** restamps the plan, marks the derivation as
  overridden, appends the plan Log `router miss` entry, records the miss in
  `traces/grill-misses.jsonl` with the signals, commits (E8).
- `scripts/kanban_ops.py` — new. The writes a human made by hand: `append_log`, `set_status`,
  `override_plan`, `commit`. The one Log-entry writer; runner and board use it.
- `scripts/render_board.py` — Gate 1 (the plan row) shows the routing stamp and the rule that
  produced it, as the grill wrote them; `main(root, out_path)` renders elsewhere.
- `scripts/schemas.py` — `RoutingStamp.rules`, `.values`, `.rule(field)`.
- `scripts/verdict_checks.py` — `orbit after close-out` Log entries become C-warns.
- `skills/build/SKILL.md` — branch location is the runner's choice; the session ends at the
  status line.
- `tests/fixtures/fake_claude.py` — a stand-in `claude` that plays a stream-json scenario.
- `tests/` — 7 new tests.

### Slice 3 — the board acts (v0.6.3-s3, v0.6.3)

- `scripts/board.py` — new. `board.py serve [--port 8765]` in a project: a stdlib HTTP server,
  localhost only, no auth, refuses a dirty tree. Serves the board with an actions panel and the
  Gate 2 pages (rendered from the JSON on demand, nothing else written). Every action is the
  code path runner and lint already use: **Gate 1** approve (refused without the routing
  stamp; `approved: <who> <date>`, `[human]` Log entry, commit) and override (`kanban_ops.
  override_plan`, as `runner --override`). **Gate 2** ship (refused past an open block that is
  neither waived nor a child; status done, Log entry listing the open findings, commit on the
  ticket branch, dataset item expected=ship, `merge --no-ff` into the current branch, push when
  a remote exists, branch deleted, `.worktrees/<id>/` removed), reject with reason (status
  in_progress, commit, dataset item expected=reject), child from finding (a `<id>.<n>` ticket
  from the finding's text, citation and repro, `depends_on` the parent, the parent's `writes`;
  the finding gets `spawn_child` and `home`; Log entries on both; the ticket's format is
  `templates/ticket.md`), home a warn (`— finding: … home: <id>` entry lint reads), waive with
  reason (`[human] — waive F# by <who>: …`, the entry lint reads; `waived_by` set), rerun.
  **Run** a ticket, or a plan: tickets in `depends_on` order, one runner each, pausing at every
  Gate 2 until the board ships the ticket; the runner's phase lines stream live from
  `traces/runs/<id>.log`. Every Log entry the board writes carries the `[human]` role with
  `who` in the head.
- `scripts/kanban_ops.py` — `find_ticket` no longer matches a child's file for its parent
  (`1.2.1.*.md` is not `1.2`); `verdict_checks` and `runner` resolve tickets through it.
- `scripts/render_verdict.py` — `view(root, tid)`: the page from disk, no stamp, no call.
- `tests/fixtures/project/` — ticket 1.2 in_review with a rejected verdict whose block spawns a
  child; 1.1 is in_review (its verdict rejects). The recommendation and the child action are
  tested end to end on a git copy.
- `tests/test_board.py` — 9 tests; 1 more elsewhere.

Consuming projects (project-template): pin `v0.6.3`. `make board` is now `board.py serve` for
the acting board; `render_board.py` stays for the static page. Add `.worktrees/` to nothing:
the runner keeps it in `.git/info/exclude`. Human `/verdict` sessions still work as before.

## 0.6.2.1 — 2026-09-03

Patch. The headless build session the runner spawns could not run shell commands: it printed
"Need user permission to run shell commands" and "Blocked on git add approval", CI stayed red on
lint, and the runner counted each denial as a ci-red retry until the cap.

- `scripts/runner.py` — the build command is now
  `claude -p "/build <id>" --model <m> --permission-mode acceptEdits --permission-prompts none
  --allowedTools "Bash(make *),Bash(make),Bash(uv *),Bash(git *),Bash(pytest *),Bash(python3 -m pytest *),Bash(python -m pytest *)"
  --output-format json`. `acceptEdits` covers file writes, the allowlist covers make, uv, git and
  pytest, and `--permission-prompts none` (the CLI's headless switch) turns anything else into a
  recorded denial instead of a hang. The runner reads the envelope's `permission_denials`; when
  it is non-empty it prints `permission denied: <tool(command)>` and exits 4 at once. No retry,
  no "ci red". `BUILD_ALLOWED_TOOLS` is the one list.
- `tests/` — a fake `claude` on PATH: a denial stops the runner with exit 4 after one attempt;
  a clean build with red CI still retries to the cap.

Consuming projects (project-template): `.claude/settings.json` gains the same allowlist under
`permissions.allow`, so a human `/build` in the worktree does not prompt either. Pin `v0.6.2.1`.

## 0.6.2 — 2026-09-03

The verdict packet is the seam: which vendor renders the verdict, and how much context it sees,
are two runner flags, and every call is one Opik trace, so verdicts across seats compare.

- `skills/verdict/verdict-prompt.md` — new. The judging prompt, extracted from `SKILL.md`, with
  its guardrail line (fix nothing; the reply is the verdict JSON and nothing else; only the
  human `/verdict` flow touches this ticket's Log).
  `SKILL.md` is a thin wrapper with two flows: a packet path (runner seat) ends at the verdict
  JSON, no close-out, no Log edit, no summary; a ticket id (human) preps, judges, closes out.
- `scripts/verdict_prep.py` — the packet is self-contained: frontmatter (`ticket`, `arm`,
  `base`, `output`, `ticket_file`, `status`, `writes`, `plugin_version`, `prompt_sha`), the
  prompt as `## Instructions`, a `## Seat` line, then the evidence sections. `--arm
  blind|packet|repo`, default `packet` (today's content). `blind` = Instructions, Seat, CI,
  Diff stat, Diff; the frontmatter drops `ticket_file`, `status`, `writes`. `repo` = packet plus
  a seat line allowing read-only reads of the tree.
- `scripts/schemas.py` — `Packet` (frontmatter + ordered sections; `rearm()` narrows a packet,
  `render()` writes it), `Verdict` and `VerdictMeta` (the JSON, its optional `meta` stamp).
  `runner.py`, `render_verdict.py`, `lint_kanban.py` and `verdict_prep.py` all load the verdict
  through `Verdict`; the ad-hoc parsing promised away in 0.4.2 is gone. Verdicts without
  `meta` still load.
- `scripts/verdict_checks.py` — each ticket AC that no test added on the branch names is a
  C-block, one per AC (`no test names AC-n`, `ac` cited); it was one warn.
  `validate(verdict, arm)`: a block without an `ac` or `charter` citation is a violation, except under `blind`, where it is downgraded to a warn and the
  decision re-derived by the prompt's own rule (reject iff an open block or CI red).
- `scripts/render_verdict.py` — the close-out: validate for the packet's arm, stamp `meta`
  (`arm`, `vendor`, `plugin_version`, `prompt_sha` = sha256 of `verdict-prompt.md`,
  `packet_sha` = sha256 of the packet file), write the JSON back, render the page with a Seat
  line and any Invalid line. `--vendor` (default `claude`). Idempotent.
- `scripts/runner.py` — `--arm` and `--verdict-cmd "<template>"`. The runner writes the packet,
  runs the template in the worktree with `{packet}`, `{output}`, `{model}`, `{ticket}` filled,
  then closes out with the template's executable name as vendor. Default template:
  `claude -p --model {model} --output-format json --tools "" < {packet}`: the packet arrives on
  stdin (never as an argument: a packet starts with `---`), no tools, and the reply is the
  verdict, which the runner takes from the report's `result` field and writes to `{output}`.
  `runner.py` and `verdict_eval.py` never invoke `/verdict`; that is the human path. A non-zero
  exit, an empty result, or JSON that fails the `Verdict` schema (`Verdict.problems()`: decision,
  finding ids, severities, statuses) is an error: logged, traced as the output's `error` with the
  reason, the CLI envelope (`stop_reason`, `num_turns`, `permission_denials`, `is_error`) and the
  stderr tail, treated as a reject to retry. The prompt no longer asks the model to write a file:
  the reply is the JSON, the harness files it. When the report carries `total_cost_usd` and
  `usage`, they are stamped as `meta.cost_usd` / `meta.tokens`; other vendors leave them null.
  Decision logic unchanged.
- Opik trace, in `runner.py` — gated on `OPIK_URL_OVERRIDE` being set and the `opik` package
  importing (`OPIK_API_KEY` alone is not a signal). The one model call is one trace: input =
  packet text, output = stamped verdict (or the error envelope), metadata = stamp + cost + ticket
  + wall seconds. Otherwise nothing is traced and nothing else changes.
- `scripts/verdict_eval.py` — new. Loads a project's `traces/verdict/*.input.md` into an Opik
  dataset, items keyed on the packet sha so a rerun replaces and never duplicates, and items
  whose packet is gone are deleted (expected = the stamped verdict's decision when its
  `packet_sha` matches, else the ticket Log's last `[verdict] — ship|reject`, else null) and runs
  one arm × one verdict command as an experiment. Code metrics only: `block_count`,
  `finding_count`, `citation_compliance`, `decision_agreement`, `wall_seconds`. An errored run
  (non-zero exit, empty result, invalid verdict) scores as failed, stays out of every average,
  and is counted in the summary line.
- `scripts/verdict_canned.py` — new. A verdict command that copies a prepared JSON to
  `{output}`: the seat end to end with zero model tokens.
- `tests/fixtures/project/` — new. A neutral project (fixture ticket 1.1 and plan 1, one
  0.6.2-format packet rendered via `Packet`, a stamped reject verdict with a cited AC-2 block) that
  `verdict_eval.py` runs over. Without Opik the eval scores locally and prints one line per
  item; CI runs it with the canned command and blocks the network.
- `Makefile` — `make ci` for this repo (unittest discover).
- `tests/` — 42 new tests (arms, packet round trip, stamp, validate, cmd template, stdin packet,
  vendor errors, opik absent and configured, eval dataset sync and metrics, fixture smoke).

Consuming projects (project-template): pin `v0.6.2`. Nothing in `make verdict` changes for the
human path. Projects that call `runner.py` get the packet seat by default. `verdict_eval.py`
and Opik tracing need `opik>=2.2` in the project's environment plus `OPIK_URL_OVERRIDE`; the
plugin does not pin it. Verdict JSONs written before 0.6.2 render as "Seat: unstamped".

## 0.6.1 — 2026-09-03

Housekeeping. The plugin carries no use-case vocabulary, and one dead script is gone.

- `tests/fixtures/kanban/` — fixtures describe a neutral document-processing pipeline
  (transform, export, retry, reference set). Ticket ids, AC tags, `depends_on`, `writes:` and
  Log entries are unchanged, so every test triggers the same rules and expects the same
  violations. Renamed: `1.3.transform-stage.md`, `1.5.export-stage.md`, `1.7.retry-pass.md`.
- `scripts/render_plan.py` — deleted. Gate 1 is the plan file plus the board; nothing called it.
  `AGENTS.md` examples now cite `render_board.py`.
- `README.md` — script table matches `scripts/`; notes that `scripts/` is the runtime the skills
  and project Makefiles call.

## 0.6.0 — 2026-09-03

Three kanban invariants that were sentences in skill files are now checks. Findings drifted
between tickets without a written home, a ticket shipped past a block nobody waived, and plans
were approved without the routing stamp the build skill keys on.

- `scripts/lint_kanban.py` — three new rules beside the closed-ticket check, same reporting
  style, one line per violation. **Finding homes:** every `— finding:` Log entry carries an
  explicit `home: <id>` / `homed to <id>` marker naming a ticket file, or a `[human]` waiver or
  build close-out names its title. Bare ticket ids in the text do not count; the violation
  lists them as candidate homes. Homed to an id with no file = violation;
  a done|superseded ticket whose Log never mentions a finding homed to it (title or source id)
  = violation. **No ship past open block:** a ticket whose `traces/verdict/<id>.json` has an
  open block may not carry a `[verdict] … — ship` entry or `status: done` unless a `[human]`
  entry names the finding id. **Routing stamp:** an approved plan carries `signals` (spend,
  partner_facing, parallel_ready, tickets), `scrutiny` and `backend`.
- `scripts/schemas.py` — `Log`, `LogEntry`, `Finding`, `RoutingStamp`; the lint loads through
  them. `lint()` keeps its signature, so `verdict_checks.py` reports the new rules as warns.
- `tests/` — 20 new tests; the fixture plans now carry a routing stamp.

Consuming projects: approved plans without a routing stamp fail `lint_kanban.py` — stamp them
by hand (the grill stamps new ones). Findings already in Logs need a `home: <id>` line or a
waiver.

## 0.5.1 — 2026-09-02

The first 0.5.0 verdict shipped a ticket that two earlier verdicts had rejected: tests made 20
live calls to a provider when a key was present. Two gaps, both mechanical now.

- `scripts/verdict_prep.py` — archives the existing `<id>.json` as `<id>.prev.json` before the
  run, so the previous verdict's blocks always reach the input with their repro commands.
  `render_verdict.py` no longer archives (it ran after the write and lost the old verdict).
- `scripts/verdict_checks.py` — live-call check: runs the test suite with every key named in
  `.env.example` set to a dummy value and outbound sockets blocked (`scripts/nosock.py`, a
  pytest plugin); any connection attempt is a block naming the host. Verified on the real
  branch: 20 attempts caught.

## 0.5.0 — 2026-09-02

Single-call verdict. The old skill explored a frontier over 14–23 model calls, each re-reading
140k tokens of context and thinking 2k before acting: 12 minutes per verdict, 80% of output
tokens thinking. Now scripts gather and check, the model judges once.

- `scripts/verdict_prep.py` — new. Writes `traces/verdict/<id>.input.md`: ACs, Out of scope,
  `writes:`, human waivers, the plan's "Verdict must attack" items, charter items, previous
  blocks with repro commands, `make ci` result, mechanical findings, tests added on the branch
  that name an AC, the diff. ~4 s, ~20k tokens on a real branch.
- `scripts/verdict_checks.py` — new. Findings with zero model tokens: writes outside `writes:`
  (block), CI red (block), ACs no added test names, new defs with ≤1 caller, new names missing
  from the glossary, closed tickets edited.
- `scripts/render_verdict.py` — archives `<id>.json` as `<id>.prev.json`, so retry mode is real.
- `scripts/schemas.py` — `Attacks`, `Charter`, `Ticket` models; the scripts load through them.
- `skills/verdict/SKILL.md` — three tool calls: prep, write JSON, close out. Blocks and warns
  only, 20-word texts, `held` labels for what holds, no notes. Status: ship → done,
  reject → in_progress.
- `tests/` — 7 new tests.

Consuming projects: nothing to change; `make verdict` and `runner.py` are unchanged.

## 0.4.2 — 2026-09-02

Verdict cost. Six verdict runs measured 6–22 minutes, tracking output tokens (63k–192k) and
context (110k–200k); the one retry cost three times a first run.

- `skills/verdict/SKILL.md` — Phase A has no attack quota: attack the frontier until empty.
  Attacks that hold go to a top-level `held` array instead of note findings. Retry mode
  re-verifies only blocks (re-running `repro`; a resolved block that fails is open again) and
  warns or notes whose cited file is in the new diff; everything else carries forward as-is.
  Ticket status after a verdict is fixed: `ship` → `done`, `reject` → `in_progress`.
- `scripts/render_verdict.py` — renders `held`.

Not a schema yet: the verdict JSON is still parsed ad hoc by `render_verdict.py` and
`runner.py`. Next time it changes shape it gets a model in `scripts/schemas.py`.

## 0.4.1 — 2026-09-02

Closed tickets are immutable. Kanban practice, and the property an issue tracker gives for
free that markdown files do not.

- `scripts/schemas.py` — `CLOSED_STATUSES` (done, superseded), `APPEND_ONLY_SECTIONS`
  (Findings, Log), and `ClosedTicketDiff`: what changed on a closed ticket between two versions.
- `hooks/guard_writes.py` — refuses any Write/Edit to a ticket whose `status:` is closed, whether
  or not a ticket is active. Message names the fix: a new ticket with `depends_on`, or a human
  reopening by hand. Scope rule unchanged.
- `scripts/lint_kanban.py` — new. Fails when a ticket that was closed at `<base>` differs now
  outside its append-only sections, or was deleted. Default base `HEAD`; pass the merge base in
  CI. Catches edits made outside a Claude session.
- `scripts/_fm.py` — `parse(text)` split out of `read(path)`.
- `tests/` — 13 new tests for both.

Consuming projects: add `python3 $(SCRIPTS)/lint_kanban.py origin/main` to `make ci`.

## 0.4.0 — 2026-09-02

Brief: `kanban/briefs/3-board-progress.md` — the board refreshes itself and shows how much of
each plan is built.

- `hooks/hooks.json` — the `Stop` hook now re-renders `traces/board.html` after every agent
  turn (second command after `trace_stop.py`). No-op when the project has no `kanban/`.
- `scripts/render_board.py` — new **Progress** section above **Tickets**: one row per plan
  (`Plan <n> — ACs reached k/total · slices built/total · tickets done, in_review, ready,
  missing`), a two-shade bar (done vs in_review), and one line per plan AC listing the tickets
  that cite it and the best status. `superseded` added to the status columns.
- `scripts/schemas.py` — new. `Plan` (AC ids + `## Slices` ids) and `TicketCitations`
  (`(plan <n> AC-x, AC-y)` groups inside ticket AC lines). The renderer loads through them.
- `tests/` — synthetic fixture (two plans, eight tickets) plus hook exit-code checks.
  Run `python3 -m unittest discover -s tests`.

Decisions recorded:

- An AC cited by an `in_review` ticket counts as reached; the bar shades it apart from `done`.
- A plan AC amended by a reopened decision counts by the ticket's citation as written
  (`AC-7 as amended by R-2` reaches AC-7).
- Only citations inside a ticket's `## Acceptance criteria` lines count; prose, findings, and
  log mentions do not. Any `(… plan <n> AC-x …)` group inside an AC line counts, including
  `(unblocks plan <n> AC-x)`.
- A slice is "built" when its ticket is `done` or `in_review`; `in_progress` and `superseded`
  counts appear in the row only when non-zero.
- Findings count per ticket on the board: out of scope, not done.

Consuming projects: delete the project-local `Stop` hook in `.claude/settings.json` that ran
`make board` as a stopgap — the plugin does it now. Pin to `v0.4.0`.

## Backlog (from network pilot, 2026-09-03)

- Build guard: refuse `/build` when the working tree is dirty with files outside the ticket's `writes:` (pilot: six kanban files merged past a build unnoticed).
- `writes:` validation: at close-out, diff the branch's touched paths against `writes:` and fail on unlisted paths (pilot: 1.1's `writes:` missed two of its own packages).
- Gate rule: charter and ADR edits require a `[human]` approval line before commit (pilot: charter amended from a question, pre-gate, commit 8f5431b).
- Verdict coverage: every AC and charter item in the packet must appear in `held` or in a finding's `ac`/`charter`; anything else becomes a C-warn "unaccounted: AC-n" at close-out (0.6.2 left silence on an item indistinguishable from a pass).
