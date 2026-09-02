---
name: verdict
description: >-
  Fresh-context review of one ticket's diff against its ACs and the charter, in one model call
  over a prepared input: scripts gather and check, the model judges once, one HTML verdict page
  is the ship gate. Use whenever the user says verdict, review, or a ticket reaches in_review.
  Must run in a session that did not implement the ticket.
disable-model-invocation: true
argument-hint: "<ticket id>"
---
# Verdict
Input: `traces/verdict/<id>.input.md`, written by `verdict_prep.py`. It holds the ticket's ACs,
Out of scope, `writes:`, human waivers, the plan's "Verdict must attack" items, the charter
items, the previous verdict's blocks with repro commands, the `make ci` result, the mechanical
findings, the tests added on the branch, and the diff. Read nothing else. You do not see the
implementer's reasoning; keep it that way.
Output: `traces/verdict/<id>.json` → `render_verdict.py` → `traces/verdict/<id>.html`. Gate 2.

## Budget
Three tool calls: prep, write the JSON, close out. One judgement pass over the input, then write.
No exploration, no second reading of the repo. If the input is not enough to judge an AC, that
AC gets a `warn` saying what evidence is missing; it never gets a read of the tree.

## Step 1 — prep
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verdict_prep.py <id>
```
Runs `make ci` and `verdict_checks.py`; prints the input path. Read that file.

## Step 2 — judge, then write `traces/verdict/<id>.json`
Judge in this order, one line of thought each, no prose in the output:
1. every "Verdict must attack" item — does the diff plus its tests hold against it?
2. every AC — met by the diff, and named by a test added on this branch?
3. each charter item reachable in this diff;
4. previous blocks — re-run each `repro`; still failing → `open`, else `resolved`.
Severity: `block` = an AC unmet, a test drifting from its AC, a write outside `writes:`, a
charter violation reachable here; every block cites `ac` or `charter`. `warn` = anything else
worth a human's eye. No `note`s: what holds goes into `held` as its label (`AC-3`,
`charter-2`, `attack-1`, `retry-safety`), what belongs to another ticket goes into `warn` with
`home` set. `text` is at most 20 words, caveman-terse, `file:line` where it applies. `repro` is
a command or null.
Copy the mechanical findings from the input verbatim (ids `C1..`); yours are `F1..`. Carry
previous blocks forward with their ids. A waiver is a `### [human]` Log entry already in the
input: put its timestamp in `waived_by`, do not restate it.
```json
{"ticket": "<id>", "decision": "ship|reject",
 "held": ["attack-1", "AC-1", "AC-2", "charter-1"],
 "findings": [
   {"id": "F1", "severity": "block|warn", "status": "open|resolved",
    "ac": "AC-3", "charter": null, "home": null, "spawn_child": false,
    "covered_by": null, "repro": "one command or null", "waived_by": null,
    "text": "≤20 words: what breaks, file:line."}
 ],
 "ci": {"green": true, "mutation_score": null}}
```
`decision` is `reject` iff any open `block`, or CI red.

## Step 3 — close out, one command
Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_verdict.py <id>` (it archives the JSON as
`<id>.prev.json` for the next retry). Set ticket `status` from the decision: `ship` → `done`,
`reject` → `in_progress`. No other value exists. Append to `## Log`:
`### [verdict] <timestamp> — <decision>`, then one line per open block or warn:
`- <severity> <id> <ac|charter|->: <text>`, suffixed ` → child` when `spawn_child`.

## Never
- Fix code. Findings only.
- Block without an `ac` or `charter` citation.
- Edit any ticket, plan, or trace other than this ticket's.
- Read the build session's transcript, or anything beyond the input file.
- Write a finding for something that holds. It is a `held` label or nothing.
