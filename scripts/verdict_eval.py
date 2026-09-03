#!/usr/bin/env python3
"""Compare verdict seats on a project's archived packets. Run: verdict_eval.py <project> [--arm packet] [--verdict-cmd "<template>"]

Loads every traces/verdict/<id>.input.md under the project into an Opik dataset (one item per
packet, its id derived from the packet sha so a rerun replaces and never duplicates: ticket, packet
text, its arm and sha, and the expected gate-2 decision), then runs one
arm × one verdict command over the dataset as one Opik experiment. Expected decision: the
archived <id>.json's decision when its meta.packet_sha matches the packet, else the last
`### [verdict] … — ship|reject` entry in the ticket's Log, else null.

Each run is the runner's seat, replayed: the packet is re-rendered under --arm (a blind packet
cannot be widened; that item errors), written into a scratch tree, the command runs there with
{packet}/{output} filled, and render_verdict.stamp applies the same close-out. The scratch tree
has no repository, so the repo arm has nothing extra to read here.

Metrics, all code: block_count, finding_count, citation_compliance, decision_agreement (when
expected is present), wall_seconds. With the opik package and OPIK_URL_OVERRIDE the run is an Opik
experiment; without them the same items are run and scored locally and printed, one line each,
which is what CI does over tests/fixtures/project with scripts/verdict_canned.py as the command.
"""
import argparse
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402
import render_verdict  # noqa: E402
import runner  # noqa: E402
import schemas  # noqa: E402
import verdict_prep  # noqa: E402

_VERDICT_HEAD_RE = re.compile(r"—\s*(?P<decision>ship|reject)\b")


# --- dataset ------------------------------------------------------------------------------
def expected_decision(project: Path, tid: str, packet_sha: str) -> str | None:
    """The gate-2 decision this packet got, when recoverable: the stamped verdict that answered
    exactly this packet, else the ticket Log's last verdict entry, else None."""
    vpath = project / "traces" / "verdict" / f"{tid}.json"
    if vpath.exists():
        try:
            v = schemas.Verdict.load(vpath)
            if v.meta and v.meta.packet_sha == packet_sha:
                return v.decision
        except ValueError:
            pass
    tickets = [p for p in (project / "kanban").rglob(f"{tid}.*.md") if not p.name.endswith(".plan.md")] if (project / "kanban").is_dir() else []
    if tickets:
        log = schemas.Log.parse(_fm.read(tickets[0])[1])
        for e in reversed(log.entries):
            if e.role == "verdict" and (m := _VERDICT_HEAD_RE.search(e.head)):
                return m.group("decision")
    return None


def item_id(packet_sha: str) -> str:
    """The dataset item id for a packet: a UUID (v7 layout, as Opik issues them) carved from the
    packet sha, so the same packet always maps to the same item and a rerun replaces it."""
    h = int(packet_sha[:32], 16)
    h = (h & ~(0xF << 76)) | (0x7 << 76)        # version nibble = 7
    h = (h & ~(0x3 << 62)) | (0x2 << 62)        # variant bits = 10
    return str(uuid.UUID(int=h))


def items(project: Path) -> list[dict]:
    out = []
    for ppath in sorted((project / "traces" / "verdict").glob("*.input.md")):
        text = ppath.read_text(encoding="utf-8")
        packet = schemas.Packet.parse(text)
        sha = schemas.sha256(ppath.read_bytes())
        tid = packet.ticket or ppath.name[: -len(".input.md")]
        out.append({"id": item_id(sha), "ticket": tid, "packet": text, "packet_arm": packet.arm, "packet_sha": sha,
                    "expected": expected_decision(project, tid, sha)})
    return out


