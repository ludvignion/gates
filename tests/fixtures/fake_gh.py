#!/usr/bin/env python3
"""A stand-in `gh` for the tickets.py tests, as fake_claude.py is for the runner tests.

Answers the subset of `gh` scripts/tickets.py calls: `label create`, `issue list --label ticket
--state <s> --json number`, `issue view <n> --json ...`, `issue comment`, `issue edit --add-label/
--remove-label`, `issue close`, `issue create`, `api graphql` (closed-issue edit history) and
`api repos/<repo>/issues/<n>/timeline` (reopen events, paginated by `per_page`/`page` fields,
default page size 30 as real GitHub). Issue data comes from $FAKE_GH_DATA (a
JSON file: {"issues": [...], "fail_view": [<number>, ...], "fail_list": true}); a number in
`fail_view` makes that issue's `issue view` call fail, `fail_list` makes every `issue list` call
fail, as `gh` does on a network or auth error. An issue may carry
`"closedAt"`, `"body_edits"` (a list of ISO timestamps, becoming `userContentEdits` nodes) and
`"timeline"` (a list of `{"event": ...}` dicts) for the GitHub-history rule's tests. A write
(comment, edit, close, create) rewrites $FAKE_GH_DATA so a later call in the same test sees it,
as a real repo would. Every call is appended to $FAKE_GH_LOG, one argv per line.
"""
import json
import os
import sys


def _label_names(issue: dict) -> list[str]:
    return [l if isinstance(l, str) else l.get("name") for l in issue.get("labels", [])]


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _fields(argv: list[str]) -> dict[str, str]:
    fields = {}
    for i, a in enumerate(argv):
        if a in ("-f", "-F") and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            fields[k] = v
    return fields


def _save(data_path: str, data: dict) -> None:
    with open(data_path, "w") as f:
        json.dump(data, f)


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(argv) + "\n")
    data_path = os.environ.get("FAKE_GH_DATA")
    data = json.load(open(data_path)) if data_path else {"issues": []}
    issues = data.setdefault("issues", [])
    if argv[:2] == ["label", "create"]:
        return 0
    if argv[:2] == ["issue", "list"]:
        if data.get("fail_list"):
            print("gh: issue list failed (simulated)", file=sys.stderr)
            return 1
        label = _flag(argv, "--label")
        state = _flag(argv, "--state") or "open"
        out = [{"number": i["number"]} for i in issues if label in _label_names(i)
               and (state == "all" or i.get("state", "OPEN").lower() == state.lower())]
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
        print(json.dumps({"state": "OPEN", **issue}))
        return 0
    if argv[:2] == ["issue", "comment"]:
        number = int(argv[2])
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        issue.setdefault("comments", []).append({"body": _flag(argv, "--body")})
        _save(data_path, data)
        return 0
    if argv[:2] == ["issue", "edit"]:
        number = int(argv[2])
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        labels = _label_names(issue)
        if (add := _flag(argv, "--add-label")) and add not in labels:
            labels.append(add)
        if (rm := _flag(argv, "--remove-label")) and rm in labels:
            labels.remove(rm)
        issue["labels"] = labels
        _save(data_path, data)
        return 0
    if argv[:2] == ["issue", "close"]:
        number = int(argv[2])
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        issue["state"] = "CLOSED"
        _save(data_path, data)
        return 0
    if argv[:2] == ["api", "graphql"]:
        fields = _fields(argv)
        number = int(fields.get("number", 0))
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        edits = [{"editedAt": t} for t in issue.get("body_edits", [])]
        out = {"data": {"repository": {"issue": {
            "closedAt": issue.get("closedAt"), "userContentEdits": {"nodes": edits}}}}}
        print(json.dumps(out))
        return 0
    if argv[:1] == ["api"] and len(argv) > 1 and argv[1].startswith("repos/") and argv[1].endswith("/timeline"):
        number = int(argv[1].split("/")[-2])
        issue = next((i for i in issues if i["number"] == number), None)
        if issue is None:
            print(f"no issue {number}", file=sys.stderr)
            return 1
        fields = _fields(argv)
        per_page = int(fields.get("per_page", 30))
        page = int(fields.get("page", 1))
        timeline = issue.get("timeline", [])
        start = (page - 1) * per_page
        print(json.dumps(timeline[start:start + per_page]))
        return 0
    if argv[:2] == ["issue", "create"]:
        number = max((i["number"] for i in issues), default=0) + 1
        labels = [n for i, n in enumerate(argv) if argv[i - 1] == "--label"]
        issues.append({"number": number, "title": _flag(argv, "--title"), "body": _flag(argv, "--body"),
                       "labels": labels, "comments": [], "state": "OPEN"})
        _save(data_path, data)
        print(f"https://github.com/{_flag(argv, '-R')}/issues/{number}")
        return 0
    print(f"fake_gh: unhandled {argv}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
