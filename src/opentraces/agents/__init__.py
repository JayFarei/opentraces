"""Agent-specific integrations (parsers, hooks, importers).

Each supported agent lives under its own subpackage and owns its wire-format
code. Cross-agent infrastructure (parser base protocol, quality gate, registry)
stays in :mod:`opentraces.parsers`.
"""
