#!/usr/bin/env python3
"""A stand-in `claude` for the runner tests. Never calls a model.

Build call (argv has --output-format stream-json): plays the scenario in $FAKE_CLAUDE_SCENARIO
(JSON list; {"cmd": "..."} runs a shell command in cwd and emits the tool_use/tool_result
events, {"text": "..."} emits assistant text), then prints the result envelope from
$FAKE_CLAUDE_ENVELOPE (a JSON file) or a default success envelope.
Verdict call (packet on stdin, --output-format json): prints $FAKE_VERDICT_ENVELOPE, or a
default ship envelope. Every call appends its argv to $FAKE_CLAUDE_LOG.
"""
import json
import os
import subprocess
import sys


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    argv = sys.argv[1:]
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(" ".join(argv) + "\n")
    if "stream-json" not in argv:
        sys.stdin.read()
        env = os.environ.get("FAKE_VERDICT_ENVELOPE")
        if env and os.path.exists(env):
            sys.stdout.write(open(env).read())
        else:
            verdict = {"ticket": "1.1", "decision": "ship", "held": ["AC-1", "AC-2", "charter-1", "charter-2"], "findings": [], "ci": {"green": True}}
            emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "total_cost_usd": 0.03,
                  "usage": {"input_tokens": 100, "output_tokens": 50}, "permission_denials": [], "result": json.dumps(verdict)})
        return 0
    emit({"type": "system", "subtype": "init"})
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO")
    steps = json.load(open(scenario)) if scenario and os.path.exists(scenario) else []
    for i, step in enumerate(steps):
        if "text" in step:
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": step["text"]}]}})
            continue
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": step["cmd"]}}]}})
        r = subprocess.run(step["cmd"], shell=True, capture_output=True, text=True)
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": (r.stdout + r.stderr)[-200:]}]}})
    env = os.environ.get("FAKE_CLAUDE_ENVELOPE")
    if env and os.path.exists(env):
        sys.stdout.write(open(env).read())
    else:
        emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": len(steps), "total_cost_usd": 0.5,
              "permission_denials": [], "result": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
