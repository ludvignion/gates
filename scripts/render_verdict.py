#!/usr/bin/env python3
"""traces/verdict/<id>.json → traces/verdict/<id>.html. Gate 2 page. Run: make verdict T=<id>"""
import html
import json
import sys
from pathlib import Path


def main(root: Path, tid: str) -> None:
    v = json.loads((root / "traces" / "verdict" / f"{tid}.json").read_text())
    ship = v["decision"] == "ship"
    attacks = "".join(
        f"<tr class={a.get('severity','note')}><td>{html.escape(a['attack'])}</td><td>{html.escape(a.get('covered_by') or '—')}</td><td>{a.get('severity','')}</td></tr>"
        for a in v.get("attacks", [])
    )
    quality = ""
    for f, qs in v.get("quality", {}).items():
        cells = "".join(f"<td>{'✓' if ok else '✗'}</td>" for ok in qs.values())
        quality += f"<tr><td>{html.escape(f)}</td>{cells}</tr>"
    findings = "".join(
        f"<li class={x['severity']}><b>{x['severity']}</b> {html.escape(x['text'])}{' → child ticket' if x.get('spawn_child') else ''}</li>"
        for x in v.get("findings", [])
    )
    ci = v.get("ci", {})
    out = f"""<!doctype html><meta charset=utf-8><title>Verdict {tid}</title>
<style>body{{font-family:system-ui;max-width:1000px;margin:2rem auto}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.hero{{font-size:2rem;padding:1rem;color:#fff;background:{'#3cb371' if ship else '#c0392b'}}}.block{{background:#fdd}}.warn{{background:#ffe9c7}}</style>
<div class=hero>{'SHIP' if ship else 'REJECT'} — {tid}</div>
<p>CI: {'green' if ci.get('green') else 'RED'} · mutation: {ci.get('mutation_score','n/a')}</p>
<h2>Findings</h2><ul>{findings or '<li>none</li>'}</ul>
<h2>Adversarial pass</h2><table><tr><th>Attack</th><th>Covered by</th><th>Severity</th></tr>{attacks}</table>
<h2>Quality</h2><table><tr><th>File</th><th>1 SRP</th><th>2 AC-trace</th><th>3 YAGNI</th><th>4 rule-of-3</th><th>5 ISP</th><th>6 DIP</th></tr>{quality}</table>"""
    (root / "traces" / "verdict" / f"{tid}.html").write_text(out)
    print(root / "traces" / "verdict" / f"{tid}.html")


if __name__ == "__main__":
    main(Path("."), sys.argv[1])
