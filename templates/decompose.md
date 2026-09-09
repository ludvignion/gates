# Decompose a spec into briefs

`/harness-plugin:decompose` runs this in a Claude session inside the repo: it reads the spec and
`docs/spec/index.md`, writes the files in round 2, and runs `make coverage`. It also works as a
plain prompt in any chat: paste this file, the spec, then the index; paste the files back. The
model does the judgment; the repo checks the result.

## What I want from you

A decomposition of the indexed spec into briefs for the harness. Two rounds.

**Round 1: one table, nothing else.**

| brief | outcome | spec_refs | after |
|---|---|---|---|

plus one row per deferred id, in a second table:

| deferred | reason | ids |
|---|---|---|

Rules for the table:

1. Every id in the index appears exactly once across both tables: in one brief's `spec_refs`
   or in one deferred row. Never twice, never missing. Count them before you answer.
2. `outcome` is what a user can do when the brief ships, in one sentence a tester could try.
   The grill refuses this shape, verbatim from its rules: "Refuse a brief whose Outcome is a
   code property — validation, typing, models, a refactor, 'add X across stages' — rather than
   a behaviour observable in the CLI or an output file. That is a horizontal phase." So no
   "set up the data model", no "add validation": name the behaviour those would enable.
3. The first row is the tracer bullet: the thinnest brief that crosses every layer the product
   touches and produces something a user can see.
4. `after` lists every brief that must ship first. When the dependencies are unclear, list all
   candidates; the human strikes the ones that are not real when ranking.
5. Defer an id only when no user-observable outcome can cite it: narrative, definitions,
   figures, strategy, roadmap. The reason names which. A unit that describes a behaviour,
   implemented or proposed, goes in a brief, even when the rest of that unit is description.
6. Do not number the briefs yet; letter the rows (A, B, C).

**Round 2: after the human ranks the table** (`go` keeps the table's order; `go, order: C, A, B`
sets it), one file per row in the shape below, numbered from the number the human gave or the
next free number in `kanban/briefs/` (existing briefs are never renumbered), named
`kanban/briefs/<n>-<slug>.md`, and one `docs/spec/deferred.md` with one line per deferred id:
`- <id> — <reason>`. Frontmatter `spec_refs` and `after` carry the table's columns as lists;
`after` names brief numbers, not letters. In a session, write the files and run `make coverage`;
in a chat, emit each file's content under its path.

## Brief shape (templates/brief.md)

```
---
spec_refs: [<id>, <id>]
after: [<n>]
---

# Brief: <task name>

## What I want
<1–3 sentences. Output, not process.>

## Why it matters
<1–3 sentences.>

## What I already know / have decided
<Systems involved, constraints, prior decisions, related tickets. Quote the cited units.>

## What I'm unsure about
<Optional. Where the grill should push.>

## Success looks like
<what the recipient does with the result the first time they see it>

## Not this
<the failure that would look like success>
```

## Not this

A roadmap, epics, phases, a data-model brief, a brief with an empty `spec_refs`, an id in two
rows, an id in no row.
