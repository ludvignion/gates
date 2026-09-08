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
  warn names, else "waive".
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
    return (
        f"<li class={f['severity']}><b>{esc(f.get('id'))}</b> <code>{esc(cite)}</code> "
        f"{esc(f['text'])}{' → child ticket' if f.get('spawn_child') else ''}"
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
    v, violations = verdict_checks.validate(v, packet.arm, packet)
    v = replace(v, ticket=packet.ticket or tid)
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
    """[(finding id, action)] for every open block and warn; [] means ship as is."""
    out = []
    child = next_child(v.ticket, tickets)
    for f in v.findings:
        if not v.is_open(f):
            continue
        if f.get("severity") == "block":
            out.append((f["id"], f"ship, create child {child} from {f['id']}" if f.get("spawn_child") else "rework in place"))
        elif f.get("severity") == "warn":
            home = f.get("home") or home_for(str(f.get("text", "")), v.ticket, tickets)
            out.append((f["id"], f"home to {home}" if home else "waive"))
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


def charter_line(charter: dict) -> str:
    """The result block's charter line (E22: only what the diff could reach is mentioned):
    "charter: 2, 7 reachable — both held" (one item "held", more than two "all held"),
    "charter: 4 reachable — F3" (the findings citing the unheld items, comma-joined), or
    "charter: none reachable". `charter` is verdict_checks.charter_report's dict."""
    reachable = [str(i) for i in charter.get("reachable") or ()]
    if not reachable:
        return "charter: none reachable"
    held = set(charter.get("held") or ())
    findings = charter.get("findings") or {}
    nums = ", ".join(i.removeprefix("charter-") for i in reachable)
    unheld = [i for i in reachable if i not in held]
    if not unheld:
        return f"charter: {nums} reachable — {('held', 'both held')[len(reachable) - 1] if len(reachable) <= 2 else 'all held'}"
    cited = [fid for i in unheld for fid in findings.get(i, ())]
    return f"charter: {nums} reachable — " + (", ".join(cited) if cited else ", ".join(f"{i} unaccounted" for i in unheld))


def human_ac(item) -> tuple[str, str]:
    """(id, text) from a schemas.HumanAc, a pair, or an AC line "AC-7 (human): text"."""
    if isinstance(item, str):
        m = re.match(r"^(?:-\s*)?(?P<id>AC-\d+)\s*(?:\(human\))?\s*:\s*(?P<text>.*)$", item.strip())
        return (m.group("id"), m.group("text").strip()) if m else (item.strip(), "")
    if hasattr(item, "id") and hasattr(item, "text"):
        return str(item.id), str(item.text)
    return str(item[0]), str(item[1])


def changed_lines(changed: dict) -> list[str]:
    """"changed vs <base>: N files +A/−B" and one indented line per file (E27), from
    verdict_prep.changed_vs_base's dict."""
    files = list(changed.get("files") or ())
    added, removed = sum(int(f.get("added") or 0) for f in files), sum(int(f.get("removed") or 0) for f in files)
    out = [f"changed vs {changed.get('base') or 'main'}: {len(files)} files +{added}/−{removed}"]
    out += [f"  {f.get('path')} +{int(f.get('added') or 0)}/−{int(f.get('removed') or 0)}" for f in files]
    return out


def _usd(x: "float | None") -> str:
    return f"${x:.4f}" if x is not None else "n/a"


def cost_words(cost_usd: "float | None", seconds: float, costs: "dict | None") -> str:
    """The header's cost words. With `costs` ({"verdict_usd", "verdict_s", "build_usd", "build_s"})
    the verdict call and the build session are named apart from the whole run (E28: a header
    that summed them read as a ten-times verdict); without, the total and the run time."""
    if not costs:
        return f"{_usd(cost_usd)} · {int(seconds)}s"
    def part(name: str) -> str:
        s = costs.get(f"{name}_s")
        return f"{name} {_usd(costs.get(f'{name}_usd'))}/{int(s) if s is not None else 'n/a'}s"
    return f"{part('verdict')} · {part('build')} · run {int(seconds)}s"


def result_lines(v: schemas.Verdict, tickets: list[tuple[str, str, list[str]]], cost_usd: float | None,
                 seconds: float, page: str, *, summary: dict | None = None, changed: dict | None = None,
                 charter: dict | None = None, human_acs=(), costs: dict | None = None) -> list[str]:
    """The end of a run as the skill prints it and traces/runs/<id>.result stores it (E19,
    contract C), in this order: the summary sentences ("built", then "review"; nothing when the
    summary is missing or errored, E24); the header; one line per open block or warn in verdict
    order with its citation, file:line and the recommended action; the charter line (E22); one
    "human: AC-n — <text> (confirm with ship)" line per human AC (E30); what changed vs the base
    (E27); the "Recommended:" line; the page path. Pure code from verdict.json and the dicts the
    runner hands over; the actions are recommendations(), never a model call."""
    acts = dict(recommendations(v, tickets))
    open_ = [f for f in v.findings if v.is_open(f) and f.get("severity") in ("block", "warn")]
    blocks = [f for f in open_ if f["severity"] == "block"]
    warns = [f for f in open_ if f["severity"] == "warn"]
    out: list[str] = []
    if summary and not summary.get("error"):
        out += [str(summary[k]).strip() for k in ("built", "review") if summary.get(k)]
    out.append(f"{v.ticket} · verdict: {v.decision.upper()} · {len(blocks)} blocks · {len(warns)} warns · {cost_words(cost_usd, seconds, costs)}")
    words = []
    for f in open_:
        fid = str(f.get("id"))
        cite = f.get("ac") or f.get("charter") or "—"
        word = action_word(acts[fid]) if fid in acts else "—"
        out.append(f'{fid} {f["severity"]} ({cite}) {finding_file(str(f.get("text", "")))} — "{f.get("text", "")}" → {word}')
        if word != "—":
            verb, _, target = word.partition(" ")
            words.append(f"{verb} {fid} → {target}" if target else f"{verb} {fid}")
    if charter is not None:
        out.append(charter_line(charter))
    out += [f"human: {aid} — {text} (confirm with ship)" for aid, text in map(human_ac, human_acs)]
    if changed is not None:
        out += changed_lines(changed)
    if not open_:
        out.append("Recommended: ship as is")
    else:
        decision = "reject" if any(not f.get("spawn_child") for f in blocks) else "ship"
        out.append(f"Recommended: {decision}, " + ", ".join(words))
    out.append(page)
    return out


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
    actions_html = "".join(f"<li><b>{esc(fid)}</b> {esc(a)}</li>" for fid, a in actions) or "<li>ship as is</li>"
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
