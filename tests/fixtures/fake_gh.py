#!/usr/bin/env python3
"""A stand-in `gh` for the tickets.py tests, as fake_claude.py is for the runner tests.

Answers the subset of `gh` scripts/tickets.py calls: `label create`, `issue list --label ticket
--json number`, `issue view <n> --json number,title,body,labels,comments`. Issue data comes from
$FAKE_GH_DATA (a JSON file: {"issues": [...], "fail_view": [<number>, ...]}); a number in
`fail_view` makes that issue's `issue view` call fail, as `gh` does on a network or auth error.
Every call is appended to $FAKE_GH_LOG, one argv per line.
"""
import json
import os
import sys


def _label_names(issue: dict) -> list[str]:
    return [l if isinstance(l, str) else l.get("name") for l in issue.get("labels", [])]


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(argv) + "\n")
    data = json.load(open(os.environ["FAKE_GH_DATA"])) if os.environ.get("FAKE_GH_DATA") else {"issues": []}
    issues = data.get("issues", [])
    if argv[:2] == ["label", "create"]:
        return 0
    if argv[:2] == ["issue", "list"]:
        label = argv[argv.index("--label") + 1] if "--label" in argv else None
        out = [{"number": i["number"]} for i in issues if label in _label_names(i)]
        print(json.dumps(out))
        return 0
    if argv[:2] == ["issue", "view"]:
        number = int(argv[2])
        if number in data.get("fail_view", []):
            print(f"gh: issue view {number} failed (simulated)", file=sys.stderr)
            return 1
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        print(json.dumps(issue))
        return 0
    print(f"fake_gh: unhandled {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
