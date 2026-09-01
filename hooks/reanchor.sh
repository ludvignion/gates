#!/usr/bin/env bash
# SessionStart(matcher: compact) — re-anchor the agent after context compaction.
# stdout is injected into the model's context. Reads kanban/.active, written by /build step 4.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}"

echo "## Context was compacted. Re-anchor before touching any file."
echo "Re-read AGENTS.md (invariants)."

if [[ -s kanban/.active ]]; then
  tid=$(<kanban/.active)
  ticket=$(ls kanban/tickets/"$tid".*.md 2>/dev/null | head -n 1)
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
  echo "Active ticket: $tid (${ticket:-file not found}). Skill: /build. Branch: $branch."
  echo "Re-read the ticket and its ## Log, then run \`git status\` and \`git log --oneline -5\`."
  echo "Resume the build sequence from the last logged step. Do not commit until you have."
else
  echo "No active ticket. If you were in /verdict, re-read the ticket named in your instruction"
  echo "and traces/verdict/ before writing anything."
fi