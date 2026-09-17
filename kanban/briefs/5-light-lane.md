# Brief: Light lane for small briefs

## What I want

A brief that passes triage becomes one ticket directly, with no grill, no plan page and no Gate 1, and runs through the runner as today. A light ticket that lands over the diff budget is stopped and sent to grill. Gate 2 for a light ticket shows the result block only.

## Why it matters

Five plans in real use. Small features pay the same floor as large ones: grill, Gate 1, full Gate 2 page, three human touches per ticket. Gate 1 has had zero human edits across those five plans. The floor is what makes gates feel heavy. Small work should cost one touch and still leave a packet and a verdict behind.

## What I already know / have decided

- Triage is a script over the brief. Zero tokens, same input gives the same route. Four checks, any true routes to the full lane:
  1. A path named in the brief matches a charter `Applies to:` glob.
  2. A path named in the brief matches an interface or schema glob list in project config.
  3. The writes scope includes the package manifest or lockfile, or the brief names a package, URL or env var.
  4. The brief or the project is flagged partner facing.
- A brief that names no paths goes to the full lane. A light ticket needs a writes scope for `guard_writes.py`.
- "Needs more than one ticket" is not a triage check. The diff budget catches it after the build. Count how often it fires; add a model call only if the rate is high.
- No model call in triage in this version. A miss on check 2 is accepted and left to the verdict.
- The light ticket is created through `kanban_ops.py`, the one door. ACs come from "What I want". Writes scope comes from the paths named. Frontmatter carries `lane: light`. Full lane tickets carry `lane: full`.
- Runner: after `feat-commit`, if `lane: light` and the diff is over the budget, stop with `done error light over budget`, drop the branch, set the ticket back to brief. Grill is not started by the runner.
- Gate 2 for a light ticket: the result block only. Same five words, same `kanban_ops.py` calls. The full page is still written to `traces/verdict/` for the record.
- Full lane is unchanged. Routing is computed from signals, never asked.
- Measurement per light ticket: wall clock from brief to ship, human touches, and whether it hit the budget. Compared against the last plan's tickets.

## What I'm unsure about

- Where triage lives: `scripts/triage.py` behind a `/gates:light` skill, or the first step of a brief entering `kanban_ops.py`.
- Whether the light diff budget is the existing warn threshold or a lower one.
- Whether `render_verdict.py` gets a light flag or Gate 2 just reads the existing result block file.
- Whether `guard_writes.py` needs any change. Probably not, since the writes scope exists.
- Whether the interface and schema glob list belongs in the domain pack or in a project config file.