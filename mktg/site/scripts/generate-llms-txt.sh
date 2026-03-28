#!/usr/bin/env bash
# Generates public/llms.txt from src/content/ markdown files
# Run automatically via pre-commit hook when docs change

set -e
SITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONTENT_DIR="$SITE_DIR/src/content"
OUT="$SITE_DIR/public/llms.txt"

cat > "$OUT" << 'HEADER'
# open traces

> Open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Three security tiers. Training-first schema.

## Links

- Documentation: https://opentraces.ai/docs
- GitHub: https://github.com/opentraces
- Dashboard: https://opentraces.ai/dashboard
- Schema: https://opentraces.ai/schema

## Full Documentation
HEADER

# Ordered list of doc files matching DOC_NAV
for f in \
  "$CONTENT_DIR/index.md" \
  "$CONTENT_DIR/getting-started.md" \
  "$CONTENT_DIR/schema/index.md" \
  "$CONTENT_DIR/schema/trace-record.md" \
  "$CONTENT_DIR/schema/steps.md" \
  "$CONTENT_DIR/schema/outcome-attribution.md" \
  "$CONTENT_DIR/security-tiers.md" \
  "$CONTENT_DIR/architecture.md" \
  "$CONTENT_DIR/cli-reference.md" \
  "$CONTENT_DIR/standards.md"; do
  if [ -f "$f" ]; then
    echo "" >> "$OUT"
    echo "---" >> "$OUT"
    echo "" >> "$OUT"
    cat "$f" >> "$OUT"
  fi
done

echo "Generated $OUT ($(wc -l < "$OUT") lines)"
