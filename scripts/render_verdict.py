#!/usr/bin/env python3
"""Close-out of one verdict, and the Gate 2 page. Run: render_verdict.py <id> [--vendor claude-session] [--summary-model haiku|none]

Loads traces/verdict/<id>.json and the packet it answered (<id>.input.md) through
scripts/schemas.py, applies verdict_checks.validate for the packet's arm (blind downgrades
uncited blocks; other arms report them; every AC and charter item must be held or found, else a
C-warn "unaccounted"), sets `ticket` from the packet, stamps `meta` (arm, vendor,
plugin_version, prompt_sha, packet_sha, cost_usd, tokens, seconds, opik) into the JSON, and
writes traces/verdict/<id>.html, the gate 2 page. Idempotent: a second run re-derives the same
stamp and keeps cost/seconds/opik it already has. Without a packet (a verdict written before
0.6.2) the page renders unstamped.

The page opens with three sections a human reads first:
- What was built: at most three sentences, from the ticket's close-out Log entry and the diff
  stat, written by one cheap model call (--summary-model, default haiku; `none` skips it).
- What the reviewer said: the decision, what held, each block and where it belongs, the warns,
  in plain language, from the same call; the facts are listed under it regardless.
- Recommended action: computed from the findings, never by a model. An open block with
  spawn_child → "ship, create child <id>.<n> from F#"; an open block without → "rework in
  place"; each open warn → "home to <id>" when another open ticket's writes: cover the file the
  warn names, else "waive" — or a child of its own when the warn is high impact.
- Human checks (when the ticket has `(human)` ACs, E30): one ☐ line per AC only a person can
  verify; the ship's Log entry records who confirmed them.
The model text is cached in traces/verdict/<id>.summary.json keyed on the inputs' sha; the
runner shows it in the Opik trace output. `result_lines` is the end-of-run block (contract C).
"""
import argparse
import html
import json
import re
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402
import vendor  # noqa: E402
import verdict_checks  # noqa: E402

DEFAULT_VENDOR = "claude-session"  # the /verdict skill runs inside a claude session; runner.py derives its own
DEFAULT_SUMMARY_MODEL = "haiku"  # the cheapest Claude; `none` skips the call
SUMMARY_CMD = 'claude -p --model {model} --output-format json --tools ""'
SUMMARY_PROMPT = """You summarise one code-review verdict for the person who decides whether to merge.
Reply with one JSON object and nothing else: {{"built": "...", "review": "..."}}.
"built": at most three sentences saying what the change does, from the close-out entry and the diff stat.
"review": plain language, at most five sentences: the decision, what held, each block and where it belongs, the warns.
No markdown, no headings, no finding ids in prose except when naming a block.

## Close-out entry
{closeout}

## Diff stat
{stat}

## Verdict JSON
{verdict}
"""
_FILE_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)+[\w.-]+\.\w+)(?::\d+)?")


def esc(s: object) -> str:
    return html.escape(str(s)) if s is not None else ""


def li(f: dict) -> str:
    cite = f.get("ac") or f.get("charter") or "—"
    meta = " · ".join(
        m for m in (
            f"home {esc(f['home'])}" if f.get("home") else "",
            f"repro <code>{esc(f['repro'])}</code>" if f.get("repro") else "",
            f"covered by <code>{esc(f['covered_by'])}</code>" if f.get("covered_by") else "",
            f"waived {esc(f['waived_by'])}" if f.get("waived_by") else "",
        ) if m
    )
    consequence = " ".join(str(f.get("consequence") or "").split())
    return (
        f"<li class={f['severity']}><b>{esc(f.get('id'))}</b> <code>{esc(cite)}</code> "
        f"<code>{esc(impact_of(f))} impact</code> "
        f"{esc(f['text'])}{' → child ticket' if f.get('spawn_child') else ''}"
        f"{f'<p>{esc(consequence)}</p>' if consequence else ''}"  # the recap prints this and drops the text; the page keeps both
        f"{f'<div class=meta>{meta}</div>' if meta else ''}</li>"
    )


