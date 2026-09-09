---
name: finding
description: >-
  /gates:finding <text>. Note one harness finding — something the plugin got wrong or
  could do better — as one dated line in traces/harness-findings.md, stamped with the plugin
  version and the active ticket. The human invokes it; nothing else changes.
disable-model-invocation: false
argument-hint: "<text>"
---
# Finding
One Bash line, then print the line it wrote:
```
mkdir -p traces && v=$(python3 -c "import json;print(json.load(open('${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json'))['version'])") && t=$( [ -s kanban/.active ] && cat kanban/.active || echo - ) && l="- $(date '+%Y-%m-%d %H:%M') · plugin $v · ticket $t · <text>" && printf '%s\n' "$l" >> traces/harness-findings.md && echo "$l"
```
The line: `- <YYYY-MM-DD HH:MM> · plugin <version> · ticket <id or -> · <text>`. The version is
the plugin manifest's; the ticket is `kanban/.active` when present. Nothing else is read or changed.

Next: continue.
