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

The argument decides which of the two flows below runs. Nothing else does. `runner.py` and
`verdict_eval.py` never invoke `/verdict`: their default command feeds the packet text to
`claude -p` with no tools and takes the reply as the verdict. `/verdict` is the human path.

## Packet path
The argument ends in `.input.md`: `runner.py` wrote the packet and owns the close-out. The two
steps below are the whole session. No prep, no `render_verdict.py`, no ticket or Log edit, no
closing summary, no options. The runner validates, stamps, renders, and reads the decision.
1. Read the packet. Read nothing else.
2. Judge per its Instructions section. Write the verdict JSON at the `output` path in the
   packet's frontmatter.

## Ticket id
The argument is a ticket id: a human runs the verdict by hand. Three tool calls.
1. Prep:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verdict_prep.py <id>
   ```
   Runs `make ci` and `verdict_checks.py`; prints the packet path (`--arm blind|packet|repo`,
   default packet). Read the packet. Read nothing else.
2. Judge per the packet's Instructions section. Write the verdict JSON at the `output` path in
   the packet's frontmatter.
3. Close out, one command: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/render_verdict.py <id>`. It
   checks the JSON against the seat (blind downgrades uncited blocks to warns; other arms
   report them), stamps `meta`, and renders the page. Then set ticket `status` from the
   decision (`ship` → `done`, `reject` → `in_progress`; no other value) and append to `## Log`
   `### [verdict] <timestamp> — <decision>`, one line per open block or warn:
   `- <severity> <id> <ac|charter|->: <text>`, suffixed ` → child` when `spawn_child`.

## Never
- Fix code. Findings only.
- Write anything but the verdict JSON and, on the ticket-id flow, this ticket's Log.
- Read the build session's transcript, or anything beyond the packet (the `repo` seat allows
  read-only reads of the tree; the packet says so when it applies).
- Session ends at the verdict. On the packet-path flow, the verdict JSON is the last write and
  the last word.
