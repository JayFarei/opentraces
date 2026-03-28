# Export

`opentraces export` converts staged traces to other formats for interoperability with existing training pipelines and tools.

## ATIF Export

```bash
opentraces export --format atif
```

Exports to ATIF (Agent Trajectory Interchange Format) v1.6. This strips opentraces-specific fields (attribution, security metadata) and outputs ATIF-compatible records.

## Options

```bash
# Export to a file
opentraces export --format atif --output /path/to/output.jsonl

# Export specific traces
opentraces export --format atif --trace-id abc123 --trace-id def456

# Preview without writing
opentraces export --format atif --dry-run
```

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | required | Target format (currently: `atif`) |
| `--output` | stdout | Output file path |
| `--trace-id` | all | Specific trace IDs to export |
| `--dry-run` | off | Preview without writing |

## Import

Import traces from other formats into opentraces staging:

```bash
opentraces import --from dataclaw /path/to/traces
```

Currently supports importing from DataClaw format. Imported traces go through the same security scanning and enrichment pipeline as parsed traces.

| Flag | Default | Description |
|------|---------|-------------|
| `--from` | required | Source format (`dataclaw`) |
| `--max-records` | unlimited | Maximum records to import |

## Field Mapping

See [Standards Alignment](/docs/schema/standards) for how opentraces fields map to ATIF, ADP, and OTel conventions.
