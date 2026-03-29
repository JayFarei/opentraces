---
schema_version: "1.0"
title: Two-Pass Context-Aware Security Scanning
scope: security
pattern_type: behavioral
transferable: true
---

# Two-Pass Context-Aware Security Scanning

## Overview

Security scanning uses a two-pass architecture with context-aware rules per field type. Pass 1 scans each field individually with rules tuned to that field's content type (tool inputs get entropy scanning, tool results do not). Pass 2 scans the final serialized output as raw text to catch anything introduced during enrichment or serialization that was not present in the original fields.

## How It Works

**Pass 1 (per-field, context-aware)**:
- Each text field in the record is classified by `FieldType`: TOOL_INPUT, TOOL_RESULT, REASONING, GENERAL
- Tool inputs and general text get both regex and Shannon entropy scanning
- Tool results get regex only (command output triggers too many entropy false positives)
- Reasoning content gets regex only (LLM hallucinations trigger entropy false positives)
- Unknown tools default to TOOL_INPUT (conservative)

**Pass 2 (serialized bytes)**:
- The complete JSONL line is scanned as a single raw string
- Catches secrets that enrichment modules may have introduced (e.g., git commit messages with tokens)
- Catches secrets that survive field-level scanning due to JSON escaping or concatenation

**Allowlist system**:
- Known false positives (example emails, private IP ranges, Python decorators, dummy API keys) are pre-filtered
- Credit card matches validated with Luhn algorithm
- SSN matches filtered by reserved area/group numbers

## Key Files

- `src/opentraces/security/scanner.py` - `two_pass_scan()`, `scan_content()`, field type classification
- `src/opentraces/security/secrets.py` - 19+ regex patterns, Shannon entropy, allowlists
- `src/opentraces/security/classifier.py` - Heuristic classifier (tier 2+)
- `src/opentraces/security/anonymizer.py` - Username hashing, path stripping
- `src/opentraces/security/redactor.py` - Logging filter to prevent log leaks

## How to Replicate

1. Define field types as an enum representing the different content contexts in your data
2. Build a pattern library with severity levels (critical/high/medium)
3. Create context-aware scan configurations: which patterns and detectors apply to which field types
4. Implement Pass 1: iterate fields, classify each, apply appropriate scan config
5. Implement Pass 2: serialize the final output and scan the raw bytes
6. Build an allowlist system to reduce false positives for known-safe patterns
7. Apply redactions from end-to-start (descending position) to preserve byte offsets
8. Add a logging filter to prevent secrets from leaking into application logs

## When to Use

- Processing user-generated content that may contain credentials or PII
- Data pipelines where enrichment stages may introduce new sensitive content after initial scanning
- Systems where different content types have different false positive profiles
- When the cost of a missed secret (credential leak) is much higher than the cost of a false positive (over-redaction)

## When NOT to Use

- When all content is uniform in type and a single scan pass suffices
- When the data is already known to be sanitized
- When false positive rates are more important than recall (e.g., user-facing content filtering)
