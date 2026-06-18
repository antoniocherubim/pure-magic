---
objective: Run a local smoke test of the agent harness
checks:
  - python -m pytest
constraints:
  - Never run sudo
  - Never run rm -rf
  - Never modify .env
  - Never push to a remote
max_iterations: 3
task_name: smoke-run
allowed_installs: []
allow_overwrite: false
cost_limit: 1.0
failure_limit: 3
command_timeout_sec: 120
estimated_cost_per_iteration: 0.02
dry_run: true
---

# Agent Contract

Copy this file to `agent_contract.md` in the target repository and adjust the values.

## Field guide

- **objective** — what the agent loop should accomplish in plain language.
- **checks** — commands the Executor may propose; strings must match **exactly** (e.g. `python -m pytest`, not `pytest` alone).
- **constraints** — safety rules repeated to Planner, Executor, and Reviewer.
- **max_iterations** — upper bound on Planner → Executor → Reviewer cycles.
- **task_name** — short slug; the harness creates branch `agent/<task_name>`.
- **allow_overwrite** — when `false`, `write_file` cannot replace existing files.
- **cost_limit** / **failure_limit** — operational guardrails for long or failing runs.
- **command_timeout_sec** — subprocess timeout for each check command.
- **dry_run** — when `true`, simulates git, file writes, and commands without mutating the repo (safe default for first run).
