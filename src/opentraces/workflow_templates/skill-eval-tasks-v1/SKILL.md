---
name: skill-eval-tasks-v1
description: Convert reviewed skill episodes into leakage-safe eval task rows.
mode: agent-skill
---

# skill-eval-tasks-v1

Builds deterministic Dtrain/Dsel/Dtest eval task rows from reviewed
`skill-episodes-v1` rows. Split assignment is based on stable leakage keys and is
performed before any synthetic rollout augmentation.
