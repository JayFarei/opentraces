---
name: skill-rollouts-v1
description: Store synthetic otbox-style rollouts for a candidate skill version.
mode: agent-skill
---

# skill-rollouts-v1

Builds deterministic synthetic rollout rows for `skill-eval-tasks-v1` tasks. The
default builder uses the SkillOpt fake re-rollout runner and writes transcript
references alongside the output JSONL.
