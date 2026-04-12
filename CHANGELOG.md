# Changelog

## [0.3.0] - 2026-04-12

### Changed
- Major code reorganization: 15 top-level items collapsed to 7 top-level folders.
  - `agents/` + `parsers/` + `installers/` + `enrichment/git/post_commit.py` -> `capture/`
  - `exporters/` + `upload/` -> `publish/`
  - Top-level glue modules (config, paths, state, workflow, inbox, pipeline, processors) -> `core/`
  - Business logic extracted from `clients/` and `cli.py` into `core/review.py` + `core/publish_flow.py`
  - `cli.py` split into `cli/` package
- Deprecated import paths removed. See upgrade guide below.

### Removed (breaking imports)
- `opentraces.agents.*` -> use `opentraces.capture.*`
- `opentraces.parsers.*` -> use `opentraces.capture._base` or `opentraces.quality.parse_gate`
- `opentraces.installers.*` -> use `opentraces.capture.*`
- `opentraces.exporters.*` -> use `opentraces.publish.*`
- `opentraces.upload.*` -> use `opentraces.publish.huggingface.*`
- `opentraces.state`, `opentraces.config`, `opentraces.paths`, `opentraces.workflow`,
  `opentraces.inbox`, `opentraces.pipeline`, `opentraces.processors`
  -> use `opentraces.core.*`
- `opentraces.enrichment.git.post_commit` -> use `opentraces.capture.git.post_commit`
