#!/usr/bin/env bash
# Generate public/llms.txt from the MkDocs source tree.

set -e
SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOCS_DIR="$SITE_DIR/docs/docs"
OUT="$SITE_DIR/public/llms.txt"

cat > "$OUT" << 'HEADER'
# open traces

> Open-source CLI for repo-local agent trace capture, dataset authoring, and publication to Hugging Face Hub. Local bucket store with optional private-remote sync, VCS-anchored Trace Trails, executable dataset workflows, and a structured JSONL schema.

## Links

- Documentation: https://opentraces.ai/docs
- GitHub: https://github.com/jayfarei/opentraces
- Explorer: https://opentraces.ai/explorer
- Schema: https://opentraces.ai/schema

## Full Documentation
HEADER

# Ordered list aligned with MkDocs navigation.
for f in \
  "$DOCS_DIR/index.md" \
  "$DOCS_DIR/getting-started/installation.md" \
  "$DOCS_DIR/getting-started/authentication.md" \
  "$DOCS_DIR/getting-started/quickstart.md" \
  "$DOCS_DIR/cli/commands.md" \
  "$DOCS_DIR/cli/supported-agents.md" \
  "$DOCS_DIR/cli/troubleshooting.md" \
  "$DOCS_DIR/workflow/parsing.md" \
  "$DOCS_DIR/workflow/bucket.md" \
  "$DOCS_DIR/workflow/trace-discovery.md" \
  "$DOCS_DIR/workflow/blame.md" \
  "$DOCS_DIR/workflow/context-tree.md" \
  "$DOCS_DIR/workflow/workflow-templates.md" \
  "$DOCS_DIR/workflow/datasets.md" \
  "$DOCS_DIR/workflow/review.md" \
  "$DOCS_DIR/workflow/pushing.md" \
  "$DOCS_DIR/clients/overview.md" \
  "$DOCS_DIR/clients/dataset-consumers.md" \
  "$DOCS_DIR/clients/private-bucket.md" \
  "$DOCS_DIR/clients/agent-workflows.md" \
  "$DOCS_DIR/clients/trace-capsule.md" \
  "$DOCS_DIR/schema/overview.md" \
  "$DOCS_DIR/schema/trace-record.md" \
  "$DOCS_DIR/schema/steps.md" \
  "$DOCS_DIR/schema/outcome-attribution.md" \
  "$DOCS_DIR/schema/standards.md" \
  "$DOCS_DIR/schema/versioning.md" \
  "$DOCS_DIR/security/tiers.md" \
  "$DOCS_DIR/security/configuration.md" \
  "$DOCS_DIR/security/scanning.md" \
  "$DOCS_DIR/integration/agent-setup.md" \
  "$DOCS_DIR/integration/ci-cd.md" \
  "$DOCS_DIR/integration/post-processor-contract.md" \
  "$DOCS_DIR/integration/capture-integration.md" \
  "$DOCS_DIR/contributing/development.md" \
  "$DOCS_DIR/contributing/schema-changes.md"; do
  if [ -f "$f" ]; then
    echo "" >> "$OUT"
    echo "---" >> "$OUT"
    echo "" >> "$OUT"
    cat "$f" >> "$OUT"
  fi
done

python3 - "$OUT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
path.write_text(text.rstrip() + "\n")
PY

echo "Generated $OUT ($(wc -l < "$OUT") lines)"
