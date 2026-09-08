# kanban

- `briefs/<n>-<slug>.md` — you write these (template in the plugin).
  "Success looks like" / "Not this" are optional; the grill turns them into an AC and a verdict attack.
- `plans/<n>.plan.md` — the grill writes; you approve by adding `approved: <name> <date>`.
  That line is the gate, not `status:` — build refuses a plan without it.
- `tickets/<n>.<m>.<slug>.md` — one vertical slice. `status:` in frontmatter, `## Log`
  append-only, role-tagged.
- `tickets/<n>.<m>.<p>.<slug>.md` — children spawned by a rejected verdict. No plan gate.
- `.active` — current ticket id; the write-guard hook reads it. Git-ignored.

Subfolders, not a flat directory: the scripts search `kanban/` recursively, so nesting is free
and `ls kanban/` stays readable as briefs accumulate.
