---
name: verdict
description: >-
  Fresh-context review of one ticket's diff: adversarial pass first, quality pass second, one
  HTML verdict page the human reads as the ship gate. Use whenever the user says verdict, review,
  or a ticket reaches in_review. Must run in a session that did not implement the ticket.
disable-model-invocation: true
argument-hint: "<ticket id>"
---

# Verdict

Input: ticket `<id>`, branch `ticket/<id>`, the diff vs its base, `make ci` output.
Output: `traces/verdict/<id>.json` → `render_verdict.py` → `traces/verdict/<id>.html`. Gate 2.

You see the ticket and the diff. You do not see the implementer's reasoning. Keep it that way.

## Phase A — adversarial (do this first, in full)

Generate ≥10 attack scenarios and check each against the diff and tests:
uncovered edge cases · scale · hidden assumptions · partial failure · ordering dependence ·
data loss / duplication / silent coercion · retry safety · anything in `docs/domain-pack/` charter.
For each: `{"attack": "...", "covered_by": "test path or null", "severity": "block|warn|note"}`.

Every item under the plan's "## Verdict must attack" is a mandatory attack scenario.

## Phase B — quality

Per file touched, yes/no:
1. Describable in one sentence without "and"?
2. Every non-trivial addition traces to an AC? (walk the diff; list leftovers)
3. Any abstraction, knob, or flexibility no AC asked for?
4. Any helper with fewer than three call sites?
5. Any caller using less than half the interface it depends on?
6. Any dependency on another module's internals?
Then compare committed tests against the ACs verbatim. Drift = finding.
Then charter conformance from `docs/domain-pack/`, item by item, or explicit waiver.

## Output

Write `traces/verdict/<id>.json`:
```json
{"ticket": "<id>", "decision": "ship|reject",
 "attacks": [...], "quality": {"file": {"q1": true, ...}},
 "findings": [{"severity": "block|warn", "text": "...", "spawn_child": true}],
 "ci": {"green": true, "mutation_score": 0.0}}
```
Run `python ${CLAUDE_PLUGIN_ROOT}/scripts/render_verdict.py <id>`.
`decision` is `reject` if any finding is `block` or CI is red. Set ticket `status` accordingly.
Append `### [verdict] <timestamp> — <decision>` to `## Log`.

## Never

- Fix code. Findings only.
- Skip Phase A or shorten it because the diff looks simple.
- Read the build session's transcript.
