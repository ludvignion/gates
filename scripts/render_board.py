#!/usr/bin/env python3
"""kanban/ → traces/board.html. Progress per plan + status columns + dependency graph.

Runs on every Stop hook (hooks/hooks.json) and on `make board`. No-op when the project has
no kanban/ directory. AC citations and slice lists load through scripts/schemas.py.
"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import schemas  # noqa: E402

STATUSES = ["ready", "in_progress", "in_review", "done", "superseded"]
COLORS = {
    "ready": "#9aa",
    "in_progress": "#e8a33d",
    "in_review": "#4a90e2",
    "done": "#3cb371",
    "superseded": "#b8a0d8",
}
REACHED = ("done", "in_review")  # an AC is reached when a ticket in one of these cites it
# Best status when several tickets cite one AC; superseded ranks below missing work.
RANK = {"done": 0, "in_review": 1, "in_progress": 2, "ready": 3, "superseded": 4}


def _plan_key(k: str):
    return (0, int(k)) if k.isdigit() else (1, k)


def progress(plans: dict[str, tuple[dict, str]], rows: list[tuple[Path, dict, str]]) -> list[dict]:
    """One record per plan: counts for the row, per-AC detail for the lines below it."""
    by_id = {fm["id"]: (fm.get("status", "ready"), body) for _, fm, body in rows}
    out = []
    for n, (_, plan_body) in sorted(plans.items(), key=lambda kv: _plan_key(kv[0])):
        plan = schemas.Plan.parse(plan_body)
        # AC -> [(ticket id, status)] from citations inside ticket AC lines
        cited: dict[str, list[tuple[str, str]]] = {ac: [] for ac in plan.acs}
        for tid, (status, body) in sorted(by_id.items()):
            for ac in sorted(schemas.TicketCitations.parse(body).acs_for(n), key=_ac_key):
                cited.setdefault(ac, []).append((tid, status))
        done = {ac for ac, ts in cited.items() if any(s == "done" for _, s in ts)}
        review = {ac for ac, ts in cited.items() if ac not in done and any(s == "in_review" for _, s in ts)}
        slice_status = {sid: by_id[sid][0] if sid in by_id else "missing" for sid in plan.slices}
        counts = {s: sum(1 for v in slice_status.values() if v == s) for s in STATUSES + ["missing"]}
        out.append(
            {
                "plan": n,
                "acs_total": len(plan.acs),
                "acs_done": len(done & set(plan.acs)),
                "acs_review": len(review & set(plan.acs)),
                "slices_total": len(plan.slices),
                "slices_built": sum(1 for v in slice_status.values() if v in REACHED),
                "counts": counts,
                "missing": [sid for sid, v in slice_status.items() if v == "missing"],
                "acs": [
                    (ac, ts, min((s for _, s in ts), key=lambda s: RANK.get(s, 9), default=None))
                    for ac, ts in cited.items()
                ],
            }
        )
    return out


def _ac_key(ac: str):
    return int(ac.split("-")[1])


def progress_row(rec: dict) -> str:
    """The one-line summary a person reads first; see kanban/briefs/3-board-progress.md."""
    c = rec["counts"]
    extra = "".join(f", {s} {c[s]}" for s in ("in_progress", "superseded") if c[s])
    return (
        f"Plan {rec['plan']} — ACs reached {rec['acs_done'] + rec['acs_review']}/{rec['acs_total']}"
        f" · slices {rec['slices_built']}/{rec['slices_total']}"
        f" · tickets done {c['done']}, in_review {c['in_review']}, ready {c['ready']}, missing {c['missing']}{extra}"
    )


def progress_html(records: list[dict]) -> str:
    parts = []
    for rec in records:
        total = rec["acs_total"] or 1
        w_done, w_rev = 100 * rec["acs_done"] / total, 100 * rec["acs_review"] / total
        missing = f" · missing: {html.escape(', '.join(rec['missing']))}" if rec["missing"] else ""
        ac_lines = "".join(
            f"<li><b>{html.escape(ac)}</b> → "
            + (
                ", ".join(f"{html.escape(t)} ({html.escape(s)})" for t, s in ts) + f" · best: {html.escape(best)}"
                if ts
                else "not yet cited"
            )
            + "</li>"
            for ac, ts, best in rec["acs"]
        )
        parts.append(
            f'<div class="plan"><div class="row">{html.escape(progress_row(rec))}{missing}</div>'
            f'<div class="bar"><span class="done" style="width:{w_done:.1f}%"></span>'
            f'<span class="review" style="width:{w_rev:.1f}%"></span></div>'
            f'<ul class="acs">{ac_lines}</ul></div>'
        )
    return "".join(parts) or "<p>no plans</p>"


def main(root: Path) -> None:
    kanban = root / "kanban"
    if not kanban.is_dir():
        return
    rows = _fm.tickets(kanban)
    plans = {p.stem.split(".")[0]: _fm.read(p) for p in kanban.rglob("*.plan.md")}
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
        for k, (v, _) in sorted(plans.items(), key=lambda kv: _plan_key(kv[0]))
    )
    out = f"""<!doctype html><meta charset=utf-8><title>Board</title>
<style>body{{font-family:system-ui;margin:2rem}}.cols{{display:flex;gap:1rem}}.col{{flex:1;background:#f6f6f6;padding:.5rem 1rem;border-radius:8px}}ul{{padding-left:1rem}}
.plan{{margin:0 0 1rem}}.row{{font-weight:600}}.bar{{display:flex;height:.6rem;width:100%;max-width:40rem;background:#e4e4e4;border-radius:4px;overflow:hidden;margin:.3rem 0}}
.bar .done{{background:{COLORS["done"]}}}.bar .review{{background:{COLORS["in_review"]}}}.acs{{font-size:.9em;color:#444}}</style>
<h1>Board</h1><h2>Plans</h2><ul>{plan_html}</ul>
<h2>Progress</h2>{progress_html(progress(plans, rows))}
<h2>Tickets</h2><div class=cols>{col_html}</div>
<h2>Dependencies</h2><pre class="mermaid">{html.escape(mermaid)}</pre>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";mermaid.initialize({{startOnLoad:true}});</script>"""
    (root / "traces").mkdir(exist_ok=True)
    (root / "traces" / "board.html").write_text(out)
    print("traces/board.html")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
