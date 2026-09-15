# kanban

- `briefs/<n>-<slug>.md` — you write these (template: the plugin's `templates/brief.md`).
  "Success looks like" / "Not this" are optional; the grill turns them into an AC and a verdict attack.
  `spec_refs:` names the `docs/spec/index.md` ids the brief covers; `after:` the briefs shipped first.
- `plans/<n>.plan.md` — the grill writes; you approve by adding `approved: <name> <date>`.
  That line is the gate, not `status:` — build refuses a plan without it.
- `tickets/<n>.<m>.<slug>.md` — one vertical slice. `status:` in frontmatter, `## Log`
  append-only, role-tagged.
- `tickets/<n>.<m>.<p>.<slug>.md` — children spawned by a rejected verdict. No plan gate.
- `.active` — current ticket id; the write-guard hook reads it. Git-ignored.
- `.issues` — opts this project into GitHub Issues for tickets: `owner/repo`, written by
  `init --issues owner/repo`. Present: `tickets/` above is a read-only mirror `make sync` writes
  from the repo's `ticket`-labelled Issues, git-ignored, never edited by hand. Absent (the
  default): tickets are the files above, as today.

Subfolders, not a flat directory: the scripts search `kanban/` recursively, so nesting is free
and `ls kanban/` stays readable as briefs accumulate.
