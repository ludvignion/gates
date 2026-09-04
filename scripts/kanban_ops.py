"""The writes a human used to make by hand in kanban/: a Log entry, a status flip, a routing
override, a commit. runner.py and board.py call these; nothing else writes Log lines in code.
Shapes match what scripts/schemas.py parses (Log, RoutingStamp); the format lives here once.
"""
import json
import re
import subprocess
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
