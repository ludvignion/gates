#!/usr/bin/env python3
"""Validate trace files against node type rules. Usage: python lint_trace.py <n>"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _fm  # noqa: E402


def lint_trace(root: Path, n: str) -> list[str]:
    """Return list of violations. Empty list means valid."""
    violations = []

    # Read trace file
    trace_path = root / "traces" / "grill" / f"{n}.jsonl"
    if not trace_path.exists():
        violations.append(f"Trace file not found: {trace_path}")
        return violations

    # Read plan file for blocking risks
    plan_path = next((root / "kanban").rglob(f"{n}.plan.md"), None)
    blocking_risks = []
    if plan_path and plan_path.exists():
        fm, body = _fm.read(plan_path)
        # Extract blocking risks section
        match = re.search(r"## Blocking risks\n(.*?)(?=\n##|$)", body, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # Parse list items
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("- ") and line != "- <capability nodes without evidence; empty means none>":
                    blocking_risks.append(line[2:])

    # Check each trace record
    with open(trace_path) as f:
        for i, line in enumerate(f, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                violations.append(f"Line {i}: Invalid JSON: {e}")
                continue

            node_type = record.get("type")
            resolved_by = record.get("resolved_by")
            evidence = record.get("evidence")
            node = record.get("node", "")

            # Rule: capability with resolved_by: human
            if node_type == "capability" and resolved_by == "human":
                violations.append(
                    f"Line {i}: capability node '{node}' cannot be resolved by human"
                )

            # Rule: capability with empty evidence
            if node_type == "capability" and not evidence:
                violations.append(
                    f"Line {i}: capability node '{node}' has no evidence"
                )

            # Rule: fact with resolved_by: human
            if node_type == "fact" and resolved_by == "human":
                violations.append(
                    f"Line {i}: fact node '{node}' cannot be resolved by human"
                )

            # Rule: decision with resolved_by: evidence
            if node_type == "decision" and resolved_by == "evidence":
                violations.append(
                    f"Line {i}: decision node '{node}' cannot be resolved by evidence"
                )

            # Rule: resolved_by: open must be in blocking risks
            if resolved_by == "open":
                # Check if this node is mentioned in blocking risks
                node_in_risks = any(node.lower() in risk.lower() for risk in blocking_risks)
                if not node_in_risks:
                    violations.append(
                        f"Line {i}: open node '{node}' not listed in Blocking risks"
                    )

    return violations


def main(root: Path, n: str) -> None:
    violations = lint_trace(root, n)
    if violations:
        print("\n".join(violations))
        sys.exit(1)
    else:
        print(f"✓ Trace {n} is valid")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python lint_trace.py <n>", file=sys.stderr)
        sys.exit(1)
    main(Path("."), sys.argv[1])
