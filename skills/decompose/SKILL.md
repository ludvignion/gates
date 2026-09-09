---
name: decompose
description: >-
  /gates:decompose [first brief number]. Turn docs/spec/index.md and its spec into
  briefs: round 1 is one table (brief, outcome, spec_refs, after) plus deferred rows with every
  id exactly once; the human ranks; round 2 writes kanban/briefs/<n>-<slug>.md and
  docs/spec/deferred.md and makes `make coverage` green. Use whenever the user says decompose,
  has a spec, or wants briefs from an index. Never edits the spec, never renumbers a brief.
disable-model-invocation: true
argument-hint: "[first brief number]"
---
# Decompose

Input: `docs/spec/index.md` (from `make intake F=docs/spec/<file>`) and the spec file its
`spec:` names. Output: one brief per table row under `kanban/briefs/`, `docs/spec/deferred.md`,
and `make coverage` printing `0 violation(s)`.

1. Read `${CLAUDE_PLUGIN_ROOT}/templates/decompose.md`, then `docs/spec/index.md`, then the
   spec. No index: print `Next: make intake F=docs/spec/<file>` and stop.
2. Round 1 as the template says: the two tables, every index id exactly once, letters not
   numbers. Print them and end the turn; the human ranks. `go` alone means the table's order;
   `go, order: C, A, B` is the human's.
3. Round 2 as the template says: one file per row in the brief shape, numbered from the
   argument or the next free number in `kanban/briefs/`; `after` as brief numbers; one
   `- <id> — <reason>` line per deferred id. Existing briefs keep their numbers. The spec file
   is never edited.
4. Run `make coverage`. A red line names an id and a brief: fix the brief or the deferred file,
   never the index, until the last line is `coverage: 0 violation(s) over <n> units`. Print it.

Next: /gates:grill <first brief number>
