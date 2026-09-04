"""One model call through a shell command, and how its report is read.

runner.py (verdict seat, build seat) and render_verdict.py (summary) share this: run a command,
capture its stdout, read the JSON envelope claude --output-format json prints (`result`,
`total_cost_usd`, `usage`, `permission_denials`, ...), and pull a JSON object out of the reply
text. No third-party dependency.
"""
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def report(stdout: str) -> dict | None:
    """stdout as one JSON object (claude --output-format json), else None."""
    try:
        d = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def usage(stdout: str) -> tuple[float | None, dict | None]:
    """(cost_usd, tokens) from a report carrying total_cost_usd / usage; (None, None) otherwise."""
    d = report(stdout)
    if d is None:
        return None, None
    cost, use = d.get("total_cost_usd"), d.get("usage")
    return (float(cost) if isinstance(cost, (int, float)) else None), (dict(use) if isinstance(use, dict) else None)


def object_in(text: str, key: str) -> dict | None:
    """The first JSON object in `text` (fenced or bare) that has `key`; None if there is none."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
    return None


def object_in_result(stdout: str, key: str) -> dict | None:
    """object_in over the report's `result` text (the model's reply)."""
    d = report(stdout)
    text = d.get("result") if d else None
    return object_in(text, key) if isinstance(text, str) else None


@dataclass(frozen=True)
class Call:
    stdout: str
    stderr: str
    returncode: int
    wall: float
    started: datetime

    @property
    def cost_usd(self) -> float | None:
        return usage(self.stdout)[0]

    @property
    def tokens(self) -> dict | None:
        return usage(self.stdout)[1]


def run(cmd: str, cwd: Path, stdin_text: str | None = None) -> Call:
    """Run one shell command, captured, timed. stderr is passed through to ours."""
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, input=stdin_text)
    wall = time.monotonic() - t0
    sys.stderr.write(r.stderr)
    return Call(r.stdout, r.stderr, r.returncode, wall, started)
