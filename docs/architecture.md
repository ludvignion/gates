# Architecture

## The light lane

`/gates:grill <n>` routes a brief through `scripts/triage.py` before it asks a single question.
`triage.py` is a pure function of the brief text, `docs/domain-pack/charter.md` and
`docs/domain-pack/interfaces.md` — zero tokens, no model call, no network call. Four checks, any
one of which routes the brief `full` (the grill runs as today: tree, questions, plan page, Gate 1):

1. **charter glob** — a named path reaches a `docs/domain-pack/charter.md` `Applies to:` glob.
2. **interface glob** — a named path reaches a `docs/domain-pack/interfaces.md` `Applies to:` glob.
3. **package, URL or env var** — the brief names a package manifest or install command, a URL, or
   an environment variable.
4. **partner-facing** — the brief's frontmatter carries `partner_facing: true`.

All four false, with at least one named path, routes `light`: no plan page, no Gate 1, one ticket
carrying `lane: light`.

A `lane: light` ticket still runs tests, ci and a verdict like any other ticket, but the runner
adds one more gate of its own: after `feat-commit`, it counts insertions plus deletions against
the base branch, scoped to the ticket's `writes:`. At or under **150** changed lines the run
continues to `ci` exactly as today. Over 150, the runner stops before ci — no verdict seat is
called — deletes the ticket branch, and supersedes the ticket with a Log entry naming
`/gates:grill <n>` to re-route the brief through the full lane.

Every light ticket that ends, by ship or over budget, appends one JSON line to
`traces/lanes.jsonl`: the ticket id, the lane, the seconds from the brief's first commit, the
human touches counted, the changed-line count, and whether the budget fired. No board or page
renders this file — a human reads it directly.