# --- one run ------------------------------------------------------------------------------
def run_one(item: dict, arm: str, template: str, model: str = runner.DEFAULT_VERDICT_MODEL) -> dict:
    """Replay one packet under `arm` with `template` in a scratch tree. Returns the task output
    the metrics read: {"output": verdict dict (empty when missing), "wall_seconds": float}."""
    tid = item["ticket"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        vdir = root / "traces" / "verdict"
        vdir.mkdir(parents=True)
        out_rel = f"traces/verdict/{tid}.json"
        packet = schemas.Packet.parse(item["packet"]).rearm(arm, output=out_rel)
        ppath = vdir / f"{tid}.input.md"
        ppath.write_text(packet.render(), encoding="utf-8")
        cmd = runner.verdict_cmd(template, packet=f"traces/verdict/{tid}.input.md", output=out_rel, model=model, ticket=tid)
        _stdout, cost_usd, tokens, wall, _started = runner.call_vendor(cmd, root, root / out_rel)
        if not (root / out_rel).exists():
            return {"output": {}, "wall_seconds": wall}
        v, _violations = render_verdict.stamp(root, tid, runner.vendor_of(template), cost_usd, tokens)
        return {"output": v.as_dict(), "wall_seconds": wall}


# --- metrics (pure; METRICS is the one list, wrapped for opik in metrics()) --------------
def block_count(output: dict) -> float:
    return float(len(schemas.Verdict.from_dict(output).open_blocks())) if output else 0.0


def finding_count(output: dict) -> float:
    return float(len(schemas.Verdict.from_dict(output).findings)) if output else 0.0


def citation_compliance(output: dict) -> float:
    """Blocks that cite ac or charter, over all blocks; 1.0 with no blocks."""
    if not output:
        return 0.0
    v = schemas.Verdict.from_dict(output)
    blocks = v.blocks()
    return 1.0 if not blocks else sum(1 for f in blocks if v.cited(f)) / len(blocks)


def decision_agreement(output: dict, expected: str | None) -> float | None:
    """None when there is no expected decision to agree with."""
    if expected is None:
        return None
    return 1.0 if output and output.get("decision") == expected else 0.0


METRICS = {
    "block_count": lambda output, expected, wall_seconds: block_count(output),
    "finding_count": lambda output, expected, wall_seconds: finding_count(output),
    "citation_compliance": lambda output, expected, wall_seconds: citation_compliance(output),
    "decision_agreement": lambda output, expected, wall_seconds: decision_agreement(output, expected),
    "wall_seconds": lambda output, expected, wall_seconds: wall_seconds,
}


def score(output: dict, expected: str | None, wall_seconds: float) -> dict:
    """Every metric for one run; None where a metric has nothing to score."""
    return {name: fn(output, expected, wall_seconds) for name, fn in METRICS.items()}


def metrics() -> list:
    from opik.evaluation.metrics import BaseMetric, score_result

    def wrap(name: str, fn):
        class _M(BaseMetric):
            def __init__(self):
                super().__init__(name=name, track=False)

            def score(self, output: dict, expected: str | None = None, wall_seconds: float = 0.0, **_):
                value = fn(output, expected, wall_seconds)
                if value is None:
                    return score_result.ScoreResult(name=name, value=0.0, scoring_failed=True, reason="no expected decision")
                return score_result.ScoreResult(name=name, value=float(value))

        _M.__name__ = name
        return _M()

    return [wrap(name, fn) for name, fn in METRICS.items()]


def score_local(rows: list[dict], arm: str, template: str) -> list[dict]:
    """The experiment without Opik: run every item, score it here. What CI runs."""
    out = []
    for item in rows:
        r = run_one(item, arm, template)
        out.append({"ticket": item["ticket"], **score(r["output"], item["expected"], r["wall_seconds"])})
    return out


# --- entry -------------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", help="path of a project with traces/verdict/*.input.md")
    ap.add_argument("--arm", choices=schemas.ARMS, default=schemas.DEFAULT_ARM)
    ap.add_argument("--verdict-cmd", default=runner.DEFAULT_VERDICT_CMD, metavar="TEMPLATE", help=runner.VERDICT_CMD_HELP)
    a = ap.parse_args(argv[1:])
    project = Path(a.project).resolve()
    rows = items(project)
    with_expected = sum(1 for r in rows if r["expected"])
    print(f"[eval] {len(rows)} packets, {with_expected} with an expected decision")
    if not rows:
        return 1
    client = runner.opik_client()
    if client is None:
        print("[eval] opik not importable or OPIK_URL_OVERRIDE unset; scoring locally, nothing uploaded")
        for row in score_local(rows, a.arm, a.verdict_cmd):
            print("[eval] " + " ".join(f"{k}={v if v is None else round(v, 3) if isinstance(v, float) else v}" for k, v in row.items()))
        return 0
    from opik.evaluation import evaluate

    dataset = client.get_or_create_dataset(name=f"verdict-packets-{project.name}")
    dataset.insert(rows)
    vendor = runner.vendor_of(a.verdict_cmd)
    prompt_sha = schemas.sha256(verdict_prep.prompt_text())
    result = evaluate(
        dataset=dataset,
        task=lambda item: run_one(item, a.arm, a.verdict_cmd),
        scoring_metrics=metrics(),
        experiment_name=f"verdict-{a.arm}-{vendor}-{time.strftime('%Y%m%dT%H%M%S')}",
        experiment_config={"arm": a.arm, "vendor": vendor, "verdict_cmd": a.verdict_cmd,
                           "plugin_version": verdict_prep.plugin_version(), "prompt_sha": prompt_sha},
        task_threads=1,
    )
    print(f"[eval] experiment {result.experiment_name}: {len(result.test_results)} items")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
