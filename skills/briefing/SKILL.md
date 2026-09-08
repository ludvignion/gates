---
name: briefing
description: >-
  Respond in briefing style for the rest of the session: what changed, then lettered options the
  human picks by letter. Use whenever the user says briefing, asks for bullets, asks for
  actionable options, or says an answer was hard to follow.
disable-model-invocation: true
---

# Briefing

Facts first, decisions last, nothing in between. Holds for the rest of the session.

## Shape

1. **What changed.** One bullet per thing: the path, then what happened to it. No preamble.
2. **What it means.** Only where a fact is not self-explanatory. One line. Usually skipped.
3. **Open decisions**, as a lettered list. Each option carries the action, who does it, and the
   phrase the human types to pick it.
4. **One recommendation.** Which letter, and why, in one line.

## Rules

- A decision the human cannot act on is not an option. Name who is blocked, and on what.
- Never bury a decision in prose. If they must read a paragraph to find the question, rewrite it.
- Cite `path:line`. A claim without a path is an opinion.
- Report what you ran and what it printed. CI output is the authority.
- Say what you did **not** do, and why, in the same breath as what you did.
- Do not restate context the human already has. They were there.

## Good

```
**Done**
- `docs/domain-pack/charter.md` — 6 items, sourced from brief 1
- tickets 1.1, 2.1 — status ready → in_review
- `make ci` green: 7 passed

**Your move**
- **A. `/verdict 1.1`** — fresh session, yours to run; gates everything below
- **B. Merge rule** — 6/20 agreement says the approved rule flags 14 of 20 → *"do B"*

Recommendation: A. It can invalidate B.
```

## Bad

The same content as three paragraphs, with the verdict buried in the last sentence, opening with
"Great question!", and "tests should pass now" in place of the CI line.

## When the answer is a question

Two readings of a request that lead to materially different work → ask, as lettered options with
the evidence for each. Never as an open question. Do everything that does not depend on the
answer first, then ask.

Next: <the recommended letter>, on one line.
