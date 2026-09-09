---
name: init
description: >-
  /harness-plugin:init [--name <n>] | --update [--yes]. Create a project from the template the
  plugin ships: copies it into an empty git repo, pins the plugin to this version, runs make
  install and make ci, commits. --update diffs the template-owned files (Makefile, .gitignore,
  .env.example, CI workflow, docs/spec/.gitkeep, plugin pin) against the repo and writes them only with --yes. Use
  whenever the user says init, new project, or update the template. The session runs one
  script and edits nothing.
disable-model-invocation: true
argument-hint: "[--name <n>] | --update [--yes]"
---
# Init
One Bash line, the arguments passed through as given:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_project.py init <args>
```
Print its output as printed and end the turn on the script's last line. Refusals (a repo with
tracked files, no git identity, a name that is not an identifier, a failed `make ci`) are one
line on stderr: print it and stop. `--update` without `--yes` prints the diff and writes
nothing; its last line is the script's own Next line.

Next: write kanban/briefs/1-<slug>.md, then /harness-plugin:grill 1
