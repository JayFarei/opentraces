# DataClaw: R&D Scouting Brief

> Research date: 2026-03-27
> Source: https://github.com/peteromallet/dataclaw
> Category: library (CLI tool)

---

## Overview

DataClaw is a Python CLI tool (v0.3.2) by Peter O'Mallet that reads conversation history from AI coding agents (Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Kimi CLI), anonymizes/redacts PII and secrets, and publishes the result as a Hugging Face dataset. It is framed as a "performance art project" protesting Anthropic's distillation-prevention data policies, letting users "throw the ladder back down" by sharing their AI interaction traces openly.

## Problem It Solves

AI companies train models on freely shared data, then restrict others from doing the same. Meanwhile, the training/RL community is starved for real-world agentic trajectory data. DataClaw bridges this gap by providing a single-command pipeline from local agent session logs to public HuggingFace datasets, with privacy guardrails built in. The key insight: every coding agent already stores structured conversation logs locally, they just need a safe path to publication.

## How It Works

### Architecture

DataClaw is a **pure CLI tool** with no server, daemon, or background process. It is a **passive reader** of existing agent log files, not an instrumentation layer.

The architecture is a 5-module Python package (~4,100 lines of production code):

```
dataclaw/
  cli.py        (~1,638 lines) - Entry point, all commands, HF push logic, PII scanning
  parser.py     (~2,038 lines) - Multi-source session parsers (7 sources)
  anonymizer.py (~105 lines)   - Path/username PII removal via SHA-256 hashing
  secrets.py    (~273 lines)   - 19 regex patterns + entropy-based secret detection
  config.py     (~54 lines)    - Persistent JSON config at ~/.dataclaw/config.json
```

Source: `/tmp/scout-ApYerx/dataclaw/dataclaw/`

### Data Flow

```
AI agent log files (JSONL/JSON/SQLite on disk)
         |
         v
   parser.py: discover_projects()
         |   (scans ~/.claude, ~/.codex, ~/.gemini, ~/.local/share/opencode,
         |    ~/.openclaw, ~/.kimi, ~/.dataclaw/custom)
         v
   parser.py: parse_project_sessions()
         |   -> source-specific parser (7 parsers)
         |   -> anonymizer.path() on file paths
         |   -> anonymizer.text() on content
         |   -> Builds normalized session dict
         v
   cli.py: export_to_jsonl()
         |   -> redact_session()  [secrets.py: 19 regex patterns + entropy]
         |   -> redact_custom_strings()  [user-configured strings]
         |   -> Writes one JSON object per line to conversations.jsonl
         v
   cli.py: confirm()
         |   -> _scan_pii()  [second-pass regex + entropy scan]
         |   -> validates 3 text attestations from agent
         |   -> saves stage="confirmed" to config
         v
   cli.py: push_to_huggingface()
         |   -> HfApi().create_repo()
         |   -> upload conversations.jsonl + metadata.json + README.md
         v
   HuggingFace Dataset: {username}/my-personal-codex-data
   (tagged 'dataclaw', MIT license)
```

### Key Concepts

- **State Machine Pipeline**: 4 explicit stages (auth -> configure -> review -> confirmed/done) persisted to `~/.dataclaw/config.json`. Every command reads stage and returns `next_steps` + `next_command` in JSON. Push is hard-gated behind `confirm`. Source: `cli.py:189` (`_compute_stage`).

- **Agent-Native CLI Protocol**: Every command outputs structured JSON with `next_steps` array and `next_command` field, designed for AI agents to parse and chain commands without improvisation. A `---DATACLAW_JSON---` sentinel separates human text from machine-parseable JSON in `export` output. Source: `cli.py:1187` (`main`).

- **Multi-Source Passive Parsing**: 7 source-specific parsers read existing log files from disk. No instrumentation hooks, no runtime overhead, no agent modification required. Sources range from JSONL (Claude, Codex, OpenClaw, Kimi) to JSON (Gemini) to SQLite (OpenCode). Source: `parser.py:135` (`discover_projects`).

