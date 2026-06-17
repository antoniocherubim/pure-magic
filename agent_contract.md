---
objective: Rebuild the initial local autonomous coding loop foundation
checks:
  - python -m pytest
constraints:
  - Never run sudo
  - Never run rm -rf
  - Never modify .env
  - Never push to a remote
max_iterations: 3
task_name: rebuild-initial-foundation
allowed_installs: []
allow_overwrite: false
cost_limit: 1.0
failure_limit: 3
command_timeout_sec: 120
estimated_cost_per_iteration: 0.02
---

# Agent Contract

Initial local contract used to validate the orchestrator itself.