# --- stamp ----------------------------------------------------------------------------------
def stamp(root: Path, tid: str, vendor_name: str, cost_usd: float | None = None, tokens: dict | None = None,
          seconds: float | None = None, opik: str | None = None) -> tuple[schemas.Verdict, list[str]]:
    """Validate against the packet's arm, set the ticket from the packet, stamp meta, write the
    JSON back. Returns the stored verdict and the violations. cost_usd/tokens/seconds/opik come
    from the caller that ran the call (runner.py); a re-run without them keeps what is stamped."""
    vpath = root / "traces" / "verdict" / f"{tid}.json"
    ppath = root / "traces" / "verdict" / f"{tid}.input.md"
    v = schemas.Verdict.load(vpath)
    if not ppath.exists():
        return v, []
    packet = schemas.Packet.load(ppath)
    v = replace(v, ticket=packet.ticket or tid)
    v, violations = verdict_checks.validate(v, packet.arm, packet, tickets_of(root))
    old = v.meta

    def keep(new, name):
        return new if new is not None else (getattr(old, name) if old else None)

    meta = schemas.VerdictMeta(
        arm=packet.arm, vendor=vendor_name, plugin_version=str(packet.fm.get("plugin_version", "")),
        prompt_sha=str(packet.fm.get("prompt_sha", "")), packet_sha=schemas.sha256(ppath.read_bytes()),
        cost_usd=keep(cost_usd, "cost_usd"), tokens=keep(tokens, "tokens"),
        seconds=keep(seconds, "seconds"), opik=keep(opik, "opik"),
    )
    v = v.with_meta(meta)
    v.dump(vpath)
    return v, violations


# --- summary (one cheap model call, cached) -------------------------------------------------
def closeout_entry(root: Path, packet: schemas.Packet) -> str:
    tf = packet.fm.get("ticket_file")
    path = root / str(tf) if tf else None
    if not path or not path.exists():
        return ""
    entries = schemas.Log.parse(_fm.read(path)[1]).closeouts()
    return entries[-1].text if entries else ""


def summary_path(root: Path, tid: str) -> Path:
    return root / "traces" / "verdict" / f"{tid}.summary.json"


def summarize(root: Path, tid: str, packet: schemas.Packet, v: schemas.Verdict, model: str | None) -> dict:
    """{"built", "review", "model", "inputs_sha", "cost_usd", "error"}; cached in <id>.summary.json."""
    spath = summary_path(root, tid)
    closeout = closeout_entry(root, packet)
    stat = packet.section("Diff stat").strip().strip("`").strip()
    prompt = SUMMARY_PROMPT.format(closeout=closeout or "(no close-out entry)", stat=stat or "(no diff stat)",
                                   verdict=json.dumps({k: val for k, val in v.as_dict().items() if k != "meta"}, indent=1))
    inputs_sha = schemas.sha256(prompt)
    if spath.exists():
        try:
            cached = json.loads(spath.read_text(encoding="utf-8"))
            if cached.get("inputs_sha") == inputs_sha and cached.get("model") == model and not cached.get("error"):
                return cached
        except (json.JSONDecodeError, AttributeError):
            pass
    out = {"built": None, "review": None, "model": model, "inputs_sha": inputs_sha, "cost_usd": None, "error": None}
    if not model or model == "none":
        out["error"] = "summary skipped (--summary-model none)"
    else:
        call = vendor.run(SUMMARY_CMD.format(model=model), root, stdin_text=prompt)
        obj = vendor.object_in_result(call.stdout, "built") if call.returncode == 0 else None
        if obj is None:
            out["error"] = f"summary call failed: exit {call.returncode}" if call.returncode else "summary call returned no JSON"
        else:
            out.update({"built": str(obj.get("built") or ""), "review": str(obj.get("review") or ""), "cost_usd": call.cost_usd})
    spath.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


# --- recommended action (computed, never by a model) ---------------------------------------
def tickets_of(root: Path) -> list[tuple[str, str, list[str]]]:
    """(id, status, writes) for every ticket file under kanban/."""
    if not (root / "kanban").is_dir():
        return []
    return [(fm["id"], str(fm.get("status", "")), list(fm.get("writes") or [])) for _, fm, _ in _fm.tickets(root / "kanban")]


def next_child(tid: str, tickets: list[tuple[str, str, list[str]]]) -> str:
    n = 1 + sum(1 for t, _, _ in tickets if t.startswith(tid + ".") and t.count(".") == tid.count(".") + 1)
    return f"{tid}.{n}"


def home_for(text: str, tid: str, tickets: list[tuple[str, str, list[str]]]) -> str | None:
    """Another open ticket whose writes: cover a file named in the finding text."""
    files = _FILE_RE.findall(text)
    for other, status, writes in tickets:
        if other == tid or status in schemas.CLOSED_STATUSES:
            continue
        if any(f.startswith(w.rstrip("/")) for f in files for w in writes if w):
            return other
    return None


