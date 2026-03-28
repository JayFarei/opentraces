# Security Tiers

The plugin offers three modes for controlling what gets uploaded. These are configured per-project or per-session, not globally, because the sensitivity of a personal side-project differs enormously from a client codebase.

## Tier 1: Open Mode (Minimal Gate)

Traces are uploaded with minimal friction, but not blindly. Before any upload a lightweight baseline security check runs: regex-based scanning for high-confidence secrets (API keys, tokens, passwords) and obvious PII (emails, IP addresses). Anything that trips the baseline is auto-redacted, not escalated.

**For:**

- Open-source projects where the codebase is already public
- Benchmark runs (SWE-bench, Aider-bench) where there's nothing to protect
- Researchers who want maximum throughput and accept the risk

The "open" label signals that this tier is for projects where the codebase is already public.

```
Trace captured
    |
    v
+----------------------+
| Baseline secret/PII  |  regex scan only
| scan + auto-redact   |  no human review
+----------+-----------+
           |
           v
+----------------------+
| Upload to HF Hub     |  continuous, per-turn
+----------------------+
```

**Security principle:** Even the lowest-friction tier guarantees no raw secrets or credentials leak into the public dataset.

## Tier 2: Guarded Screening + Escalation (Default)

Traces pass through a classifier/extraction pipeline before upload:

1. **PII detection** - Scans for emails, API keys, tokens, credentials, internal hostnames, IP addresses, filesystem paths that reveal org structure. Regex patterns + lightweight classifier.
2. **Sensitive content classification** - Flags traces that reference proprietary codebases, internal tool names, customer data. LLM or embedding-based classifier.
3. **De-anonymization risk scoring** - Estimates how identifiable the contributor is from the trace content. Stylometric and contextual signals beyond explicit PII.
4. **Escalation** - Anything flagged gets surfaced in an interactive review before upload.

```
Trace captured
    |
    v
+----------------------+
| Baseline secret/PII  |  same regex layer as Tier 1
| scan + auto-redact   |
+----------+-----------+
           |
           v
+----------------------+
| Classifier pipeline  |  LLM/embedding-based
|  - sensitive content |
|  - de-anon risk      |
+----------+-----------+
           |
      +----+----+
      |         |
   clean     flagged
      |         |
      v         v
+----------+ +--------------+
| Upload   | | Interactive  |
| to HF    | | review       |
+----------+ | (approve /   |
             |  redact /    |
             |  reject)     |
             +------+-------+
                    |
                    v
              Upload or discard
```

**Security principle:** Machine classifiers handle the bulk. Humans only see edge cases the classifier is uncertain about.

## Tier 3: Strict Post-Session Review (Human-in-the-Loop)

Nothing is uploaded during the session. All traces are buffered locally. After the session ends, the user manually reviews every trace before anything leaves their machine.

```
Trace captured
    |
    v
+----------------------+
| Buffer locally       |  nothing leaves the machine
| (session JSONL)      |
+----------+-----------+
           |
     session ends
           |
           v
+----------------------+
| Human review         |  CLI: `opentraces review`
|  - per-trace approve |  or local web UI
|  - redact turns      |
|  - annotate quality  |
|  - reject / skip     |
+----------+-----------+
           |
    user confirms push
           |
           v
+----------------------+
| Upload to HF Hub     |  explicit, deliberate action
+----------------------+
```

**Security principle:** Full human-in-the-loop. Nothing is uploaded without the contributor seeing and approving it.

## Vendored Security Patterns

The baseline security layer (used across all tiers) vendors DataClaw's battle-tested modules (MIT licensed):

- **secrets.py** (~273 lines): 19 regex patterns + Shannon entropy analysis + allowlist for false positives. Covers JWT, API keys by provider prefix, DB URLs, private keys, Bearer tokens, IPs, emails, high-entropy strings. Extended with: credit card numbers (Luhn), SSNs, phone numbers.
- **anonymizer.py** (~105 lines): SHA-256 username hashing, `/Users/`/`/home/` path stripping, macOS hyphen-encoded path handling.
