# {{project_name}}

<!-- FILL IN: what this repo builds, for whom -->

Workflow comes from harness-plugin, pinned in `.claude/settings.json`. `make plugin` refreshes
the installed plugin; `/harness-plugin:init --update` moves the pin and the template files.

## Before the first build

Fill `docs/domain-pack/`, charter included — every verdict cites the charter. Edit the first
paragraph of `AGENTS.md`.

## Work

From a spec: put it in `docs/spec/` (one file, markdown with headings or CSV with an `id`
column; convert pdf, docx, xlsx outside first), `make intake F=docs/spec/<file>`, decompose it in
a chat with the plugin's `templates/decompose.md`, paste the briefs in, `make coverage` green.
A changed requirement is `drift` until the citing brief is reviewed and committed with the new index.

1. Write `kanban/briefs/<n>-<slug>.md` (template: the plugin's `templates/brief.md`).
2. `/grill <n>`; say "approve" in that session (the grill runs `kanban_ops.py approve <n>`),
   or `make approve N=<n>` from a terminal.
3. `/harness-plugin:runner <n>.<m>` or `/harness-plugin:runner plan <n>` in a Claude session:
   it starts the runner, relays the phases, prints the result block, and takes Gate 2 in words
   (`ship`, `reject: <reason>`, `child from F#`, `home F# to <id>`, `waive F#: <reason>`).
   Watch `traces/board.html` meanwhile; it refreshes itself while a run is on.
4. Terminal equivalents: `make run T=<n>.<m>` or `make run P=<n>`, then `make gate A="ship <id>"`.

## Tracing

Copy `.env.example` to `.env` and set `OPIK_URL_OVERRIDE` (local server or an explicit cloud
URL). The runner loads `.env` itself (a session start too); `make run` refuses to start while
it is empty. Untraced runs are opt-in only: `OPIK_DISABLE=1 make run T=<id>`. The runner's
first line says `opik: tracing to <url>` or `opik: untraced (<reason>)`.
