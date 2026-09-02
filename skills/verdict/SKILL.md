---
name: verdict
description: >-
  Fresh-context review of one ticket's diff against its ACs and the charter: adversarial pass
  first, quality pass second, one HTML verdict page the human reads as the ship gate. Use whenever
  the user says verdict, review, or a ticket reaches in_review. Must run in a session that did not
  implement the ticket.
disable-model-invocation: true
argument-hint: "<ticket id>"
---
# Verdict
Input: ticket `<id>`, branch `ticket/<id>`, the diff vs its base, `make ci` output, and
`traces/verdict/<id>.prev.json` if present (the previous verdict on this ticket).
Output: `traces/verdict/<id>.json` → `render_verdict.py` → `traces/verdict/<id>.html`. Gate 2.
You see the ticket and the diff. You do not see the implementer's reasoning. Keep it that way.
## Scope — read before Phase A
You review this ticket against its ACs, its Out of scope, its `writes:`, and the charter items
reachable in this diff. Nothing else can block.
- `block` — an AC of this ticket is unmet; a committed test drifts from its AC; a write lands
  outside `writes:`; a charter item is violated by behaviour reachable in this diff. Every block
  cites `ac` or `charter`. No citation, no block.
- `warn` — a Phase B question answered no, or a charter item reachable here that holds only by
  accident. Never changes `decision`.
- `note` — reachable only after a later ticket, or belonging to another ticket. Set `home` to
  that ticket id. Never changes `decision`. What the ticket's Out of scope waives is a note.
You write to this ticket and `traces/verdict/` only. `home` carries cross-ticket items; the human
propagates them at Gate 2.
## Retry mode
If `<id>.prev.json` exists: verify every entry in it against the new diff first, and carry it
forward with the same `id` and `status: resolved|open`. Only then add new entries — new blocks
only under the Scope rule. A waiver is a `### [human]` Log entry; put its timestamp in
`waived_by`, do not restate it.
## Phase A — adversarial (first, in full)
Generate ≥10 attack scenarios against the diff and tests, in this order:
1. every item under the plan's "## Verdict must attack" (mandatory);
2. each AC, attempted to break;
3. charter items reachable in this diff;
4. uncovered edge cases · scale · hidden assumptions · partial failure · ordering ·
   data loss / duplication / silent coercion · retry safety.
Each attack is one entry. An attack that holds is a `note` with `covered_by` set.
## Phase B — quality
Per file touched, yes/no:
1. Describable in one sentence without "and"?
2. Every non-trivial addition traces to an AC? (walk the diff; list leftovers)
3. Any abstraction, knob, or flexibility no AC asked for?
4. Any helper with fewer than three call sites?
5. Any caller using less than half the interface it depends on?
6. Any dependency on another module's internals?
Then compare committed tests against the ACs verbatim. Drift = block, citing the AC.
Then charter conformance from `docs/domain-pack/`, item by item, or waiver by Log reference.
## Output
Write `traces/verdict/<id>.json`:
```json
{"ticket": "<id>", "decision": "ship|reject",
 "findings": [
   {"id": "F1", "severity": "block|warn|note", "status": "open|resolved",
    "ac": "AC-3", "charter": null, "home": null, "spawn_child": false,
    "covered_by": null, "repro": "one command or null", "waived_by": null,
    "text": "≤40 words: what breaks, where (file:line)."}
 ],
 "quality": {"file": {"q1": true, "q2": true, "q3": true, "q4": true, "q5": true, "q6": true}},
 "ci": {"green": true, "mutation_score": 0.0}}
```
`text` is at most 40 words. `repro` is a command, not a story. History lives in `id` and
`status`, never in `text`.
Run `python ${CLAUDE_PLUGIN_ROOT}/scripts/render_verdict.py <id>`.
`decision` is `reject` iff any open `block`, or CI red. Set ticket `status` accordingly.
Append to `## Log`: `### [verdict] <timestamp> — <decision>`, then one line per open block or
warn: `- <severity> <id> <ac|charter|->: <text>`, suffixed ` → child` when `spawn_child`.
Notes are not logged on this ticket.
## Never
- Fix code. Findings only.
- Block without an `ac` or `charter` citation.
- Edit any ticket, plan, or trace other than this ticket's.
- Skip Phase A or shorten it because the diff looks simple.
- Read the build session's transcript.