def recommendations(v: schemas.Verdict, tickets: list[tuple[str, str, list[str]]]) -> list[tuple[str, str]]:
    """[(finding id, action)] for every open block and warn; [] means ship as is. A warn no other
    ticket can home lands on `waive` — unless its impact is high, which nobody waives by default:
    it gets a child of its own."""
    out = []
    child = next_child(v.ticket, tickets)
    for f in v.findings:
        if not v.is_open(f):
            continue
        if f.get("severity") == "block":
            out.append((f["id"], f"ship, create child {child} from {f['id']}" if f.get("spawn_child") else "rework in place"))
        elif f.get("severity") == "warn":
            home = f.get("home") or home_for(str(f.get("text", "")), v.ticket, tickets)
            if home:
                out.append((f["id"], f"home to {home}"))
            else:  # a high-impact warn is never waived by default: somebody holds it
                out.append((f["id"], f"ship, create child {child} from {f['id']}" if impact_of(f) == "high" else "waive"))
    return out


def action_word(action: str) -> str:
    """The arrow's word in the end-of-run lines (E19), from a recommendations() action:
    "ship, create child 2.2.1 from F1" → "child 2.2.1", "rework in place" → "rework",
    "home to 2.3" → "home 2.3", "waive" → "waive"."""
    if action.startswith("ship, create child "):
        return "child " + action[len("ship, create child "):].split(" from ")[0]
    if action.startswith("home to "):
        return "home " + action[len("home to "):]
    return "rework" if action == "rework in place" else action


def finding_file(text: str) -> str:
    """The first path[:line] named in a finding's text, "—" when none."""
    m = _FILE_RE.search(text)
    return m.group(0) if m else "—"


def human_ac(item) -> tuple[str, str]:
    """(id, text) from a schemas.HumanAc, a pair, or an AC line "AC-7 (human): text"."""
    if isinstance(item, str):
        m = re.match(r"^(?:-\s*)?(?P<id>AC-\d+)\s*(?:\(human\))?\s*:\s*(?P<text>.*)$", item.strip())
        return (m.group("id"), m.group("text").strip()) if m else (item.strip(), "")
    if hasattr(item, "id") and hasattr(item, "text"):
        return str(item.id), str(item.text)
    return str(item[0]), str(item[1])


def mmss(seconds: "float | None") -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds is not None else "n/a"


