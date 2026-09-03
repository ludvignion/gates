# Changelog

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
