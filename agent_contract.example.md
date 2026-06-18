---
objective: Implement a tiny Python feature and verify it locally
checks:
  - pytest
constraints:
  - Never run sudo
  - Never run rm -rf
  - Never modify .env
  - Never push to a remote
max_iterations: 3
task_name: initial-mvp
allowed_installs: []
allow_overwrite: false
cost_limit: 1.0
failure_limit: 3
command_timeout_sec: 120
estimated_cost_per_iteration: 0.02
# dry_run: true
---

# Agent Contract

Copy this file to `agent_contract.md` inside the target repository and adjust the values.