def token_words(tokens: "dict | None") -> str:
    """"21K in / 8K out" from the vendor's usage (input = new + cached + cache-read input)."""
    if not tokens:
        return "tokens n/a"
    t = tokens
    inp = sum(int(t.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return f"{round(inp / 1000)}K in / {round(int(t.get('output_tokens') or 0) / 1000)}K out"


def charter_line(charter: "dict | None") -> str:
    """"Charter: <names> held · <names> — touched, not judged" with the items' names from the
    charter headings (E34), never ids. Items the diff cannot reach are never mentioned; reachable
    items the reviewer neither held nor cited are "touched, not judged": never findings, never
    waivable. `charter` is verdict_checks.charter_report's dict."""
    c = charter or {}
    reachable = [str(i) for i in c.get("reachable") or ()]
    if not reachable:
        return "Charter: none touched"
    names = c.get("names") or {}
    name = lambda i: str(names.get(i) or i.removeprefix("charter-"))  # noqa: E731
    held = [i for i in reachable if i in set(c.get("held") or ())]
    cited = set(c.get("findings") or {})
    unjudged = [i for i in reachable if i not in held and i not in cited]
    parts = ([", ".join(map(name, held)) + " held"] if held else []) + ([", ".join(map(name, unjudged)) + " — touched, not judged"] if unjudged else [])
    return "Charter: " + (" · ".join(parts) or ", ".join(map(name, reachable)) + " — in findings")


RESULT_MAX_LINES = 40
IMPACTS = ("high", "medium", "low")
BODY_WIDTH = 100
COLUMN = 21
INDENT = " " * 4
RULE = "─" * 54
COUNT_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def finding_line(fid: str, text: str, where: str) -> str:
    """"F# <file:line> — <text>", the file:line stripped from the text when it repeats the prefix (E33).
    The one-line form: what a verdict written without a `consequence` carries."""
    body = text.strip()
    while where != "—" and body.startswith(where) and body != where:
        body = body[len(where):].lstrip(" —:-,;") or where
    return f"{fid} {where} — {body}" if where != "—" else f"{fid} — {body}"


def impact_of(f: dict) -> str:
    """A finding's blast radius for a person: `high`, `medium`, `low`; `medium` when the verdict
    does not say. Never `severity` — that is the gate's question (does this stop the ship), this
    is the human's (who is blocked, and from what)."""
    word = str(f.get("impact") or "").strip().lower()
    return word if word in IMPACTS else "medium"


def open_findings(v: schemas.Verdict) -> list[dict]:
    """The open blocks and warns, worst blast radius first, verdict order within an impact:
    the one that costs a person the most is read first, not last."""
    open_ = [f for f in v.findings if v.is_open(f) and f.get("severity") in ("block", "warn")]
    return sorted(open_, key=lambda f: IMPACTS.index(impact_of(f)))  # stable: verdict order inside an impact


def seen_before(root: "Path | None", tid: str, f: dict) -> str:
    """"4.5 F2" when an earlier ticket's verdict already carried a finding naming the same file,
    "" when none. The recap's own line, never the judge's: the packet is the judge's whole world,
    so a finding that keeps coming back can only be spotted here."""
    if root is None:
        return ""
    files = set(_FILE_RE.findall(str(f.get("text", "")))) | set(_FILE_RE.findall(str(f.get("consequence", ""))))
    if not files:
        return ""
    for path in sorted((root / "traces" / "verdict").glob("*.json")):
        if path.name.endswith(".summary.json") or path.stem == tid:
            continue
        try:
            other = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for g in other.get("findings") or ():
            if not isinstance(g, dict) or g.get("severity") not in ("block", "warn"):
                continue
            if files & set(_FILE_RE.findall(str(g.get("text", "")))):
                return f"{path.stem} {g.get('id')}"
    return ""


def finding_block(f: dict, where: str, back: str = "") -> list[str]:
    """One finding as the recap prints it: `F1  high  scripts/init_project.py:158-165`, then
    `consequence` — who is blocked and what they cannot do — wrapped under it. A verdict without a
    `consequence` keeps the one-line form."""
    fid, text = str(f.get("id")), str(f.get("text", ""))
    consequence = " ".join(str(f.get("consequence") or "").split())
    back_line = [f"{INDENT}Second sighting: {back} flagged the same file."] if back else []
    if not consequence:
        return [finding_line(fid, text, where), *back_line]
    head = f"{fid}  {impact_of(f)}  {where}" if where != "—" else f"{fid}  {impact_of(f)}"
    wrapped = textwrap.wrap(consequence, BODY_WIDTH, initial_indent=INDENT, subsequent_indent=INDENT, break_on_hyphens=False)
    return [head, *wrapped, *back_line]


def findings_heading(open_: list[dict]) -> str:
    """"FINDINGS (4) — none block the ship; one is high impact": the count, then the two things a
    human needs before reading one: whether anything stands between them and a ship, and whether
    any of it is high impact."""
    if not open_:
        return "FINDINGS (0) — nothing open"
    blockers = [str(f.get("id")) for f in open_ if f.get("severity") == "block" and not f.get("spawn_child")]
    highs = [f for f in open_ if impact_of(f) == "high"]
    parts = [f"{and_list(blockers)} block{'s' if len(blockers) == 1 else ''} the ship" if blockers else "none block the ship"]
    if highs:
        parts.append(f"{COUNT_WORD.get(len(highs), len(highs))} {'is' if len(highs) == 1 else 'are'} high impact")
    return f"FINDINGS ({len(open_)}) — " + "; ".join(parts)


def and_list(names) -> str:
    names = [str(n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}" if names else ""


def answer_options(v: schemas.Verdict, tickets: list[tuple[str, str, list[str]]], open_: list[dict],
                   backs: "dict[str, str] | None" = None) -> list[tuple[list[str], str]]:
    """The ways to answer Gate 2, recommended first, each `([the lines to type, in order], what it
    does)`. A routes every finding that has somewhere to go and ships, one typed line each —
    `child from F#`, `home F# to <id>` or `waive F#: <why>`, the last never dropped to prose
    (plan 6 AC-4); B is the blunt one — ship as it stands, or, when a block holds the gate shut,
    the override that waives and ships; B is dropped when it is A. The last is always the
    human's own words: the gate stays open. A finding's own `action`/`why` (when legal — not
    `action_refused`) is typed verbatim; otherwise the line comes from recommendations(), plus
    one rule of its own: a warn `backs` has seen on an earlier ticket gets a child instead of a
    waiver. It came back once."""
    per, backs = dict(recommendations(v, tickets)), backs or {}
    child_n = int(next_child(v.ticket, tickets).rsplit(".", 1)[1])  # per call: two children in one run are .1 and .2
    routed, waived, blockers = [], [], []
    for f in open_:
        fid = str(f.get("id"))
        if f.get("action") and not f.get("action_refused"):
            verb, why, target = str(f["action"]), str(f.get("why") or ""), f.get("home")
        else:
            verb, _, target = action_word(per.get(fid, "waive")).partition(" ")
            why = str(f.get("text") or "")
            if verb == "waive" and backs.get(fid):  # twice is a pattern, not a nit
                verb = "child"
        if verb == "child":
            routed.append((f"child from {fid}", f"{fid} → {v.ticket}.{child_n}"))
            child_n += 1
        elif verb == "home":
            routed.append((f"home {fid} to {target}", f"{fid} → {target}"))
        elif verb == "rework":
            blockers.append(fid)
        else:
            waived.append(f"waive {fid}: {why}")
    if blockers:
        a = ([f"reject: rework {', '.join(blockers)}"] + [line for line, _ in routed] + waived,
             f"{v.ticket} goes back to build with {and_list(blockers)} named. Nothing merges.")
        b = ([f"waive {fid}: <reason>" for fid in blockers] + [line for line, _ in routed] + waived + ["ship"],
             f"Merges with {and_list(blockers)} unfixed. Your reason goes in the ticket log.")
    else:
        high = next((str(f.get("id")) for f in open_ if impact_of(f) == "high"), "")
        made = ", ".join(n for _, n in routed)
        a = ([line for line, _ in routed] + waived + ["ship"], (made + ". " if made else "") + f"{v.ticket} merges.")
        n = len(open_)
        held_by_nobody = (("The finding is" if n == 1 else f"All {COUNT_WORD.get(n, n)} are") + " logged open and unfixed"
                          + (f", including {high}, the high-impact one." if high else f", and nobody is holding {'it' if n == 1 else 'them'}."))
        b = (["ship"], "Merges now. " + (held_by_nobody if open_ else "Nothing is open."))
    c = (["<your own words>"], f"A question, a change to one of these, or `reject: <reason>` to send all of {v.ticket} back to build. "
                               "Nothing merges until you say so.")
    return [a, c] if a[0] == b[0] else [a, b, c]


def stance_line(v: schemas.Verdict, open_: list[dict]) -> str:
    """"Ship.", "Ship with waivers." or "Send it back." — derived from `decision` and whether
    anything is still open, never from a written field — plus the seat's own sentence from the
    verdict's top-level `stance`, when it wrote one (plan 6 AC-7, ticket 6.2 AC-1/AC-2). A stance
    sentence can never move the printed verb: only `decision` does."""
    verb = "Send it back." if v.decision == "reject" else ("Ship with waivers." if open_ else "Ship.")
    sentence = str(v.raw.get("stance") or "").strip()
    return f"{verb} {sentence}" if sentence else verb


def next_block(v: schemas.Verdict, tickets: list[tuple[str, str, list[str]]], open_: list[dict],
               human_acs=(), backs: "dict[str, str] | None" = None) -> list[str]:
    """The end of the recap: the options in a labelled column, the recommended one first,
    then the one line that says how to answer. Nothing follows it. Option A's head is the
    stance line (ticket 6.2 AC-1); its own typed lines follow, indented like every other line."""
    opts = answer_options(v, tickets, open_, backs)
    labels = ["A (recommended)", "B", "C"] if len(opts) == 3 else ["A (recommended)", "C"]
    out = []
    if human_acs:
        out += [f"First check {', '.join(aid for aid, _ in map(human_ac, human_acs))} on the phone — a ship confirms it.", ""]
    out += [f"NEXT — {COUNT_WORD.get(len(opts), len(opts))} ways to answer:", ""]
    pad = " " * COLUMN
    for label, (lines, note) in zip(labels, opts):
        if label.startswith("A "):
            out.append(f"  {label:<{COLUMN - 2}}{stance_line(v, open_)}")
            out += [pad + line for line in lines]
        else:
            out.append(f"  {label:<{COLUMN - 2}}{lines[0]}")
            out += [pad + line for line in lines[1:]]
        out += [pad + line for line in textwrap.wrap(note, BODY_WIDTH - COLUMN, break_on_hyphens=False)]
        out.append("")
    return out + ["Type one option's lines, in order, one line at a time."]


def changed_line(changed: "dict | None") -> str:
    """"Changed: N files +A/−B — <up to 6 file names>" (E27)."""
    files = list((changed or {}).get("files") or ())
    added, removed = sum(int(f.get("added") or 0) for f in files), sum(int(f.get("removed") or 0) for f in files)
    names = [str(f.get("path")) for f in files]
    shown = ", ".join(names[:6]) + (f", +{len(names) - 6} more" if len(names) > 6 else "")
    return f"Changed: {len(files)} files +{added}/−{removed}" + (f" — {shown}" if shown else "")


def result_lines(v: schemas.Verdict, tickets: list[tuple[str, str, list[str]]], cost_usd: float | None,
                 seconds: float, page: str, *, summary: dict | None = None, changed: dict | None = None,
                 charter: dict | None = None, human_acs=(), costs: dict | None = None, root: Path | None = None) -> list[str]:
    """The end of a run as the skill prints it and traces/runs/<id>.result stores it, at most
    RESULT_MAX_LINES lines of text, no dollar amounts (E19, E24, E27, E28, E30, E31–E34):
      <id> · SHIP|REJECT · build m:ss · verdict m:ss · <in>K in / <out>K out
      Built: <one sentence from summary.json>
      FINDINGS (N) — <what blocks the ship; what is high impact>
      F1  high  <file:line>   then the consequence: who is blocked and from what
      Charter: <names> held · <names> — touched, not judged
      Changed: N files +A/−B — <up to 6 file names>
      Page: <path>
      N proposals refused — see <path> (only when at least one open finding's action was refused)
      NEXT — <n> ways to answer: the options in a column, recommended first, then how to answer
    Pure code from verdict.json, summary.json and the dicts the runner hands over; the options
    are answer_options(), never a model call. `root` (the working tree) only buys the second
    sighting line; without it the block is the same minus that. `cost_usd` and `seconds` are kept
    for callers; `costs` carries build_s and verdict_s."""
    open_ = open_findings(v)
    c = costs or {}
    head = f"{v.ticket} · {v.decision.upper()} · build {mmss(c.get('build_s'))} · verdict {mmss(c.get('verdict_s'))} · {token_words(v.meta.tokens if v.meta else None)}"
    built = str((summary or {}).get("built") or "").strip() if not (summary or {}).get("error") else ""
    sentence = re.split(r"(?<=[.!?])\s", built, maxsplit=1)[0] if built else "no summary"
    humans = [f"Human: {aid} — {text}" for aid, text in map(human_ac, human_acs)]
    backs = {str(f.get("id")): seen_before(root, v.ticket, f) for f in open_}
    refused_n = sum(1 for f in open_ if f.get("action_refused"))
    refused_line = [f"{refused_n} proposal{'s' if refused_n != 1 else ''} refused — see {page}."] if refused_n else []
    tail = [charter_line(charter), *humans, changed_line(changed), f"Page: {page}", *refused_line, "", *next_block(v, tickets, open_, human_acs, backs)]
    body = [head, "", f"Built: {sentence}", "", findings_heading(open_)]
    if not open_:
        return [*body, "", *tail]
    blocks = [finding_block(f, finding_file(str(f.get("text", ""))), backs.get(str(f.get("id")), "")) for f in open_]
    room = RESULT_MAX_LINES - len([line for line in (*body, *tail) if line]) - 2  # the two rules
    shown: list[str] = []
    for i, block in enumerate(blocks):
        left = len(blocks) - i
        if len(block) + (1 if left > 1 else 0) > room:
            shown.append(f"… {left} more on the page")
            break
        shown += ([""] if shown else []) + block
        room -= len(block)
    return [*body, RULE, *shown, RULE, "", *tail]

# --- page -------------------------------------------------------------------------------------
def seat_line(v: schemas.Verdict) -> str:
    if not v.meta:
        return "Seat: unstamped"
    m = v.meta
    t = m.tokens or {}
    parts = [
        f"{m.vendor} · {m.arm} seat",
        f"cost {'$' + format(m.cost_usd, '.4f') if m.cost_usd is not None else 'n/a'}",
        f"tokens in/out {t.get('input_tokens', 'n/a')}/{t.get('output_tokens', 'n/a')}",
        f"{format(m.seconds, '.0f') + ' s' if m.seconds is not None else 'n/a s'}",
        m.opik or "untraced (no runner)",
    ]
    return "Seat: " + " · ".join(parts)


def human_acs_of(root: Path, packet: schemas.Packet) -> tuple:
    """The ticket's `(human)` ACs through the packet's ticket_file (schemas.Ticket.human_acs,
    E30); empty when the packet names no ticket file or the schema predates the field."""
    tf = packet.fm.get("ticket_file")
    path = root / str(tf) if tf else None
    if not path or not path.exists():
        return ()
    return tuple(getattr(schemas.Ticket.parse(_fm.read(path)[1]), "human_acs", ()))


def render(root: Path, tid: str, v: schemas.Verdict, violations: list[str], summary: dict, actions: list[tuple[str, str]],
           closeout: str = "", stat: str = "", human_acs=()) -> Path:
    """The Gate 2 page. `human_acs` (E30) adds a "Human checks" section, one ☐ line per AC only a
    person can verify; the ship confirms them."""
    ship = v.decision == "ship"
    human_html = ("<section class=top><h2>Human checks</h2><ul>"
                  + "".join(f"<li>☐ {esc(aid)}: {esc(text)}</li>" for aid, text in map(human_ac, human_acs)) + "</ul></section>") if human_acs else ""
    is_open = schemas.Verdict.is_open
    fs = v.findings
    blocks = [f for f in fs if f["severity"] == "block" and is_open(f)]
    warns = [f for f in fs if f["severity"] == "warn" and is_open(f)]
    resolved = [f for f in fs if not is_open(f)]
    resolved_html = (
        f"<details><summary>Resolved ({len(resolved)})</summary><ul>{''.join(li(f) for f in resolved)}</ul></details>"
        if resolved else ""
    )
    quality = ""
    for f, qs in v.raw.get("quality", {}).items():
        cells = "".join(f"<td{' class=no' if not ok else ''}>{'✓' if ok else '✗'}</td>" for ok in qs.values())
        quality += f"<tr><td>{esc(f)}</td>{cells}</tr>"
    ci = v.ci
    held_html = f"<p class=meta>Held: {esc(', '.join(v.held))}</p>" if v.held else ""
    invalid_html = "<p class=invalid>Invalid: " + "; ".join(esc(x) for x in violations) + "</p>" if violations else ""

    if summary.get("built"):
        built = f"<p>{esc(summary['built'])}</p>"
    else:
        built = (
            f"<p><i>summary unavailable: {esc(summary.get('error') or 'no summary')}</i></p>"
            + (f"<pre>{esc(closeout.strip())}</pre>" if closeout.strip() else "")
            + (f"<pre>{esc(stat.strip())}</pre>" if stat.strip() else "")
        )

    def where(f: dict) -> str:
        return f"belongs to {esc(f['home'])}" if f.get("home") else ("child ticket" if f.get("spawn_child") else "this ticket")

    said_facts = (
        f"<p><b>{'Ship' if ship else 'Reject'}.</b> "
        + (f"Held: {esc(', '.join(v.held))}. " if v.held else "")
        + (f"{len(blocks)} open block{'s' if len(blocks) != 1 else ''}: " + "; ".join(f"{esc(f['id'])} ({where(f)}) {esc(f['text'])}" for f in blocks) + ". " if blocks else "No open block. ")
        + (f"{len(warns)} warn{'s' if len(warns) != 1 else ''}: " + "; ".join(f"{esc(f['id'])} {esc(f['text'])}" for f in warns) + "." if warns else "No warns.")
        + "</p>"
    )
    said = (f"<p>{esc(summary['review'])}</p>" if summary.get("review") else "") + said_facts
    by_id = {str(f.get("id")): f for f in fs}

    def refusal_note(fid: str) -> str:
        reason = by_id.get(fid, {}).get("action_refused")
        if not reason:
            return ""
        why = by_id[fid].get("why")
        return f" — proposal refused: {esc(reason)}" + (f" ({esc(why)})" if why else "")

    actions_html = "".join(f"<li><b>{esc(fid)}</b> {esc(a)}{refusal_note(fid)}</li>" for fid, a in actions) or "<li>ship as is</li>"
    out = f"""<!doctype html><meta charset=utf-8><title>Verdict {tid}</title>
<style>body{{font-family:system-ui;max-width:1000px;margin:2rem auto}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.hero{{font-size:2rem;padding:1rem;color:#fff;background:{'#3cb371' if ship else '#c0392b'}}}
li.block{{background:#fdd}}li.warn{{background:#ffe9c7}}
li{{margin:.3rem 0;padding:.2rem .4rem;list-style:none}}ul{{padding-left:0}}
.meta{{color:#666;font-size:.85rem}}.invalid{{color:#c0392b;font-weight:bold}}td.no{{background:#fdd}}details{{margin:.4rem 0}}
section.top{{background:#f6f6f6;padding:.6rem 1rem;border-radius:8px;margin:.6rem 0}}pre{{white-space:pre-wrap;font-size:.85rem}}</style>
<div class=hero>{'SHIP' if ship else 'REJECT'} — {tid}</div>
<p class=meta>{esc(seat_line(v))}</p>
<p>CI: {'green' if ci.get('green') else 'RED'} · mutation: {ci.get('mutation_score','n/a')}</p>
{invalid_html}
<section class=top><h2>What was built</h2>{built}</section>
<section class=top><h2>What the reviewer said</h2>{said}</section>
<section class=top><h2>Recommended action</h2><ul>{actions_html}</ul></section>
{human_html}
{held_html}
<h2>Blocks ({len(blocks)})</h2><ul>{''.join(li(f) for f in blocks) or '<li>none</li>'}</ul>
<h2>Warnings ({len(warns)})</h2><ul>{''.join(li(f) for f in warns) or '<li>none</li>'}</ul>
{resolved_html}
<h2>Quality</h2><table><tr><th>File</th><th>1 SRP</th><th>2 AC-trace</th><th>3 YAGNI</th><th>4 rule-of-3</th><th>5 ISP</th><th>6 DIP</th></tr>{quality}</table>"""
    out_path = root / "traces" / "verdict" / f"{tid}.html"
    out_path.write_text(out, encoding="utf-8")
    return out_path


def main(root: Path, tid: str, vendor_name: str = DEFAULT_VENDOR, cost_usd: float | None = None, tokens: dict | None = None,
         seconds: float | None = None, opik: str | None = None, summary_model: str | None = DEFAULT_SUMMARY_MODEL) -> list[str]:
    """Stamp, summarise, recommend, render, print the page path; violations go to stderr and come
    back to the caller."""
    v, violations = stamp(root, tid, vendor_name, cost_usd, tokens, seconds, opik)
    ppath = root / "traces" / "verdict" / f"{tid}.input.md"
    packet = schemas.Packet.load(ppath) if ppath.exists() else schemas.Packet(fm={}, sections=())
    summary = summarize(root, tid, packet, v, summary_model)
    actions = recommendations(v, tickets_of(root))
    print(render(root, tid, v, violations, summary, actions, closeout_entry(root, packet), packet.section("Diff stat").strip("`\n "),
                 human_acs_of(root, packet)))
    for x in violations:
        print(f"[verdict] invalid: {x}", file=sys.stderr)
    return violations


def view(root: Path, tid: str) -> Path | None:
    """The Gate 2 page from what is on disk, writing nothing but the html: no stamp, no model call
    (the board serves this when only the JSON is there). None when there is no verdict."""
    vpath = root / "traces" / "verdict" / f"{tid}.json"
    if not vpath.exists():
        return None
    v = schemas.Verdict.load(vpath)
    ppath = root / "traces" / "verdict" / f"{tid}.input.md"
    packet = schemas.Packet.load(ppath) if ppath.exists() else schemas.Packet(fm={}, sections=())
    spath = summary_path(root, tid)
    try:
        summary = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else {"error": "no summary yet"}
    except json.JSONDecodeError:
        summary = {"error": "unreadable summary"}
    return render(root, tid, v, [], summary, recommendations(v, tickets_of(root)), closeout_entry(root, packet),
                  packet.section("Diff stat").strip("`\n "), human_acs_of(root, packet))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket")
    ap.add_argument("--vendor", default=DEFAULT_VENDOR, help="who rendered the verdict; stamped into meta.vendor")
    ap.add_argument("--summary-model", default=DEFAULT_SUMMARY_MODEL, help="model for the two prose sections; `none` skips the call")
    a = ap.parse_args()
    main(Path("."), a.ticket, a.vendor, summary_model=a.summary_model)
