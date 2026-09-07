"""The writes a human used to make by hand in kanban/: a Log entry, a status flip, a routing
override, a commit. runner.py and board.py call these; nothing else writes Log lines in code.
Shapes match what scripts/schemas.py parses (Log, RoutingStamp); the format lives here once.
From a shell, `kanban_ops.py approve|override <n> ...` runs Gate 1 and
`kanban_ops.py ship|reject|child|home|waive <id> ...` Gate 2, all through board.act; `order <n>` lists a plan's tickets in depends_on order (the runner's
--plan walk and the runner skill use both). plan_order lives here so runner.py and board.py read
the plan one way.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

GIT_IDENTITY = ["-c", "user.name=harness-runner", "-c", "user.email=runner@harness"]
STAMP_FIELDS = ("scrutiny", "backend")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def is_ticket_file(name: str, tid: str) -> bool:
    """`<tid>.<slug>.md` and not a child `<tid>.<n>.<slug>.md`: slugs never start with a digit."""
    rest = name[len(tid) + 1:] if name.startswith(tid + ".") else ""
    return bool(rest) and name.endswith(".md") and not name.endswith(".plan.md") and not rest[0].isdigit()


def find_ticket(root: Path, tid: str) -> Path | None:
    return next((p for p in sorted((root / "kanban").rglob("*.md")) if is_ticket_file(p.name, tid)), None)


def find_plan(root: Path, n: str) -> Path | None:
    return next(iter(sorted((root / "kanban").rglob(f"{n}.plan.md"))), None)


def append_log(path: Path, role: str, head: str, lines: tuple[str, ...] = (), when: str | None = None) -> str:
    """Append ``### [role] <timestamp> — <head>`` plus lines to the file's ``## Log`` (the last
    section by template). Returns the entry text."""
    entry = f"### [{role}] {when or now()} — {head}" + "".join(f"\n{l}" for l in lines) + "\n"
    text = path.read_text(encoding="utf-8")
    if "## Log" not in text:
        text = text.rstrip("\n") + "\n\n## Log (append-only)\n"
    path.write_text(text.rstrip("\n") + "\n" + entry, encoding="utf-8")
    return entry


def set_status(path: Path, status: str) -> None:
    """Rewrite the frontmatter ``status:`` value, keeping any trailing comment."""
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"^(status:\s*)\S+", rf"\g<1>{status}", text, count=1, flags=re.MULTILINE)
    if not n:
        raise ValueError(f"{path}: no status: line in frontmatter")
    path.write_text(new, encoding="utf-8")


def override_plan(root: Path, n: str, field: str, value: str, who: str = "human") -> tuple[str, str]:
    """A router miss: set the plan's ``scrutiny`` or ``backend`` stamp to ``value``, mark the
    derivation comment as overridden, append the plan Log entry, and record the miss in
    traces/grill-misses.jsonl with the signals. Returns (old, new)."""
    if field not in STAMP_FIELDS:
        raise ValueError(f"override field must be one of {STAMP_FIELDS}, not {field!r}")
    path = find_plan(root, n)
    if path is None:
        raise FileNotFoundError(f"no plan {n} under kanban/")
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"^{field}:\s*(?P<val>[^#\n]*?)\s*(?:#\s*(?P<rule>[^\n]*))?$", text, re.MULTILINE)
    if not m:
        raise ValueError(f"plan {n} has no {field}: stamp")
    old = m.group("val").strip()
    rule = (m.group("rule") or "").strip()
    line = f"{field}: {value}" + (f"   # {rule} — overridden by {who}, was {old}" if rule else f"   # overridden by {who}, was {old}")
    text = text[: m.start()] + line + text[m.end():]
    path.write_text(text, encoding="utf-8")
    append_log(path, who, f"router miss: {field} {old} → {value}", (f"- rule was: {rule or 'unstated'}",))
    import _fm  # local import keeps this module importable from a bare python3 with scripts/ on the path

    fm, _ = _fm.parse(text)
    signals = {}
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    in_signals = False
    for raw in head.splitlines():
        if raw.startswith("signals:"):
            in_signals = True
            continue
        if in_signals and raw.startswith((" ", "\t")) and ":" in raw:
            k, v = raw.strip().split(":", 1)
            signals[k.strip()] = v.split("#", 1)[0].strip()
        elif in_signals and not raw.startswith((" ", "\t")):
            in_signals = False
    misses = root / "traces" / "grill-misses.jsonl"
    misses.parent.mkdir(parents=True, exist_ok=True)
    with misses.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"plan": n, "source": "override", "field": field, "from": old, "to": value,
                            "signals": signals, "by": who, "ts": now()}) + "\n")
    return old, value


def commit(root: Path, paths: list[str], message: str, force: bool = False) -> str | None:
    """Stage the paths and commit with the harness identity. Returns the short sha, or None when
    nothing changed (or the commit failed; the reason is printed)."""
    subprocess.run(["git", "add", *(["-f"] if force else []), "--", *paths], cwd=root, capture_output=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode == 0:
        return None
    r = subprocess.run(["git", *GIT_IDENTITY, "commit", "-q", "-m", message], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[kanban] commit failed: {r.stderr.strip()[-200:]}")
        return None
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()


def plan_order(root: Path, n: str) -> list[str]:
    """Tickets of plan n in depends_on order (a dependency before its dependants), done and
    superseded ones out. Raises ValueError on a depends_on cycle."""
    import _fm  # local import, as in override_plan
    import schemas

    rows = [(fm["id"], fm.get("status", "ready"), list(fm.get("depends_on") or [])) for _, fm, _ in _fm.tickets(root / "kanban") if str(fm.get("parent", "")) == str(n)]
    order: list[str] = []
    pending = {tid: deps for tid, status, deps in rows if status not in schemas.CLOSED_STATUSES}
    while pending:
        ready = [tid for tid, deps in pending.items() if all(d in order or d not in pending for d in deps)]
        if not ready:
            raise ValueError(f"plan {n}: depends_on cycle among {', '.join(sorted(pending))}")
        for tid in sorted(ready, key=lambda t: [int(x) for x in t.split(".")]):
            order.append(tid)
            pending.pop(tid)
    return order


# --- the command line: the Gate 2 words a session executes ------------------------------------
GATE1 = ("approve", "override")
GATE2 = ("ship", "reject", "child", "home", "waive")


def main(argv: list[str]) -> int:
    """`kanban_ops.py <action> ... [--who <name>] [--cwd .]`: the Gate 1 and Gate 2 actions,
    from a shell. Each call is `board.act` with the form the board used to post, so the Log
    entries, commits and dataset items are identical; nothing here writes on its own. `order <n>`
    prints the plan's tickets in depends_on order, one per line.
      approve <n> · override <n> <scrutiny|backend> <value> ·
      ship <id> · reject <id> <reason> · child <id> <F#> · home <id> <F#> <target> ·
      waive <id> <F#> <reason> · order <n>"""
    import argparse

    ap = argparse.ArgumentParser(description=main.__doc__.splitlines()[0])
    ap.add_argument("action", choices=(*GATE1, *GATE2, "order"))
    ap.add_argument("args", nargs="*")
    ap.add_argument("--who", default="human")
    ap.add_argument("--cwd", default=".")
    a = ap.parse_args(argv[1:])
    root = Path(a.cwd).resolve()
    shapes = {"approve": ("plan",), "override": ("plan", "field", "value"), "ship": ("ticket",), "reject": ("ticket", "reason"), "child": ("ticket", "finding"),
              "home": ("ticket", "finding", "target"), "waive": ("ticket", "finding", "reason"), "order": ("plan",)}
    keys = shapes[a.action]
    if len(a.args) < len(keys):
        print(f"[kanban] {a.action} takes {' '.join('<' + k + '>' for k in keys)}", file=sys.stderr)
        return 2
    form = dict(zip(keys, a.args[:len(keys) - 1]))
    form[keys[-1]] = " ".join(a.args[len(keys) - 1:])  # the last field may be a multi-word reason
    if a.action == "order":
        try:
            print("\n".join(plan_order(root, form["plan"])))
        except ValueError as e:
            print(f"[kanban] {e}", file=sys.stderr)
            return 1
        return 0
    import board  # here, not at the top: board imports kanban_ops

    try:
        print(board.act(root, {"action": a.action, "who": a.who, **form}))
    except (board.BoardError, ValueError, FileNotFoundError) as e:  # override_plan raises ValueError / FileNotFoundError
        print(f"[kanban] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
