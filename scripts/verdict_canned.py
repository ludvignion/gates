#!/usr/bin/env python3
"""A verdict command that calls no model. Run: verdict_canned.py <verdict.json> <output>

Copies a prepared verdict JSON to the output path the runner or verdict_eval.py hands it, so
CI can exercise the verdict seat (packet, close-out, stamp, metrics) end to end with zero model
tokens. Use it as --verdict-cmd:
  python3 <plugin>/scripts/verdict_canned.py <plugin>/tests/fixtures/project/traces/verdict/1.1.json {output}
Paths must be absolute: the command runs in a scratch tree.
"""
import shutil
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    src, out = Path(argv[1]), Path(argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
