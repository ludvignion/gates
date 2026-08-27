#!/usr/bin/env python3
"""kanban/<n>.plan.md → traces/plan/<n>.html. Gate 1 page. Run: make plan N=<n>"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402


def md_to_html(md: str) -> str:
    out, in_table = [], False
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            tag = "th" if not in_table else "td"
            out.append("<table>" if not in_table else "")
            out.append("<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells) + "</tr>")
            in_table = True
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        m = re.match(r"^(#+) (.*)", line)
        if m:
            out.append(f"<h{len(m.group(1))}>{html.escape(m.group(2))}</h{len(m.group(1))}>")
        elif line.startswith("- "):
            out.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    if in_table:
        out.append("</table>")
    return "\n".join(out)


def main(root: Path, n: str) -> None:
    p = root / "kanban" / f"{n}.plan.md"
    fm, body = _fm.read(p)
    mods = re.findall(r"`(src/[^`]+)`", body)
    mermaid = "graph TD\n" + "\n".join(f'  m{i}["{m}"]' for i, m in enumerate(mods))
    banner = (
        f'<div class=ok>APPROVED {html.escape(fm["approved"])}</div>'
        if fm.get("approved") else '<div class=warn>NOT APPROVED — read the assumptions, then add an <code>approved:</code> line.</div>'
    )
    out = f"""<!doctype html><meta charset=utf-8><title>Plan {n}</title>
<style>body{{font-family:system-ui;max-width:900px;margin:2rem auto}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.3rem .6rem}}
.ok{{background:#dfd;padding:.5rem}}.warn{{background:#fdd;padding:.5rem}}</style>
{banner}{md_to_html(body)}
<h2>Module map</h2><pre class="mermaid">{html.escape(mermaid)}</pre>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";mermaid.initialize({{startOnLoad:true}});</script>"""
    d = root / "traces" / "plan"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n}.html").write_text(out)
    print(d / f"{n}.html")


if __name__ == "__main__":
    main(Path("."), sys.argv[1])
