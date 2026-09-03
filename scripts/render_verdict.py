#!/usr/bin/env python3
"""Close-out of one verdict. Run: render_verdict.py <id> [--vendor claude]

Loads traces/verdict/<id>.json and the packet it answered (<id>.input.md) through
scripts/schemas.py, applies verdict_checks.validate for the packet's arm (blind downgrades
uncited blocks; other arms report them on stderr and on the page), stamps `meta` (arm, vendor,
plugin_version, prompt_sha, packet_sha) into the JSON, and writes traces/verdict/<id>.html, the
gate 2 page. Idempotent: a second run re-derives the same stamp. Without a packet (a verdict
written before 0.6.2) the page renders unstamped.
"""
import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import schemas  # noqa: E402
import verdict_checks  # noqa: E402

DEFAULT_VENDOR = "claude"  # the skill runs inside a claude session; runner.py derives it from --verdict-cmd


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


def stamp(root: Path, tid: str, vendor: str, cost_usd: float | None = None, tokens: dict | None = None) -> tuple[schemas.Verdict, list[str]]:
    """Validate against the packet's arm, stamp meta, write the JSON back. Returns the stored
    verdict and the violations. cost_usd/tokens come from the vendor's own report (runner.py);
    a re-run without them keeps what is already stamped."""
    vpath = root / "traces" / "verdict" / f"{tid}.json"
    ppath = root / "traces" / "verdict" / f"{tid}.input.md"
    v = schemas.Verdict.load(vpath)
    if not ppath.exists():
        return v, []
    packet = schemas.Packet.load(ppath)
    v, violations = verdict_checks.validate(v, packet.arm)
    meta = schemas.VerdictMeta(
        arm=packet.arm, vendor=vendor, plugin_version=str(packet.fm.get("plugin_version", "")),
        prompt_sha=str(packet.fm.get("prompt_sha", "")), packet_sha=schemas.sha256(ppath.read_bytes()),
        cost_usd=cost_usd if cost_usd is not None else (v.meta.cost_usd if v.meta else None),
        tokens=tokens if tokens is not None else (v.meta.tokens if v.meta else None),
    )
    v = v.with_meta(meta)
    v.dump(vpath)
    return v, violations


def render(root: Path, tid: str, v: schemas.Verdict, violations: list[str]) -> Path:
    ship = v.decision == "ship"
    is_open = schemas.Verdict.is_open
    fs = v.findings
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
    for f, qs in v.raw.get("quality", {}).items():
        cells = "".join(f"<td{' class=no' if not ok else ''}>{'✓' if ok else '✗'}</td>" for ok in qs.values())
        quality += f"<tr><td>{esc(f)}</td>{cells}</tr>"

    ci = v.ci
    held_html = f"<p class=meta>Held: {esc(', '.join(v.held))}</p>" if v.held else ""
    seat_html = (
        "<p class=meta>Seat: " + " · ".join(f"{k} {esc(val)}" for k, val in v.meta.as_dict().items() if val is not None) + "</p>"
        if v.meta else "<p class=meta>Seat: unstamped</p>"
    )
    invalid_html = (
        "<p class=invalid>Invalid: " + "; ".join(esc(x) for x in violations) + "</p>" if violations else ""
    )
    out = f"""<!doctype html><meta charset=utf-8><title>Verdict {tid}</title>
<style>body{{font-family:system-ui;max-width:1000px;margin:2rem auto}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
.hero{{font-size:2rem;padding:1rem;color:#fff;background:{'#3cb371' if ship else '#c0392b'}}}
li.block{{background:#fdd}}li.warn{{background:#ffe9c7}}li.note{{background:#f4f4f4}}
li{{margin:.3rem 0;padding:.2rem .4rem;list-style:none}}ul{{padding-left:0}}
.meta{{color:#666;font-size:.85rem}}.invalid{{color:#c0392b;font-weight:bold}}td.no{{background:#fdd}}details{{margin:.4rem 0}}</style>
<div class=hero>{'SHIP' if ship else 'REJECT'} — {tid}</div>
<p>CI: {'green' if ci.get('green') else 'RED'} · mutation: {ci.get('mutation_score','n/a')}</p>
{seat_html}
{invalid_html}
{held_html}
<h2>Blocks ({len(blocks)})</h2><ul>{''.join(li(f) for f in blocks) or '<li>none</li>'}</ul>
<h2>Warnings ({len(warns)})</h2><ul>{''.join(li(f) for f in warns) or '<li>none</li>'}</ul>
<h2>Notes ({len(notes)})</h2>{notes_html or '<p class=meta>none</p>'}
{resolved_html}
<h2>Quality</h2><table><tr><th>File</th><th>1 SRP</th><th>2 AC-trace</th><th>3 YAGNI</th><th>4 rule-of-3</th><th>5 ISP</th><th>6 DIP</th></tr>{quality}</table>"""
    out_path = root / "traces" / "verdict" / f"{tid}.html"
    out_path.write_text(out, encoding="utf-8")
    return out_path


def main(root: Path, tid: str, vendor: str = DEFAULT_VENDOR, cost_usd: float | None = None, tokens: dict | None = None) -> list[str]:
    """Stamp, render, print the page path; violations go to stderr and come back to the caller."""
    v, violations = stamp(root, tid, vendor, cost_usd, tokens)
    print(render(root, tid, v, violations))
    for x in violations:
        print(f"[verdict] invalid: {x}", file=sys.stderr)
    return violations


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ticket")
    ap.add_argument("--vendor", default=DEFAULT_VENDOR, help="who rendered the verdict; stamped into meta.vendor")
    a = ap.parse_args()
    main(Path("."), a.ticket, a.vendor)