- **Two-Pass Redaction**: First pass during parsing (anonymizer + secrets.py with 19 regex patterns + Shannon entropy analysis). Second pass during `confirm` (`_scan_pii` at `cli.py:766`). Users can add custom redaction strings via `--redact`.

- **Attestation-as-Code**: The `confirm` command requires 3 free-text attestations from the calling agent, each semantically validated (checks for keywords like "ask", "scan", "manual", session count >= 20) before advancing state. Source: `cli.py:849-926`.

- **Gemini Hash Resolution**: Gemini CLI hashes project directory paths with SHA-256 for subdirectory names. DataClaw reverses this by hashing first-level `$HOME` subdirectories and matching, or by extracting file paths from tool call arguments within sessions. Source: `parser.py:57-119`.

### Core API / Interface

**CLI Commands:**

| Command | Purpose |
|---------|---------|
| `dataclaw prep [--source SOURCE]` | Discover projects, check HF auth, return full JSON context |
| `dataclaw config [--repo, --source, --exclude, --redact, --redact-usernames, --confirm-projects]` | View/set persistent config (list flags append, never replace) |
| `dataclaw list [--source SOURCE]` | List discovered projects with exclusion status |
| `dataclaw export [--output, --no-push, --no-thinking, --publish-attestation]` | Export to JSONL, optionally push to HF |
| `dataclaw confirm [--file, --full-name, --skip-full-name-scan, --attest-*]` | PII scan, attestation validation, unlock push |
| `dataclaw status` | Show current pipeline stage |
| `dataclaw update-skill claude` | Install Claude Code skill file |

Source filter choices: `auto|claude|codex|custom|gemini|kimi|opencode|openclaw|all`

**Python API (importable):**

| Module | Key exports |
|--------|------------|
| `dataclaw.parser` | `discover_projects() -> list[dict]`, `parse_project_sessions(project_dir_name, anonymizer, include_thinking, source) -> list[dict]` |
| `dataclaw.anonymizer` | `Anonymizer(extra_usernames)` with `.path()` and `.text()` methods |
| `dataclaw.secrets` | `scan_text(text) -> list[dict]`, `redact_text(text) -> (str, int)`, `redact_session(session, custom_strings) -> (dict, int)` |
| `dataclaw.config` | `load_config() -> DataClawConfig`, `save_config(config)` |

**Output Schema** (each line of `conversations.jsonl`):

```json
{
  "session_id": "uuid",
  "project": "source:project-name",
  "source": "claude|codex|gemini|opencode|openclaw|kimi|custom",
  "model": "model-id",
  "git_branch": "main",
  "start_time": "ISO-8601",
  "end_time": "ISO-8601",
  "messages": [
    {"role": "user", "content": "...", "timestamp": "..."},
    {
      "role": "assistant", "content": "...", "thinking": "...",
      "tool_uses": [{"tool": "bash", "input": {...}, "output": {...}, "status": "success"}],
      "timestamp": "..."
    }
  ],
  "stats": {
    "user_messages": 5, "assistant_messages": 8,
    "tool_uses": 20, "input_tokens": 50000, "output_tokens": 3000
  }
}
```

### Secret Detection Patterns

19 named regex patterns in `secrets.py` covering:
JWT (full + partial), DB connection strings, Anthropic/OpenAI/HF/GitHub/PyPI/NPM/AWS/Slack/Discord keys, private PEM keys, CLI `--token` flags, env-var assignments, Bearer headers, IP addresses, URL query params, emails, and high-entropy quoted strings (Shannon entropy >= 3.5 + mixed char types).

Allowlist suppresses false positives: noreply emails, example URLs, Python decorators, private IPs, public DNS resolvers.

## Maturity & Traction

