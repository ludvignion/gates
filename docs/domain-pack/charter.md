# Charter

Non-negotiables for this domain; the verdict judges each item only where the diff touches a path its `Applies to:` globs reach, and a waiver needs written rationale in the ticket.

## 1. Refuse in one line, never work around
Applies to: scripts/*, hooks/*, skills/*
Pattern: a script that cannot proceed exits non-zero with one line saying why, and changes nothing.
Anti-pattern: a script that guesses, retries silently, or edits state to get past a check.
Demonstrated by: a test that runs the script against the bad input and asserts the exit code, the single line, and an unchanged tree.
