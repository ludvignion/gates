---
name: verdict
description: >-
  Fresh-context review of one ticket's diff against its ACs and the charter, in one model call
  over a prepared packet: scripts gather and check, the model judges once, one HTML verdict page
  is the ship gate. Use whenever the user says verdict, review, or a ticket reaches in_review.
  Must run in a session that did not implement the ticket.
disable-model-invocation: true
argument-hint: "<ticket id | packet path>"
---
# Verdict
The judging instructions live in `verdict-prompt.md` next to this file. `verdict_prep.py`
copies them into the packet, so the packet is self-contained: frontmatter (ticket, arm,
output path), the instructions, the seat line, then the evidence sections. The packet is the
seam: another vendor gets the same file. Gate 2 is the rendered page.

## Budget
Three tool calls: prep, write the JSON, close out. One judgement pass over the packet.

## Step 1 — prep (skip when the argument is a packet path)
Argument `<ticket id>`:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verdict_prep.py <id>
```
Runs `make ci` and `verdict_checks.py`; prints the packet path (`--arm blind|packet|repo`,
default packet). Argument ending in `.input.md`: the packet exists (runner.py wrote it); do not
run prep again. Read the packet. Read nothing else.

## Step 2 — judge, then write the JSON
Follow the packet's Instructions section. Write the JSON to the `output` path in the packet's
frontmatter.

## Step 3 — close out, one command
Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_verdict.py <id>`. It checks the JSON
against the seat (blind downgrades uncited blocks to warns; other arms report them), stamps
`meta` (arm, vendor, plugin_version, prompt_sha, packet_sha), and renders the page. Then, if
the ticket file named in the packet frontmatter exists: set ticket `status` from the decision
(`ship` → `done`, `reject` → `in_progress`; no other value) and append to `## Log`
`### [verdict] <timestamp> — <decision>`, one line per open block or warn:
`- <severity> <id> <ac|charter|->: <text>`, suffixed ` → child` when `spawn_child`.

## Never
- Fix code. Findings only.
- Write anything but the verdict JSON and this ticket's Log.
- Read the build session's transcript, or anything beyond the packet (the `repo` seat allows
  read-only reads of the tree; the packet says so when it applies).
