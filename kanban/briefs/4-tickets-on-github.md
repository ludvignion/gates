# Brief: tickets live in GitHub Issues, plans and traces stay in the repo

## What I want
Tickets move from `kanban/tickets/*.md` to GitHub Issues so a second person can read, comment
on, and pick up work without a Claude session or a local checkout. Plans, briefs, gate pages and
traces stay as files in the repo. The four skills, the two hooks and the scripts keep working;
they read and write tickets through `gh` instead of the filesystem.

## Why it matters
A collaborator is joining. Today the only way to follow or touch a ticket is to open a markdown
file in the repo, and the only thing stopping anyone from rewriting a closed ticket is a hook
that runs inside Claude sessions (plugin 0.4.1). An issue tracker gives both for free: a
readable board, comments that append, closed issues that stay closed, and a history of edits.

## What I already know / have decided
- The ticket shape today is frontmatter (`id`, `parent`, `status`, `depends_on`, `writes`) plus
  sections: Outcome, Acceptance criteria, Out of scope, Findings (append-only), Log (append-only).
  Everything the harness relies on is in `scripts/schemas.py`: `Plan`, `TicketCitations`,
  `ClosedTicketDiff`, `CLOSED_STATUSES`. The issue body has to carry the same shape, loaded
  through one model; no second parser.
- Statuses become labels: `ready`, `in_progress`, `in_review`, `done`, `superseded`. `done` and
  `superseded` close the issue. Matt Pocock's `to-tickets` and `triage` skills use the same
  device (`ready-for-agent`, `ready-for-human`, `wontfix`).
- Readers of ticket files, all of which change: `hooks/guard_writes.py` (`writes:` of the active
  ticket), `hooks/reanchor.sh` (active ticket on compaction), `scripts/runner.py` (status line in
  the Log, worktree per ticket), `scripts/render_board.py` (columns, progress, dependency graph),
  `scripts/render_verdict.py` (ticket text on the gate-2 page), `scripts/lint_kanban.py` (becomes
  moot: GitHub keeps edit history), `skills/grill` (writes tickets), `skills/build` and
  `skills/verdict` (read ticket, append Log, set status).
- `kanban/.active` stays a file; its content becomes an issue number. Worktree and branch names
  stay `ticket/<id>`; `<id>` is the issue number, so the plan's slice list cites issue numbers
  once the issues exist.
- The plan gate does not move. `kanban/plans/<n>.plan.md` with its `approved:` line is still gate
  1, rendered locally. Briefs stay in `kanban/briefs/`. Traces stay in `traces/`.
- Findings and Log entries become issue comments. A comment is append-only by construction, which
  is the property 0.4.1 had to bolt on.
- The Dockerfile image has `git` and `curl` but not `gh`; it gets added. The runner
  needs a token with issues scope; the hooks run with the user's `gh auth`.

## What I'm unsure about
- Whether the harness should keep a read-only mirror of each issue under `kanban/tickets/` so the
  board, the verdict page and offline sessions keep working without network. Suggest yes, written
  by a `make sync` and never edited by hand; the guard refuses writes to it.
- Which fields go in the issue body versus labels versus a GitHub Projects custom field:
  `parent` and `depends_on` in the body (a `Blocked by` line, Pocock's shape), `writes` in the
  body, status as a label. Suggest body for everything but status, so one model parses one text.
- Whether the AC citations `(plan <n> AC-<x>)` survive as-is. Suggest yes; the parser already
  works on text and issue bodies are text.
- Whether GitHub Projects replaces `traces/board.html` or the board renders from issues. Suggest
  the board renders from issues so the progress-per-plan view survives; Projects is optional.
- Migration of existing tickets: one issue per file, same numbering lost. Suggest a one-off
  script that opens the issues, closes the done ones, and rewrites the plans' slice lists.

## Success looks like
The collaborator opens the repo's Issues tab, sees the tickets with their status labels, reads
one, leaves a comment, and picks up a `ready` one by creating the `ticket/<n>` branch. The next
Claude session on that ticket sees the comment in the Log without anyone copying it.

## Not this
- A ticket that exists in two editable places. The file and the issue drift within a day.
- Status kept in the issue body as text. The label is the status; a body that says otherwise is
  a bug the lint catches.
- A closed issue reopened by an agent to change an acceptance criterion. Reopen is a human click.
- Tickets moved but the plan gate moved with them into an issue. The approved plan is a commit.
