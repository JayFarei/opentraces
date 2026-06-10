# Schema Changes

The opentraces schema is open source. Feedback, questions, and proposals are welcome via [GitHub Issues](https://github.com/JayFarei/opentraces/issues).

## How to Propose a Change

When suggesting a schema change, include:

1. **What** field or model you would add, change, or remove
2. **Why** it matters for your use case (training, analytics, attribution, etc.)
3. **How** it relates to existing standards (ATIF, Agent Trace, ADP, OTel) if applicable

## What Counts as Breaking

| Change | Version Bump |
|--------|-------------|
| New optional field | Minor |
| New optional model | Minor |
| Field rename | Major |
| Field removal before 1.0 | Minor with rationale and a registered migration |
| Field removal after 1.0 | Major with a registered migration |
| Type change | Major |

See [Versioning](/docs/schema/versioning) for full policy.

## Adapter Contributions

To add support for a new live-capture agent, implement the `SessionParser` protocol in `src/opentraces/capture/_base.py` and register it in `src/opentraces/capture/__init__.py`.

For dataset or file imports, implement `FormatImporter` instead.

## Review Process

- Schema changes are reviewed by the maintainers
- Breaking changes require a new rationale document
- All changes are documented in the [CHANGELOG](https://github.com/JayFarei/opentraces/blob/main/packages/opentraces-schema/CHANGELOG.md)
