#!/usr/bin/env python3
"""traces/verdict/<id>.json → traces/verdict/<id>.html. Gate 2 page. Run: make verdict T=<id>

Also archives <id>.json as <id>.prev.json so the next verdict on this ticket runs in retry mode.
"""
import html
import json
import shutil
import sys
from pathlib import Path


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


def main(root: Path, tid: str) -> None:
    v = json.loads((root / "traces" / "verdict" / f"{tid}.json").read_text())
    ship = v["decision"] == "ship"
    fs = v.get("findings", [])
    is_open = lambda f: f.get("status", "open") == "open"
    blocks = [f for f in fs if f["severity"] == "block" and is_open(f)]
    warns = [f for f in fs if f["severity"] == "warn" and is_open(f)]
    notes = [f for f in fs if f["severity"] == "note" and is_open(f)]
    resolved = [f for f in fs if not is_open(f)]

    by_home: dict[str, list[dict]] = {}
    for f in notes:
        by_home.setdefault(f.get("home") or "this ticket", []).append(f)
    notes_html = "".join(
        f"<details><summary>{esc(h)} ({len(g)})</summary><ul>{''.join(li(f) for f in g)}</ul></details>"
        for h, g in sorted(by_home.items())
    )
    resolved_html = (
        f"<details><summary>Resolved ({len(resolved)})</summary><ul>{''.join(li(f) for f in resolved)}</ul></details>"
        if resolved else ""
    )

    quality = ""
    for f, qs in v.get("quality", {}).items():
        cells = "".join(f"<td{' class=no' if not ok else ''}>{'✓' if ok else '✗'}</td>" for ok in qs.values())
        quality += f"<tr><td>{esc(f)}</td>{cells}</tr>"

    ci = v.get("ci", {})
    held = v.get("held", [])
    held_html = f"<p class=meta>Held: {esc(', '.join(held))}</p>" if held else ""
    out = f"""<!doctype html><meta charset=utf-8><title>Verdict {tid}</title>
<style>body{{font-family:system-ui;max-width:1000px;margin:2rem auto}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.hero{{font-size:2rem;padding:1rem;color:#fff;background:{'#3cb371' if ship else '#c0392b'}}}
li.block{{background:#fdd}}li.warn{{background:#ffe9c7}}li.note{{background:#f4f4f4}}
li{{margin:.3rem 0;padding:.2rem .4rem;list-style:none}}ul{{padding-left:0}}
.meta{{color:#666;font-size:.85rem}}td.no{{background:#fdd}}details{{margin:.4rem 0}}</style>
<div class=hero>{'SHIP' if ship else 'REJECT'} — {tid}</div>
<p>CI: {'green' if ci.get('green') else 'RED'} · mutation: {ci.get('mutation_score','n/a')}</p>
{held_html}
<h2>Blocks ({len(blocks)})</h2><ul>{''.join(li(f) for f in blocks) or '<li>none</li>'}</ul>
<h2>Warnings ({len(warns)})</h2><ul>{''.join(li(f) for f in warns) or '<li>none</li>'}</ul>
<h2>Notes ({len(notes)})</h2>{notes_html or '<p class=meta>none</p>'}
{resolved_html}
<h2>Quality</h2><table><tr><th>File</th><th>1 SRP</th><th>2 AC-trace</th><th>3 YAGNI</th><th>4 rule-of-3</th><th>5 ISP</th><th>6 DIP</th></tr>{quality}</table>"""
    (root / "traces" / "verdict" / f"{tid}.html").write_text(out, encoding="utf-8")
    shutil.copyfile(root / "traces" / "verdict" / f"{tid}.json", root / "traces" / "verdict" / f"{tid}.prev.json")
    print(root / "traces" / "verdict" / f"{tid}.html")


if __name__ == "__main__":
    main(Path("."), sys.argv[1])