- **License**: MIT
- **Stars**: 2,005 (as of 2026-03-27)
- **Forks**: 234
- **Open Issues**: 10
- **Latest Release**: v0.3.2 (2026-02-26)
- **Created**: 2026-02-24 (approximately 1 month old)
- **Backing**: Individual project by Peter O'Mallet; GitHub org is `banodoco`
- **Production Users**: ~32 HuggingFace datasets tagged `dataclaw`, of which ~10-12 are unique original contributions (rest are mirrors/copies of the creator's dataset)
- **Ecosystem Size**: Minimal, one bundled Claude Code skill file (`SKILL.md`), one fork variant (`dataclaw-mcp` for CSV analysis, unrelated)
- **Distribution**: PyPI (`pip install dataclaw`), single runtime dependency (`huggingface_hub>=0.20.0`)
- **Social**: Twitter/X account [@_dataclaw](https://x.com/_dataclaw), 2 HN threads (low engagement: 2 points, 2 comments)

## Strengths

- **Zero-friction capture**: Reads existing log files passively, no agent modification or instrumentation needed. Users install one package and run one command.

- **Multi-agent support**: 7 source parsers (Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Kimi CLI, Custom) covering the major coding agents. New sources are the primary growth driver.

- **Strong privacy guardrails**: Two-pass redaction (parse-time + confirm-time), 19 secret patterns, Shannon entropy analysis, username hashing, path anonymization, custom redaction strings, mandatory attestation gates. This is the product's core differentiator.

- **Agent-native design**: Every command outputs structured JSON with `next_steps`/`next_command`, designed to be driven by AI agents rather than humans. The `SKILL.md` installs directly into Claude Code's skill system.

- **Minimal dependencies**: Single runtime dependency (`huggingface_hub`), ~4,100 lines of Python, stdlib-heavy. Easy to audit, install, and trust.

- **Community discoverability**: All exports tagged `dataclaw` on HuggingFace, browsable at `huggingface.co/datasets?other=dataclaw`. An embedding atlas has already been built over the collective datasets.

- **Good test coverage**: 0.8:1 test-to-source ratio, edge case focus, CI matrix across Python 3.10-3.13.

## Limitations & Risks

- **Passive-only capture**: Cannot collect traces in real-time or from agents that don't write log files to disk. No streaming, no webhook, no runtime instrumentation. If an agent changes its log format, DataClaw's parser breaks.

- **No outcome signals**: The schema captures conversation content but has no `outcome` field, no success/failure signal, no task completion metadata. This limits value for RL/reward-modeling use cases.

- **No sub-agent hierarchy**: While Claude Code sessions include subagent JSONL files, the output schema flattens them. There's no explicit parent-child relationship, no agent role taxonomy, no delegation tracking.

- **Shallow schema**: No environment metadata (OS, language, framework), no git diff/commit correlation, no cost tracking beyond token counts, no session-level annotations or tags.

- **Federated but uncoordinated**: Each user publishes to their own HF repo. There's no canonical aggregated dataset, no schema versioning, no deduplication across contributors, no quality gate beyond self-attestation.

- **Config file permissions**: `~/.dataclaw/config.json` stores `redact_strings` (which may contain real secrets) with no explicit permission hardening (no `chmod 0600`). Source: `config.py` `save_config`.

- **Skill fetch has no integrity check**: `update-skill` downloads `SKILL.md` via plain HTTPS with no checksum verification. A compromised GitHub could deliver a malicious skill file. Source: `cli.py:589-600`.

- **Version string drift**: `pyproject.toml` says `0.3.2`, `__init__.py` says `0.3.0`. Minor but signals rapid iteration without full release hygiene.

- **Silent parser failures**: Non-custom source parsers return `None` on errors without logging, making missing sessions invisible. Source: `parser.py` various `_parse_*` functions.

- **No integration tests**: All tests are unit-level with mocking. No end-to-end test confirms the full discover -> parse -> redact -> write -> push pipeline.

- **1-month-old project**: Created 2026-02-24. High star count (2k) but very early. Only ~10-12 genuine community contributions despite 234 forks.

## Competitive Landscape

| Alternative | Differentiator | Trade-off |
|-------------|---------------|-----------|
| **opentraces.ai (us)** | 3 security tiers, outcome signals, sub-agent hierarchy, schema designed for RL/training | Not yet built; more complex schema may slow adoption |
| **claude-trace** | Real-time Claude Code hook, streams traces | Capture-only, no publication pipeline, no redaction |
| **Langfuse / AgentOps** | Full observability platform with dashboards, metrics | Enterprise SaaS, not designed for open data sharing |
| **claudebin.com** | Paste-and-share for individual conversations | Manual, no structured format, no bulk export |
| **OpenAmnesia** | Claude Code extension with selective forgetting | Privacy-focused, not publication-focused |
| **Synthetic datasets (Nemotron-RL, SWE-bench)** | Controlled, reproducible benchmarks | Lack diversity of real developer workflows |

## Community Signal

**Hacker News**: Two threads posted ~1 month ago ([47147604](https://news.ycombinator.com/item?id=47147604), [47155981](https://news.ycombinator.com/item?id=47155981)). Very low engagement (2 points, 2 comments). Both commenters praised the PII redaction and review-before-publish flow. No criticisms surfaced.

**Reddit**: No indexed discussions found.

**Twitter/X**: Has a dedicated account [@_dataclaw](https://x.com/_dataclaw). A HuggingFace staff member (Daniel van Strien) built an embedding atlas of public dataclaw datasets, showing clusters around models and topics.

**HuggingFace**: 32 datasets tagged `dataclaw`, but only ~10-12 represent unique original contributions. The rest are mirrors or copies of the creator's 549-session dataset.

**Overall sentiment**: Positive but niche. The framing as "performance art protest" resonates with open-source advocates but may limit enterprise/institutional adoption. The privacy-conscious design is consistently praised.

## Integration Analysis: opentraces.ai

### Fit Assessment

**Strong Fit / Direct Competitor**

DataClaw and opentraces.ai solve the same core problem: getting real-world agent traces from local machines to HuggingFace datasets with privacy protection. DataClaw is the closest existing implementation to what opentraces.ai aims to build. The differences are in schema depth, security tier granularity, and RL/training-readiness.

### Where DataClaw Leads (What We Should Learn From)

1. **Zero-friction adoption**: Passive log reading means zero behavior change for users. No hooks, no config, no runtime overhead. This is the right default for v0.1.

2. **Agent-native CLI protocol**: JSON output with `next_steps`/`next_command` on every command is brilliant UX for agent-driven workflows. Our CLI should adopt this pattern.

3. **Multi-source parsers**: Supporting 7 agents out of the box dramatically expands TAM. Their Claude Code parser (`parser.py:682`) and tool-result correlation logic (`_build_tool_result_map`) is worth studying.

4. **Staged pipeline with gates**: The auth -> configure -> review -> confirm -> push state machine prevents accidental data leaks. We should replicate this pattern.

5. **Attestation mechanism**: Requiring semantic attestations before publish is a creative accountability measure, especially for agent-driven flows where "click yes" is too easy.

6. **Community tagging**: All datasets tagged `dataclaw` on HF makes discovery trivial. We need an equivalent.

### Where opentraces.ai Can Differentiate

1. **Outcome signals**: DataClaw has no success/failure field. Our `outcome` field (success bool, signal_source, description) is critical for RL/reward-modeling consumers.

2. **Sub-agent hierarchy**: DataClaw flattens subagent sessions. Our explicit parent-child tracking with agent roles (explore/plan/main) provides richer training signal.

3. **Security tier system**: DataClaw has one tier (regex + entropy + manual review). Our 3-tier system (Open/Guarded/Strict) gives users granular control.

4. **Schema richness**: Environment metadata, git diff correlation, cost tracking, annotation support, schema versioning, all absent from DataClaw.

5. **Dataset governance**: DataClaw's federated-but-uncoordinated model (each user publishes to their own repo) creates discovery friction. We can offer a canonical aggregated dataset option.

6. **Real-time capture option**: DataClaw is passive-only. Our stop-hook architecture enables real-time capture alongside passive log reading.

7. **Standards alignment**: Our schema aligns with Agent Trace, ATIF, ADP, OTel conventions. DataClaw's schema is ad-hoc.

### Integration Points

- DataClaw's parser module (`dataclaw.parser`) could be imported directly as a session discovery/ingestion layer, potentially avoiding reimplementation of 7 source-specific parsers
- DataClaw's `secrets.py` (19 patterns + entropy analysis) could serve as our Tier 1 "Open" redaction engine, or at minimum as a reference for pattern coverage
- DataClaw's output JSONL could be consumed as an input format for opentraces.ai (migration/compatibility path)
- The `custom` source directory (`~/.dataclaw/custom/`) pattern could be adopted for our adapter system

### Effort Estimate

**Medium (weeks)** to build a differentiated v0.1 that surpasses DataClaw on schema depth and security tiers while matching its adoption friction.

### Strategic Options

1. **Build from scratch with DataClaw as reference**: Use their parsers and redaction patterns as architectural inspiration. Differentiate on schema, security tiers, and RL-readiness. Risk: slower to market.

2. **Fork and extend DataClaw**: Add outcome signals, sub-agent hierarchy, security tiers, schema versioning. Risk: inherits their architectural limitations (no real-time capture, tightly-coupled CLI).

3. **Build complementary**: Position opentraces.ai as a schema/pipeline standard that can ingest DataClaw exports as one input format. Users who already use DataClaw get a migration path. Risk: may not differentiate enough to attract new users.

4. **Build competitive with compatibility layer**: Build our own pipeline but include a `dataclaw` source adapter that reads existing DataClaw exports. Captures their community while offering a better product. This is likely the strongest position.

### Open Questions

- Should we support DataClaw's JSONL format as an import source? (Low effort, high compat value)
- Can we reuse their secret detection patterns under MIT license? (Yes, MIT allows this)
- How do we handle the "performance art protest" framing? opentraces.ai should have a more neutral/constructive positioning
- Is the 2k-star community large enough to matter for migration, or should we focus on greenfield users?
- Should we offer a canonical aggregated dataset, or follow their federated model?

## Key Takeaways

1. **DataClaw validates the market**: 2k stars and 32 HF datasets in 1 month proves real demand for crowdsourced agent trace sharing. The problem is real and people want to contribute.

2. **Schema depth is the competitive moat**: DataClaw's schema is sufficient for conversation replay but insufficient for RL, reward modeling, or training pipeline consumers. Adding outcome signals, sub-agent hierarchy, and environment metadata is where opentraces.ai can create lasting differentiation.

3. **Adoption friction is the real battleground**: DataClaw's zero-friction passive capture (read existing logs, run one command) set the bar. Any solution requiring agent modification, runtime hooks, or complex configuration will lose on adoption even if technically superior. Our v0.1 must match DataClaw's friction level while delivering a richer schema.

## Sources

- [GitHub: peteromallet/dataclaw](https://github.com/peteromallet/dataclaw)
- [HuggingFace: dataclaw-peteromallet dataset](https://huggingface.co/datasets/peteromallet/dataclaw-peteromallet)
- [HN: DataClaw discussion](https://news.ycombinator.com/item?id=47147604)
- [X/Twitter: @_dataclaw](https://x.com/_dataclaw)
- [Daniel van Strien's embedding atlas](https://x.com/vanstriendaniel/status/2027069088648855907)
- [DataClaw on LobeHub Skills Marketplace](https://lobehub.com/skills/peteromallet-dataclaw-dataclaw)
- [HuggingFace datasets tagged dataclaw](https://huggingface.co/datasets?other=dataclaw)
- Source code analysis via cloned repo at `/tmp/scout-ApYerx/dataclaw/` (all file path references from direct code reading)
