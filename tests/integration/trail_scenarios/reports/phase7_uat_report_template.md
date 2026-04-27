# Trace Trails Phase 7 UAT Report Template

Scenario:
Scenario file:
Run date:
Operator:
Command:
Evidence bundle:

## Setup

- `otd --version`:
- Hook interpreter:
- Scratch repository:
- Commit SHA:
- Trace id:
- Step id:
- Trace Patch id:
- Git Anchor id:
- Containing segment id:
- File path:
- File-line origin:

## Human Review

Paste the human-facing command output that a reviewer would read:

- `otd trail explain --commit HEAD`
- `otd blame HEAD`
- `otd graph --limit 1 --no-color`
- `otd trail search --commit HEAD`

## Machine Review

Record the JSON evidence paths or bundle keys:

- `raw_json.phase7:commit:selected`
- `raw_json.phase7:resolve:selected`
- `raw_json.phase7:blame:selected`
- `raw_json.phase7:blame-line:selected`
- `raw_json.phase7:graph:selected`
- `raw_json.phase7:search:selected`

## Cross-Command Identity Check

All checked commands must agree on:

- Trace id:
- Step id:
- Trace Patch id:
- Git Anchor id:
- Containing segment id:
- Commit SHA:
- File path:
- File-line origin:

## Limitations

- Watcher requirement:
- Capture limitations:
- Projection disagreements:
- Unknown/unanchored patches:
- Cleanup result:
