# <project>

<One paragraph: what this repo builds, for whom. Replace.>

Workflow comes from **harness-plugin** (pinned in `.claude/settings.json`). Do not copy skills into this repo.

# Where things live

| Path | Role |
|---|---|
| `kanban/briefs/` | human-written briefs |
| `kanban/plans/<n>.plan.md` | grill write-back; gate 1 in `approved:`, routing in `signals/scrutiny/backend` |
| `kanban/tickets/<n>.<m>.<slug>.md` | tickets; role-tagged append-only `## Log` |
| `docs/glossary.md` | one canonical name per concept; update in the same PR that introduces it |
| `docs/adr/` | numbered, append-only decisions (template in the plugin) |
| `docs/domain-pack/` | everything domain-specific: charter, conventions, references, spend rules |
| `traces/` | grill records, verdicts, plan pages, board, blind-spots. Machine-written. |
| `src/{{project_name}}/` | product code. |
| `tests/` | mirrors `src/`. `tests/evals/` only when `src/` calls a model; never in `make ci`. |

# Commands

`make help`. Core: `install · ci · board · approve N= · run T=|P= · gate A= · verdict T= · digest · replay`. From a session: `/harness-plugin:runner`.

# Invariants agents can't infer

- **Tests before code, in their own commit.**
  Good: `test(1.2): ACs as tests` then `feat(1.2): ...`. Bad: one commit with both.
- **CI is the only authority on green.**
  Good: "make ci: 41 passed". Bad: "tests should pass now".
- **The slice does what the slice does.**
  Good: `### [build] — finding: off-by-one in src/{{project_name}}/other.py, not touched`. Bad: fixing it "while here".
- **No helper before three call sites.**
  Good: two similar 8-line blocks. Bad: `utils.py` with one caller.
- **Every non-trivial addition traces to an AC at close-out.**
  Good: `src/{{project_name}}/x/reconcile.py → AC-2`. Bad: "added for future flexibility".
- **Domain rules live in `docs/domain-pack/`, never in skills or AGENTS.md.**
  Good: charter item cited by path in a verdict. Bad: a client name in a SKILL.md.
- **The plan's routing stamp decides the process, not your caution.**
  Good: `scrutiny: light` → build on a branch, human reads the diff, no per-ticket verdict.
  Bad: "recommend a verdict first to be safe" on a light plan.
- **Always-writable in any ticket: `docs/glossary.md` and the parent plan's `## Log`.**
  Everything else needs `writes:` or a logged widening.
  Good: glossary term added in the same PR, no `writes:` entry needed.
  Bad: a verdict warn for "glossary written outside `writes:`".
- **Report the terminal state and stop.**
  Good: "merged, main at ccb979b". Bad: "Done. Your move — A/B/C" after a completed task.
  New work enters only as a brief in `kanban/briefs/` or a `finding:` in a ticket Log.

# Principles

Remove instructions over adding. Boring beats elegant unless an AC says otherwise.
A new rule here needs a why plus a good and a bad example, like the ones above.
Add import-linter with a real contract when `src/` has two or more packages.
