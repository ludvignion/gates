#!/usr/bin/env python3
"""kanban/<n>.plan.md → traces/plan/<n>.html. Gate 1 page. Run: make plan N=<n>"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import lint_trace  # noqa: E402


def md_to_html(md: str, in_dependencies: bool = False) -> str:
    out, in_table = [], False
    current_section = None

    for line in md.splitlines():
        # Track which section we're in
        if line.startswith("## Dependencies"):
            current_section = "dependencies"
        elif line.startswith("##"):
            current_section = None

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            tag = "th" if not in_table else "td"
            out.append("<table>" if not in_table else "")

            # Check if this is a Dependencies row with empty evidence (last column)
            row_class = ""
            if current_section == "dependencies" and tag == "td" and len(cells) == 3:
                evidence = cells[2]
                if not evidence or evidence in ("—", "TBD", ""):
                    row_class = ' class="red-row"'

            out.append(f"<tr{row_class}>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells) + "</tr>")
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
    p = next((root / "kanban").rglob(f"{n}.plan.md"), None)
    if p is None:
        raise SystemExit(f"no plan {n}.plan.md under {root / 'kanban'}")
    fm, body = _fm.read(p)

    # Check for trace violations
    violations = lint_trace.lint_trace(root, n)
    violations_box = ""
    if violations:
        violations_html = "<br>".join(html.escape(v) for v in violations)
        violations_box = f'<div class="error">Trace violations:<br>{violations_html}</div>'

    # Extract blocking risks
    blocking_risks = []
    match = re.search(r"## Blocking risks\n(.*?)(?=\n##|$)", body, re.DOTALL)
    if match:
        content = match.group(1).strip()
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("- ") and line != "- <capability nodes without evidence; empty means none>":
                blocking_risks.append(line[2:])

    blocking_box = ""
    if blocking_risks:
        risks_html = "<br>".join(html.escape(r) for r in blocking_risks)
        blocking_box = f'<div class="error">Blocking risks:<br>{risks_html}</div>'
    else:
        blocking_box = '<div class="ok">No blocking risks</div>'

    # Check for Dependencies section
    has_dependencies = "## Dependencies" in body
    dependencies_warning = ""
    if not has_dependencies:
        dependencies_warning = '<div class="warn">No Dependencies table — grill skipped capability nodes</div>'

    mods = re.findall(r"`(src/[^`]+)`", body)
    mermaid = "graph TD\n" + "\n".join(f'  m{i}["{m}"]' for i, m in enumerate(mods))
    banner = (
        f'<div class=ok>APPROVED {html.escape(fm["approved"])}</div>'
        if fm.get("approved") else '<div class=warn>NOT APPROVED — read the assumptions, then add an <code>approved:</code> line.</div>'
    )
    out = f"""<!doctype html><meta charset=utf-8><title>Plan {n}</title>
<style>body{{font-family:system-ui;max-width:900px;margin:2rem auto}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.3rem .6rem}}
.ok{{background:#dfd;padding:.5rem}}.warn{{background:#ffd;padding:.5rem}}.error{{background:#fdd;padding:.5rem;margin:.5rem 0}}
.red-row{{background:#fdd}}</style>
{banner}{blocking_box}{dependencies_warning}{violations_box}{md_to_html(body)}
<h2>Module map</h2><pre class="mermaid">{html.escape(mermaid)}</pre>
<script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";mermaid.initialize({{startOnLoad:true}});</script>"""
    d = root / "traces" / "plan"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n}.html").write_text(out)
    print(d / f"{n}.html")


if __name__ == "__main__":
    main(Path("."), sys.argv[1])
