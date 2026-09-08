# traces

Machine-written. Do not edit by hand except `grill-misses.jsonl`.

- `grill/<n>.jsonl` — one record per design-tree node. Written by the grill skill.
- `grill-misses.jsonl` — you append at close-out: `{"ticket","finding","category","should_grill_have_caught","missed_question"}`. Written by `runner.py` / the grill skill; read by `grill_digest.py`.
- `blind-spots.md` — written by `grill_digest.py` (`make digest`); read by the grill skill.
- `verdict/<id>.json|html` — gate 2. `.json` written by the verdict skill; `.html` by `render_verdict.py` (`make verdict`).
- `golden/<n>/{brief.md,grill.jsonl}` — replay set. Read by `replay.py` (`make replay`).
- `board.html` — written by `render_board.py` (`make board`, and after every agent turn).
- `sessions.jsonl` — one line per agent turn. Written by the `trace_stop.py` Stop hook. Git-ignored.
