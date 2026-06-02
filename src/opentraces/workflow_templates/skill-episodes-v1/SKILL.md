---
name: skill-episodes-v1
description: Project Trace Index skill_invocation units into reviewed skill-use episodes.
mode: agent-skill
---

# skill-episodes-v1

Builds `skill-episodes-v1` rows from Trace Index `skill_invocation` units. The
builder uses `opentraces.consumers.skill_intelligence` so the workflow remains a
projection over the core index rather than a separate skill ledger.
