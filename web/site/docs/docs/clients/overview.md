# Clients & Use Cases

Clients consume either published dataset rows or private bucket evidence. The
important distinction is whether the client needs curated rows or the retained
trace environment.

| Client / use case | Primary input | Start here |
|-------------------|---------------|------------|
| Dataset consumers | Published dataset rows | [Dataset Consumers](/docs/clients/dataset-consumers) |
| Private bucket clients | Remote bucket evidence | [Private Bucket Clients](/docs/clients/private-bucket) |
| Agent workflows | Trace discovery + context + Trail evidence | [Agent Workflows](/docs/clients/agent-workflows) |
| Trace capsule | Privacy-bounded agent usage episode ("Agent Experience Report") | [Trace Capsule](/docs/clients/trace-capsule) |

Published rows are for evaluation jobs, training loops, analytics, dashboards,
and sharing. Bucket evidence is for agents or teammates that need to inspect a
trace environment: raw trace records, Trail anchors, Context Tree nodes, or
source blobs.

The same captured trace can feed multiple clients. One workflow can publish
small eval rows, another can warm an agent with relevant prior context, and a
third can package a capsule — a privacy-bounded usage episode — for sharing or
filing as a GitHub issue.
