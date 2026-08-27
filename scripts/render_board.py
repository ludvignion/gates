#!/usr/bin/env python3
"""kanban/ → traces/board.html. Status columns + dependency graph. Run: make board"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402

STATUSES = ["ready", "in_progress", "in_review", "done"]
COLORS = {"ready": "#9aa", "in_progress": "#e8a33d", "in_review": "#4a90e2", "done": "#3cb371"}


def main(root: Path) -> None:
    rows = _fm.tickets(root / "kanban")
    plans = {p.stem.split(".")[0]: _fm.read(p)[0] for p in (root / "kanban").glob("*.plan.md")}
    cols = {s: [] for s in STATUSES}
    edges = []
    for p, fm, body in rows:
        title = next((l[2:] for l in body.splitlines() if l.startswith("# ")), p.stem)
        cols.setdefault(fm.get("status", "ready"), []).append((fm["id"], title))
        for d in fm.get("depends_on", []) or []:
            edges.append((d, fm["id"]))
    mermaid = "graph LR\n" + "\n".join(
        f'  {a.replace(".", "_")}["{a}"] --> {b.replace(".", "_")}["{b}"]' for a, b in edges
    )
    for p, fm, _ in rows:
        mermaid += f'\n  style {fm["id"].replace(".", "_")} fill:{COLORS.get(fm.get("status"), "#ccc")}'
    col_html = ""
    for s in STATUSES:
        items = "".join(f"<li><b>{html.escape(i)}</b> {html.escape(t)}</li>" for i, t in cols.get(s, []))
        col_html += f'<div class="col"><h3 style="color:{COLORS[s]}">{s} ({len(cols.get(s, []))})</h3><ul>{items}</ul></div>'
    plan_html = "".join(
        f"<li>Brief {k}: {html.escape(v.get('status', '?'))}"
        f"{' — approved ' + html.escape(v['approved']) if v.get('approved') else ''}</li>"
        for k, v in sorted(plans.items())
    )
    out = f"""<!doctype html><meta charset=utf-8><title>Board</title>
<style>body{{font-family:system-ui;margin:2rem}}.cols{{display:flex;gap:1rem}}.col{{flex:1;background:#f6f6f6;padding:.5rem 1rem;border-radius:8px}}ul{{padding-left:1rem}}</style>
<h1>Board</h1><h2>Plans</h2><ul>{plan_html}</ul>
<h2>Tickets</h2><div class=cols>{col_html}</div>
<h2>Dependencies</h2><pre class="mermaid">{html.escape(mermaid)}</pre>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";mermaid.initialize({{startOnLoad:true}});</script>"""
    (root / "traces").mkdir(exist_ok=True)
    (root / "traces" / "board.html").write_text(out)
    print("traces/board.html")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
