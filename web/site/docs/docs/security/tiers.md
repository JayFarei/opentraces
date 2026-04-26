# Security Tiers

opentraces applies layered security scanning before traces are staged or pushed. The current pipeline version is `SECURITY_VERSION = 0.3.0`.

Tip: run `opentraces doctor --security` to see the exact tiers, versions, and commands active in your current install.

## Current User-Facing Tiers

The current 0.4 CLI surfaces these layers:

| Tier | Name | Status | What it does |
|------|------|--------|--------------|
| 1a | Regex patterns | always on | Built-in secret detectors for known token and key formats |
| 1b | Shannon entropy | always on | Flags high-entropy strings that look like secrets |
| 1.5 | TruffleHog | optional | Runs TruffleHog locally for broader secret detection |
| 2 | LLM trace review | optional, on demand | Semantic review over the whole trace transcript |
| 3 | Human review | always available | Web inbox, TUI, and CLI review before upload |

## Tier 1a And 1b

Regex and entropy scanning are always on. They run locally during processing and rewrite sensitive content before traces surface in the inbox.

## Tier 1.5: TruffleHog

Enable Tier 1.5 with:

```bash
opentraces setup trufflehog
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
```

Current behavior:

- TruffleHog is opt-in
- it runs locally with `verify_secrets = false`
- findings are redacted in place
- findings force human review before upload
- `opentraces push --no-trufflehog` skips it for one push only

Use `opentraces doctor --security` to confirm whether the binary is installed and enabled.

## Tier 2: LLM Trace Review

Tier 2 sends each staged trace to a third-party LLM for a semantic review on top of the regex, entropy, and TruffleHog tiers. It is opt-in, runs out-of-band, and is stored in the global config under `security.llm_review` (one config per machine, projects inherit it).

### Configure The Reviewer

Run the interactive wizard once:

```bash
opentraces setup llm-review
```

The picker offers these presets out of the box:

| Preset | API format | Default endpoint | API key env |
|--------|------------|------------------|-------------|
| `ollama` | openai-compat | `http://localhost:11434/v1` | (none, local) |
| `lm-studio` | openai-compat | `http://localhost:1234/v1` | (none, local) |
| `llama-cpp` | openai-compat | `http://localhost:8080/v1` | (none, local) |
| `vllm` | openai-compat | `http://localhost:8000/v1` | (none, local) |
| `openai` | openai-compat | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `groq` | openai-compat | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `openrouter` | openai-compat | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `together` | openai-compat | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| `anthropic-direct` | anthropic | (native SDK) | `ANTHROPIC_API_KEY` |
| `custom` | any | your URL | your env var |

Local Ollama models can be pulled inline from the wizard when the `ollama` binary is on PATH.

### Non-Interactive Setup

Skip the picker by passing flags directly, useful in agent setups and CI:

```bash
opentraces setup llm-review \
  --api-format openai-compat \
  --base-url http://localhost:11434/v1 \
  --model gemma3n:e4b

opentraces setup llm-review \
  --api-format openai-compat \
  --base-url https://api.groq.com/openai/v1 \
  --model llama-3.3-70b-versatile \
  --api-key-env GROQ_API_KEY

opentraces setup llm-review \
  --api-format anthropic \
  --model claude-haiku-4-5-20251001 \
  --api-key-env ANTHROPIC_API_KEY
```

Full flag set:

| Flag | Purpose |
|------|---------|
| `--api-format {openai-compat,ollama,anthropic,fake}` | Wire protocol the client speaks |
| `--base-url <url>` | Base URL (include `/v1` for openai-compat servers; ignored for `anthropic`) |
| `--model <name>` | Model name or tag |
| `--api-key-env <VAR>` | Env var holding the API key; empty for local servers |
| `--timeout <seconds>` | Request timeout, defaults to `120` |
| `--enable` | Turn llm-review on using current config |
| `--disable` | Turn llm-review off without changing other fields |
| `--test` | Ping the endpoint without writing config, exits non-zero on failure |
| `--print` | Print the effective config as JSON and exit |
| `--no-interactive` | Skip the preset picker when no flags are given |
| `--project` | Scope this change to the project marker instead of global config |

### Run Tier 2 On Demand

```bash
opentraces llm-review                          # every staged trace
opentraces llm-review --scope staged           # second line of defence before push
opentraces llm-review --scope inbox            # pre-add only
opentraces llm-review --trace 8a3f1c           # one trace (short id ok; repeatable)
opentraces llm-review --limit 5                # cap the batch
opentraces llm-review --force                  # re-review cached verdicts
opentraces llm-review --dry-run                # estimate sessions, chars, tokens, cost
opentraces llm-review --context-file AGENTS.md # project context (README/AGENTS.md up to 10KB)
```

Per-run overrides match the setup flags so you can try a different backend without touching config:

```bash
opentraces llm-review \
  --api-format openai-compat \
  --base-url https://api.groq.com/openai/v1 \
  --api-key-env GROQ_API_KEY \
  --model llama-3.3-70b-versatile
```

Verdicts are cached into `metadata.llm_review` on each trace so downstream gates and the TUI can see them. A bad verdict also blocks the trace in state so later push flows skip it.

### Gate The Push

```bash
opentraces push --llm-review
```

This fails upload unless every staged trace has a clean Tier 2 verdict. Typical flow before a public dataset push: `opentraces llm-review --scope staged` followed by `opentraces push --llm-review`.

### Doctor Output

`opentraces doctor` (and the focused `opentraces doctor --security` subview) surfaces everything configured for Tier 2:

- state: `disabled`, `on-demand`, or `unreachable`
- backend and model, inferred from the endpoint (for example `ollama / gemma3n:e4b`, `groq / llama-3.3-70b-versatile`)
- `endpoint` URL and `api` format
- `api key env` var name and whether it is currently `set` or `unset`
- probe status, including model count at the endpoint and whether your configured model is in the list; flagged as `not found` when the endpoint answers but does not expose the model, `not installed` when the binary is missing, or `not set` when a required API key env var is empty
- toggle hints for `run`, `gate push`, `reconfigure`, and `disable`

Use `doctor` to confirm the tier is healthy before relying on `push --llm-review` as an upload gate.

## Tier 3: Human Review

Human review is always available through:

```bash
opentraces web
opentraces tui
opentraces list --stage inbox
opentraces show <trace-id>
opentraces redact <trace-id>
```

This is the final check for project-specific context, sensitive business details, and traces that are technically safe but not worth publishing.

## Review Policy

Each repo carries a review policy in `.opentraces.json`:

```bash
opentraces setup review-policy --review
opentraces setup review-policy --auto
```

| Policy | Effect |
|--------|--------|
| `review` | Every trace lands in Inbox for manual review |
| `auto` | Safe traces are auto-approved into `staged` |

`auto` does not push automatically. Upload remains explicit.

## What Can Still Block

The user-facing pipeline is designed to redact and route most issues into review, but some failures still stop upload:

- parse errors
- missing required integrations you explicitly enabled
- `push --llm-review` when staged traces lack a clean Tier 2 verdict

Use `opentraces doctor` for pipeline failures and `opentraces list --stage blocked` for traces that still need intervention.
