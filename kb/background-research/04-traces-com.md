# Traces.com: R&D Scouting Brief

> Research date: 2026-03-27
> Source: https://traces.com/ , https://traces.com/docs
> Category: platform (proprietary SaaS)
> Built by: Lab 0324, Inc. (operates as market.dev, GitHub: market-dot-dev)
> Purpose: Competitive analysis for open-source equivalent

---

## Overview

Traces is a proprietary platform that captures conversations between developers and AI coding agents, normalizes them into a unified format, and makes them shareable. The tagline is "Make Coding Agents Multiplayer." It provides a CLI-first workflow for publishing, browsing, and sharing AI coding sessions across 10+ agents, with team collaboration features, privacy controls, and git integration. Built by Lab 0324, Inc. (npm maintainer: `lab0324 <tarun@lab0324.xyz>`), which also operates market.dev.

## Problem It Solves

As AI coding agents become ubiquitous, there is no standard way to:
1. **Share** what an agent actually did during a session (beyond screenshots or copy-paste)
2. **Review** agent work alongside code changes in PRs
3. **Learn** from how others use agents effectively
4. **Audit** agent actions with sensitive data scrubbed
5. **Continue** someone else's agent session
6. **Track** team-wide agent usage patterns and analytics

Each agent stores sessions in its own proprietary format (JSONL, SQLite, JSON), making cross-agent sharing impossible without normalization.

## How It Works

### Architecture Overview

```
Developer's Machine                    Traces Cloud
+------------------+                   +------------------+
| Agent Session    |                   | Traces API       |
| (JSONL/SQLite/   |   traces share   | (actions.traces  |
|  JSON)           | ───────────────>  |  .com)           |
+------------------+                   +--------+---------+
        |                                       |
        v                                       v
+------------------+                   +------------------+
| Traces CLI       |                   | Traces Web       |
| - Discovery      |                   | (Next.js +       |
| - Parsing        |                   |  Convex backend) |
| - Normalization  |                   | - Viewer         |
| - Upload         |                   | - Profiles       |
| - Local SQLite   |                   | - Teams          |
+------------------+                   | - Analytics      |
                                       | - Community Feed |
                                       +------------------+
```

### Upload Pipeline (5 stages)

1. **Discovery** - Scans known agent storage paths for session data
2. **Parsing** - Reads agent-specific formats (JSONL, SQLite, JSON)
3. **Normalization** - Converts to unified typed message format
4. **Upload** - Batches messages to Traces API
5. **Processing** - Server-side generation of highlights, summaries, search indexes

### Session Resolution Priority

When using `--cwd`, the CLI resolves which session to share:
1. `TRACES_CURRENT_TRACE_ID` env var (exact trace ID)
2. `TRACES_CURRENT_SOURCE_PATH` env var (exact source file)
3. `TRACES_CURRENT_AGENT` env var (agent hint)
4. Directory matching (most recent trace matching `--cwd`)
5. Agent-latest fallback (most recent trace for `--agent`)

### Technology Stack

- **CLI**: Distributed as compiled binary (Rust/Go likely, given binary distribution via GitHub releases)
- **npm wrapper**: `@traces-sh/traces@0.4.9` (3.1 kB, zero deps, likely a thin wrapper that downloads the binary)
- **Web frontend**: Next.js (App Router, RSC streaming)
- **Backend**: Convex (real-time database)
- **Auth**: GitHub OAuth (device flow for CLI)
- **API base**: `api.traces.com` (storage), `actions.traces.com` (v1 REST API)
- **Binary distribution**: `market-dot-dev/traces-binaries` GitHub repo
- **Homebrew**: `market-dot-dev/homebrew-tap`
- **Fonts**: Inter, Berkeley Mono

---

## Key Concepts

### Trace
A captured coding agent session containing metadata and a sequence of messages. Each trace has:
- A unique ID and external ID (agent-generated session ID)
- Association with an agent type and model
- Visibility level (public/direct/private)
- Ownership (namespace + author)

### Namespace
An identity scope, either `individual` (personal) or `org` (team). All traces, API keys, and members belong to a namespace.

### Adapter
Agent-specific parser that reads the native session format and converts it to the normalized Traces format. One adapter per agent.

### Agent Identity
A non-human identity within an organization, used for CI/CD attribution. Linked to API keys so automated traces display the agent's name/avatar instead of the key creator's.

### Skill
An agent-level integration that allows the agent itself to run `traces share` during a session, triggered by natural language ("share this to traces").

---

## Normalized Message Schema

### Trace Object (from live trace page source, verified against 2 public traces)

```json
{
  "id": "string (internal ID, e.g., 'jn7cws0esztwg9acj80p346h3983p6qq')",
  "externalId": "string (agent-generated session UUID, e.g., '019d288e-d55a-7a20-8fe6-baad08128f46')",
  "title": "string (AI-generated via 'ai_title' field)",
  "agentId": "string (e.g., 'claude-code', 'codex', 'cursor')",
  "model": "string (e.g., 'gpt-5-4', 'sonnet-4-6')",
  "modelName": "string (human-readable, e.g., 'GPT-5.4', 'Claude Sonnet 4.6')",
  "visibility": "'public' | 'direct' | 'private'",
  "createdAt": "number (Unix ms, e.g., 1774631586269)",
  "updatedAt": "number (Unix ms)",
  "messageCount": "number (e.g., 1065, 4)",
  "summaryMessageCount": "number",
  "downloadCount": "number",
  "projectPath": "string (original filesystem path, e.g., 'e:\\Desktop\\Poker')",
  "ai_title": "string (AI-generated title)",
  "ai_summary": "string (AI-generated 1-2 sentence summary)",
  "searchText": "string (concatenated readable content for full-text indexing)",
  "searchEmbedding": "float[64] (vector embedding for semantic search)",
  "messageTypeCounts": {
    "user_message": "number",
    "agent_text": "number",
    "agent_thinking": "number",
    "agent_context": "number",
    "tool_call": "number"
  },
  "toolTypeCounts": {
    "terminal_command": "number",
    "edit": "number",
    "mcp__playwright__browser_navigate": "number",
    "update_plan": "number",
    "view_image": "number"
  },
  "namespace": {
    "id": "string",
    "slug": "string",
    "displayName": "string",
    "type": "'org' | 'individual'",
    "avatarUrl": "string"
  },
  "author": {
    "id": "string",
    "slug": "string",
    "displayName": "string",
    "avatarUrl": "string"
  }
}
```

### Message Types (confirmed from live trace `messageTypeCounts`)

Traces normalizes all agent messages into 5 message types:

| Message Type | Description | Observed In |
|-------------|-------------|-------------|
| `user_message` | User-submitted prompt | All agents |
| `agent_text` | Agent prose, explanations, reasoning output | All agents |
| `agent_thinking` | Internal reasoning/chain-of-thought (may be 0) | Claude Code, Codex |
| `agent_context` | IDE context injection (active file, selection, etc.) | Cursor, Copilot |
| `tool_call` | Tool invocations (further typed by `toolTypeCounts`) | All agents |

### Tool Types (confirmed from live trace `toolTypeCounts`)

| Tool Type | Description |
|-----------|-------------|
| `terminal_command` | Shell/bash command execution |
| `edit` | File edit operations |
| `update_plan` | Agent plan updates |
| `view_image` | Image viewing/screenshot |
| `mcp__*` | MCP tool calls (e.g., `mcp__playwright__browser_navigate`, `mcp__playwright__browser_run_code`, `mcp__playwright__browser_wait_for`) |

Tool types appear to be open-ended, new MCP tools appear as `mcp__{server}__{tool}` keys.

### Message Object (from API curl example)

```json
{
  "externalId": "string (e.g., 'msg_001')",
  "role": "'user' | 'assistant'",
  "parts": [
    {
      "type": "text",
      "text": "string"
    }
  ]
}
```

Messages use a **typed parts** system. The upload API accepts a simple `role` + `parts` format, but the server-side processing classifies messages into the 5 types above and extracts tool type counts. The classification likely happens during the "Processing" stage of the upload pipeline.

### Download API (confirmed from trace viewer page)

```
GET https://actions.traces.com/v1/traces/{externalId}/download
Authorization: Bearer trk_...  (or public traces may not require auth)
```

Download button present on every trace viewer page, returning the full trace content.

### API Endpoints (from sandboxed environments docs + live trace pages)

| Method | Endpoint | Purpose | Source |
|--------|----------|---------|--------|
| `PUT` | `/v1/traces/{trace-id}` | Create or update a trace | Docs |
| `POST` | `/v1/traces/{trace-id}/messages/batch` | Add messages in batch | Docs |
| `GET` | `/v1/traces/{externalId}/download` | Download full trace content | Trace viewer |

**Base URL**: `https://actions.traces.com`

**Additional observed endpoints** (from page source):
- `api.traces.com/api/storage/` - Storage API (exact endpoints unknown)
- Convex backend handles real-time queries via `traces:get` function name

**Auth header**: `Authorization: Bearer trk_...`

**Create/update trace payload**:
```json
{
  "title": "CI Run",
  "agent": "ci-bot",
  "visibility": "private"
}
```

**Batch messages payload**:
```json
{
  "messages": [
    {
      "externalId": "msg_001",
      "role": "user",
      "parts": [{"type": "text", "text": "Analyze codebase"}]
    },
    {
      "externalId": "msg_002",
      "role": "assistant",
      "parts": [{"type": "text", "text": "Found 3 potential issues..."}]
    }
  ]
}
```

### JSON Output (from `traces share --json`)

```json
{
  "ok": true,
  "data": {
    "traceId": "abc123",
    "sharedUrl": "https://www.traces.com/s/abc123",
    "visibility": "direct",
    "agentId": "claude-code"
  }
}
```

---

## Agent Compatibility Matrix

### Supported Agents & Storage Formats

| Agent | Identifier | Storage Format | Detection Signature |
|-------|-----------|----------------|-------------------|
| Claude Code | `claude-code` | JSONL | `.claude/` directory |
| Cursor | `cursor` | SQLite | storage databases + project markers |
| OpenCode | `opencode` | JSON + SQLite | `.opencode/` or XDG data dirs |
| Codex | `codex` | JSON | `.codex/` or `codex.json` |
| Gemini CLI | `gemini-cli` | JSON | `.gemini/` directories |
| Pi | `pi` | JSON | `.pi/` directories |
| Amp | `amp` | JSON | `.amp/` and XDG data dirs |
| Copilot (VS Code) | `copilot` | SQLite | `.copilot/` and instructions markers |
| Cline | `cline` | JSON | `.cline/` and rules files |
| OpenClaw | `openclaw` | JSONL | `.openclaw/agents/main/sessions` |

### Adapter Behavior

1. Auto-detects agent from characteristic files/directories (or accepts `--agent` flag)
2. Reads session data from known storage paths
3. Normalizes into typed trace messages (parts-based format)
4. Uploads via API

### Session Matching Methods (per agent)

| Agent | Matching Method |
|-------|----------------|
| Claude Code, Cursor | Session hooks, active session targeted reliably |
| OpenCode | Plugin installed that passes session context |
| Codex, Gemini CLI, Pi, Cline, Amp, Copilot | Working-directory matching (less reliable, may need disambiguation) |

### Environment Variable Overrides

| Variable | Purpose |
|----------|---------|
| `TRACES_CURRENT_TRACE_ID` | Override session resolution with exact trace ID |
| `TRACES_CURRENT_SOURCE_PATH` | Override source path for session discovery |
| `TRACES_CURRENT_AGENT` | Override agent detection |
| `TRACES_API_KEY` | API key auth (prefix: `trk_`) |
| `TRACES_CURSOR_GLOBAL_DB` | Override Cursor database location (Docker) |

---

## CLI Reference (Complete)

### Installation

```bash
# Homebrew
brew install market-dot-dev/tap/traces

# npm (thin wrapper, downloads binary)
npm i -g @traces-sh/traces

# Shell script
curl -fsSL https://www.traces.com/install | bash
```

Install script details:
- Detects OS (darwin/linux/windows) and arch (arm64/x64)
- Downloads from `market-dot-dev/traces-binaries` GitHub releases
- Binary placed in `~/.traces/bin/`
- Auto-adds to PATH via shell config detection (.zshrc, .bashrc, config.fish)
- GitHub Actions support via `$GITHUB_PATH`
- Optional skill install via `npx skills add market-dot-dev/traces`

### Command Reference

#### Core Navigation
| Command | Description |
|---------|-------------|
| `traces` | Open interactive TUI to browse traces |
| `traces list` | List traces without TUI |
| `traces list --limit N` | Limit results |
| `traces list --agent ID` | Filter by agent |
| `traces list --json` | JSON output |
| `traces list --diagnostics` | Include diagnostic metadata |

#### Sharing
| Command | Description |
|---------|-------------|
| `traces share --cwd .` | Share most recent trace in current directory |
| `traces share --trace-id ID` | Share specific trace by ID |
| `traces share --source-path PATH` | Share by source file path |
| `traces share --visibility MODE` | Set public/direct/private |
| `traces share --agent ID` | Filter by agent (or `auto`) |
| `traces share --key trk_...` | API key auth |
| `traces share --json` | Machine-readable JSON output |
| `traces share --follow` | Keep syncing as session continues |
| `traces share --list` | List available traces instead of sharing |

**Constraints**: `--trace-id`, `--source-path`, and `--cwd` are mutually exclusive.

#### Authentication
| Command | Description |
|---------|-------------|
| `traces login` | GitHub OAuth device flow |
| `traces login --no-browser` | Print URL for remote/SSH sessions |
| `traces logout` | Revoke session + clear local credentials |
| `traces whoami` | Show username, active namespace, namespace type |

#### Namespace Management
| Command | Description |
|---------|-------------|
| `traces namespace list` | List all namespaces (personal + orgs) |
| `traces ns ls` | Alias for namespace list |
| `traces namespace use <slug>` | Switch active namespace |
| `traces transfer <id> --to <slug>` | Move trace to another namespace |

#### Setup & Maintenance
| Command | Description |
|---------|-------------|
| `traces setup` | Interactive agent skill setup |
| `traces setup skills` | Same as `traces setup` |
| `traces setup skills --yes` | Non-interactive, all detected agents |
| `traces setup skills --agent <id>` | Target specific agent(s), repeatable |
| `traces setup skills --global` | Apply globally (home-dir config) |
| `traces setup git` | Install post-commit hook for trace-commit linking |
| `traces remove skills` | Remove agent sharing setup |
| `traces remove skills --global` | Remove global setup |
| `traces remove skills --json` | JSON output |
| `traces remove git` | Remove git hook |
| `traces sync <externalId>` | Download full message content for a trace |

#### Diagnostics
| Command | Description |
|---------|-------------|
| `traces doctor` | Check binary, DB, auth, network, agent detection |
| `traces status` | Show local DB location, auth status, namespace |
| `traces version` | Show version |
| `traces reset` | Delete local DB (force resync) |
| `traces reset --force` | Skip confirmation |
| `traces reset --all` | Also clear credentials |
| `traces upgrade` | Update to latest version |
| `traces upgrade <version>` | Update to specific version |
| `traces uninstall` | Remove traces binary |

### TUI Interface

The interactive TUI (launched by bare `traces` command) shows:
- Session list with columns: Time, Directory, Agent, Message count, Title, Action
- Per-trace actions: Publish, Open Link, Copy Link, Refresh, Unpublish
- Keyboard shortcuts (at minimum: `Q: Quit`)
- Background discovery for git-linked traces when inside a repo

---

## Git Integration

### Post-Commit Hook

**Setup**: `traces setup git`

Installs `.git/hooks/traces-post-commit` and injects a guarded call into `.git/hooks/post-commit`. Chains with existing hooks (husky, lefthook) without overwriting.

**What the hook does on each commit**:
1. Runs `traces list --dir . --since <previous commit timestamp>` to find recent traces
2. Writes external IDs into `refs/notes/traces` on the commit
3. Runs `traces share` in background (uploads unshared traces)
4. Pushes notes to the remote

**Note format** (appended, not replaced):
```
traces:<externalId> <sharedUrl>
traces:<externalId>
```

The `traces:` prefix prevents collisions with other git notes tools.

**Verification**:
```bash
git notes --ref=traces show HEAD
git log --notes=traces -3
```

**Removal**: `traces remove git`

### CLI Discovery

When TUI opens inside a git repo, background discovery:
1. Reads trace IDs from `refs/notes/traces` on last 20 commits
2. Queries API using repo's remote URL for matching traces in namespace
3. Deduplicates against local SQLite store
4. Fetches metadata-only entries (no message content downloaded)
5. Auto-refreshes list when new traces appear

Full content download: `traces sync <externalId>`

This enables: clone a repo, open Traces TUI, browse all traces from entire git history including other contributors' work.

---

## Privacy & Data Security

### Visibility Levels

| Level | Who Can View | Appears in Feeds | Default For |
|-------|-------------|-----------------|-------------|
| `public` | Anyone | Yes | - |
| `direct` | Anyone with link | No | Individual namespaces |
| `private` | Namespace members only | No | Organization namespaces |

### Automatic Sensitive Data Scrubbing

On publish, the system:
- Strips API keys, emails, database credentials
- Replaces with `[REDACTED]` markers
- Keeps reasoning and code context intact

### Team-Level Policies

Admins can set allowed visibility levels for the entire organization, preventing members from accidentally publishing traces as public.

---

## Organizations & Teams

### Organization Model

- Shared namespace for traces, members, agents, and API keys
- Slug is permanent (part of URL, cannot be changed)
- Creator becomes first admin
- Settings at `/<org-slug>/settings`

### Roles & Permissions

| Permission | Admin | Member |
|-----------|-------|--------|
| View/upload traces | Yes | Yes |
| Share traces | Yes | Yes |
| Invite teammates | Yes | No |
| Change member roles | Yes | No |
| Remove members | Yes | No |
| Manage org settings | Yes | No |

### Invitation Flow

1. Admin generates invite at `/<org-slug>/settings/members`
2. Invite link format: `https://www.traces.com/invite/A1B2C3D4E`
3. Invites expire after 24 hours
4. New members join with `member` role by default
5. Admins can promote to admin or demote

### Agent Identities

Non-human identities for CI/CD:
- One agent per API key, one agent can hold multiple keys
- Up to 25 agents per namespace
- Changing agent name retroactively updates all its traces
- Deleting agent preserves trace content, reverts to creator attribution
- API keys are not revoked when agent is deleted

### API Keys

- Format: `trk_*` prefix
- Shown only once on creation
- Max 25 active keys per namespace
- Scopes:
  - `traces:write` - Create/update traces & messages
  - `traces:read` - Read traces & messages
  - `namespace:read` - Read namespace info/members
- Revocable anytime

### Team Analytics Dashboard

- Top agents used (e.g., Claude Code, Codex, Cursor, Gemini CLI, Amp)
- Average session length (example: 47 minutes)
- AI output percentage (example: 82.0%)

---

## Authentication System

### Methods

| Method | Use Case | Token Type |
|--------|----------|------------|
| CLI device auth | Interactive CLI login | Device token (stored locally) |
| GitHub OAuth | Web app login | Session cookie |
| API key | CI/CD, scripts, headless | `trk_` prefixed key |

### Auth Precedence (highest to lowest)

1. `--key` flag on CLI command
2. Stored login from `traces login`
3. `TRACES_API_KEY` environment variable

### Token Lifecycle

- Tokens auto-refresh
- `traces logout` revokes server session + clears local credentials
- `--no-browser` flag for remote/SSH environments (prints URL to open elsewhere)

---

## CI/CD Integration

### GitHub Actions

```yaml
name: Share Trace
on: [push]
jobs:
  share:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install Traces CLI
        run: |
          curl -fsSL https://www.traces.com/install | bash
          echo "$HOME/.traces/bin" >> $GITHUB_PATH
      - name: Run agent task
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: claude --print "Review this code for bugs"
      - name: Share trace
        env:
          TRACES_API_KEY: ${{ secrets.TRACES_API_KEY }}
        run: |
          RESULT=$(traces share --cwd . --agent auto --json)
          URL=$(echo "$RESULT" | jq -r '.data.sharedUrl')
          echo "### Trace shared" >> $GITHUB_STEP_SUMMARY
          echo "[$URL]($URL)" >> $GITHUB_STEP_SUMMARY
```

### PR Comment Integration

```yaml
- name: Comment on PR
  uses: actions/github-script@v7
  with:
    script: |
      github.rest.issues.createComment({
        issue_number: context.issue.number,
        owner: context.repo.owner,
        repo: context.repo.repo,
        body: `Agent review trace: ${{ steps.share.outputs.url }}`
      })
```

### Docker

```dockerfile
FROM node:22-slim
RUN curl -fsSL https://www.traces.com/install | bash \
    && ln -s /root/.traces/bin/traces /usr/local/bin/traces
WORKDIR /app
COPY . .
CMD ["sh", "-c", "claude --print \"$PROMPT\" && traces share --cwd . --agent auto --json"]
```

### GitLab CI

```yaml
share-trace:
  image: node:22-slim
  script:
    - curl -fsSL https://www.traces.com/install | bash
    - export PATH="$HOME/.traces/bin:$PATH"
    - claude --print "Review this MR"
    - traces share --cwd . --agent auto --json
  variables:
    TRACES_API_KEY: $TRACES_API_KEY
    ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
```

### Raw HTTP (No CLI)

```bash
BASE_URL="https://actions.traces.com"
TRACE_ID="ci-$(date +%s)-${GITHUB_RUN_ID:-local}"

# Create trace
curl -sf -X PUT "$BASE_URL/v1/traces/$TRACE_ID" \
  -H "Authorization: Bearer $TRACES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "CI Run", "agent": "ci-bot", "visibility": "private"}'

# Add messages
curl -sf -X POST "$BASE_URL/v1/traces/$TRACE_ID/messages/batch" \
  -H "Authorization: Bearer $TRACES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"externalId": "msg_001", "role": "user", "parts": [{"type": "text", "text": "Analyze codebase"}]},
      {"externalId": "msg_002", "role": "assistant", "parts": [{"type": "text", "text": "Found 3 potential issues..."}]}
    ]
  }'
```

---

## Web Application Features

### Community Feed
- Public traces browsable by anyone
- Shows: title, agent, model, message count, author, timestamp
- Example traces range from 4 messages to 2,410 messages

### Profile Pages
- Individual profiles: personal trace feed, avatar, display name
- Organization profiles: shared trace feed, team analytics

### Trace Viewer (confirmed from live trace pages)
- **Two tabs**: "Highlights" (`/s/{id}`) and "Full Trace" (`/s/{id}/full`)
- AI-generated summary with expandable "Read more" section
- Full conversation replay, message-by-message browsing
- Tool call rendering with type-specific formatting (terminal commands, edits, MCP calls)
- Download button linking to `actions.traces.com/v1/traces/{externalId}/download`
- Metadata sidebar: agent, model, shared timestamp, updated timestamp, message count
- Redacted content displayed with `[REDACTED]` markers
- OG image auto-generated per trace (1200x630 PNG) for social sharing
- Server-side rendering via Next.js RSC with Convex real-time state (`preloadedTrace` injected)
- 64-dimensional vector embedding stored per trace for semantic search

### URL Patterns
- Traces: `traces.com/s/{trace-id}`
- Trace highlights: `traces.com/s/{trace-id}` (default tab)
- Full trace: `traces.com/s/{trace-id}/full`
- Profiles: `traces.com/@{slug}`
- Org settings: `traces.com/{org-slug}/settings`
- Download: `actions.traces.com/v1/traces/{externalId}/download`

### Account Features
- Display name, description, avatar
- Linked identities (login providers)
- Personal API keys (scoped to personal namespace)

---

## Pricing & Business Model

- "Free to get started" (stated on homepage)
- No public pricing tiers documented
- Billing page says "Billing docs are coming soon"
- Planned coverage: plans, billing owners, payment methods, invoices, seat/usage details
- Revenue model likely freemium with paid team features

---

## Competitive Landscape

| Alternative | Type | Differentiator | Trade-off |
|-------------|------|---------------|-----------|
| DataClaw | CLI tool | Open-source, HF dataset output, 19 redaction patterns, attestation gates | Passive-only (no real-time), no outcome signals, federated but uncoordinated |
| DEV Community Agent Sessions | Platform | Embeds in blog posts, slicing/curation, community-native | Tied to DEV ecosystem, less CLI-focused |
| Agent Trace (agent-trace.dev) | Open Spec | Vendor-neutral file-level attribution, VCS-native | Data format only, no UI/sharing/collaboration |
| GitHub PR comments | Built-in | Already where code review happens | No trace rendering, just links |
| Langfuse / LangSmith | LLM Observability | Deep eval/metrics/cost tracking | Runtime telemetry, not session sharing |
| claudebin.com | Web tool | Paste-and-share for individual conversations | Manual, no structured format, no bulk export |

### Key Positioning

Traces occupies a unique niche: it's not LLM observability (Langfuse, LangSmith, Datadog) and it's not code attribution (Agent Trace spec). It's specifically **session sharing and collaboration** for coding agents, the social/team layer on top of agent work.

---

## Community Signal

- Very new product (npm package published March 2026, 53 versions iterated fast)
- No Reddit or Hacker News discussions found yet
- Discord community linked for support
- DEV Community is building a competing/complementary feature (agent session embeds)
- Testimonial from Millin Gabani (CEO of Workers): "Github increasingly doesn't feel like the best place to understand the work done on a codebase."
- The problem space is being validated by multiple players simultaneously

---

## Strengths

1. **CLI-first design** with excellent DX, multiple install paths (Homebrew, npm, curl)
2. **Wide agent support** (10 agents) with auto-detection and adapter architecture
3. **Unified normalization** across wildly different storage formats (JSONL, SQLite, JSON)
4. **Privacy-aware** with automatic sensitive data scrubbing
5. **Git-native integration** via post-commit hooks and git notes
6. **Follow mode** for real-time session streaming
7. **Non-human identities** for CI/CD attribution (thoughtful enterprise feature)
8. **Raw HTTP API** that works without the CLI
9. **Session resolution system** with env var overrides and multi-method fallback
10. **Fast iteration** (53 npm versions, active development)

## Limitations & Risks

1. **Proprietary, closed-source** - no self-hosting, no code inspection, vendor lock-in. Binary distributed via GitHub releases with no source repo. CLI is compiled (not inspectable), web is SaaS-only.
2. **GitHub-only auth** - no GitLab, Bitbucket, or email auth. Limits adoption for teams on other forges.
3. **No public API documentation** - the only documented endpoints are `PUT /v1/traces/{id}` and `POST /v1/traces/{id}/messages/batch` from the sandboxed environments guide, plus the `GET /v1/traces/{externalId}/download` visible on trace pages. No OpenAPI spec, no rate limit docs, no error code reference.
4. **Pricing not disclosed** - billing page says "coming soon." Uncertainty for teams evaluating adoption at scale.
5. **No outcome/quality signals** - traces capture conversations but have no success/failure field, no task completion signal, no quality rating. This limits value for RL/training use cases.
6. **Session continuation still "coming soon"** - download exists (via download button), but resuming a trace in another agent is not yet shipped. The download format is undocumented.
7. **Single company dependency** - small team (Lab 0324, single npm maintainer `lab0324`), unclear funding/sustainability. 53 versions in ~1 month suggests rapid iteration but also instability risk.
8. **No offline viewer** - requires web access to view shared traces. No static HTML export or local rendering.
9. **Community feed discoverability** - no search, tagging, filtering, or categorization visible on the community page. No way to find traces by topic, language, framework, or agent.
10. **Git integration requires push access** for notes syncing. The post-commit hook pushes notes to remote, which fails in fork-based workflows without upstream write access.
11. **No per-message metadata** - individual messages lack timestamps, token counts, or cost data in the surface payload. `messageTypeCounts` and `toolTypeCounts` are trace-level aggregates only.
12. **Shallow privacy controls** - redaction is automatic with no user configuration of patterns, no preview of what will be redacted before publish, no redaction audit log. Compare to DataClaw's 19 configurable regex patterns + entropy analysis + custom redaction strings + attestation gates.
13. **No dataset/training export** - traces are stored in Traces' proprietary backend. No bulk export to Parquet, JSONL, or HuggingFace datasets for ML training pipelines.

---

## Feature Map for Open-Source Implementation

### P0 - Core (Must Have for Parity)

| Feature | Traces.com Approach | Open-Source Approach Notes |
|---------|-------------------|--------------------------|
| Agent adapters | 10 adapters, auto-detection via directory signatures | Same approach, open adapter plugin system |
| Normalized schema | Typed parts message format | Define open schema spec (consider Agent Trace alignment) |
| CLI `share` command | Upload to Traces API | Upload to configurable backend (HF, S3, local) |
| CLI `list` command | Local SQLite + TUI | Same |
| Visibility controls | public/direct/private | Same, plus self-hosted access control |
| Sensitive data scrubbing | Automatic `[REDACTED]` replacement | Same, open regex/pattern config |
| JSON output mode | `--json` flag on all commands | Same |

### P1 - Collaboration (Key Differentiators)

| Feature | Traces.com Approach | Open-Source Approach Notes |
|---------|-------------------|--------------------------|
| Web viewer | Next.js + Convex SaaS | Static HTML viewer, HF Spaces, or self-hosted |
| Community feed | Central public feed | HF Hub datasets, decentralized feeds |
| Git hooks | Post-commit hook + git notes | Same, but with open note format |
| CLI discovery | Background discovery from git notes + API | Same, from git notes + local/remote store |
| Follow mode | `--follow` for real-time sync | Tail + upload loop |
| Session continuation | "Coming soon" | Priority: export to agent-native format |

### P2 - Teams & Enterprise

| Feature | Traces.com Approach | Open-Source Approach Notes |
|---------|-------------------|--------------------------|
| Organizations | Namespace-based, admin/member roles | Optional, self-hosted teams |
| Agent identities | Non-human CI/CD attribution | Metadata field on traces |
| API keys | `trk_*` scoped keys | Standard API key or token auth |
| Team analytics | Dashboard with agent/usage stats | Local analytics or Grafana integration |
| Namespace transfer | `traces transfer` command | Move between storage backends |

### P3 - Platform

| Feature | Traces.com Approach | Open-Source Approach Notes |
|---------|-------------------|--------------------------|
| PR bot comments | tracebot on PRs | GitHub Action with viewer links |
| Agent skill integration | `traces setup skills` | Same, MCP tool or agent skill files |
| CI/CD templates | GitHub Actions, GitLab CI, Docker | Same templates, different backend |
| Raw HTTP API | REST with PUT/POST | Same or simpler |

---

## Schema Specification (For Open-Source Implementation)

### Trace Metadata (confirmed from live trace page source)

```typescript
interface Trace {
  id: string;                    // Internal unique ID (e.g., 'jn7cws0esztwg9acj80p346h3983p6qq')
  externalId: string;            // Agent-generated session UUID
  title: string;                 // AI-generated via ai_title
  agentId: AgentId;              // Agent identifier
  model?: string;                // Model identifier (e.g., 'sonnet-4-6', 'gpt-5-4')
  modelName?: string;            // Human-readable (e.g., 'GPT-5.4', 'Claude Sonnet 4.6')
  visibility: 'public' | 'direct' | 'private';
  createdAt: number;             // Unix timestamp (ms)
  updatedAt: number;             // Unix timestamp (ms)
  messageCount: number;
  summaryMessageCount: number;   // Messages included in highlights/summary view
  downloadCount: number;         // Number of times trace has been downloaded
  projectPath?: string;          // Original filesystem path (e.g., 'e:\\Desktop\\Poker')

  // AI-generated fields (server-side processing)
  ai_title: string;              // AI-generated title
  ai_summary: string;            // AI-generated 1-2 sentence summary
  searchText: string;            // Concatenated readable content for full-text search
  searchEmbedding: number[];     // 64-dim vector for semantic search

  // Aggregate statistics
  messageTypeCounts: MessageTypeCounts;
  toolTypeCounts: Record<string, number>;  // Open-ended, keys are tool type strings

  namespace: Namespace;
  author: Author;
}

interface MessageTypeCounts {
  user_message: number;
  agent_text: number;
  agent_thinking: number;
  agent_context: number;
  tool_call: number;
}

type AgentId =
  | 'claude-code'
  | 'cursor'
  | 'opencode'
  | 'codex'
  | 'gemini-cli'
  | 'pi'
  | 'amp'
  | 'copilot'
  | 'cline'
  | 'openclaw';

interface Namespace {
  id: string;
  slug: string;
  displayName: string;
  type: 'org' | 'individual';
  avatarUrl?: string;
}

interface Author {
  id: string;
  slug: string;
  displayName: string;
  avatarUrl?: string;
}
```

### Message Format (confirmed message types, part structure inferred from upload API)

```typescript
// Upload API format (what the CLI sends)
interface UploadMessage {
  externalId: string;            // Unique within trace
  role: 'user' | 'assistant';   // Conversation role
  parts: Part[];                 // Typed content blocks
}

type Part =
  | { type: 'text'; text: string };
  // Upload API only documents 'text' type.
  // Server-side processing classifies messages into the 5 types
  // (user_message, agent_text, agent_thinking, agent_context, tool_call)
  // and extracts tool types. The adapter handles this mapping.

// Internal normalized format (what the server stores, from live trace analysis)
// Messages are classified into these types post-upload:
type MessageType =
  | 'user_message'    // User-submitted prompts
  | 'agent_text'      // Agent prose/explanations
  | 'agent_thinking'  // Chain-of-thought reasoning (may be empty)
  | 'agent_context'   // IDE context injection (file, selection)
  | 'tool_call';      // Tool invocations (terminal, edit, MCP, etc.)

// Confirmed tool types from live traces:
type ToolType =
  | 'terminal_command'                      // Shell execution
  | 'edit'                                  // File edits
  | 'update_plan'                           // Agent plan updates
  | 'view_image'                            // Image/screenshot viewing
  | `mcp__${string}__${string}`;            // MCP tools: mcp__{server}__{tool}
  // Examples: mcp__playwright__browser_navigate,
  //           mcp__playwright__browser_run_code,
  //           mcp__playwright__browser_wait_for
```

### Git Notes Format

```
traces:<externalId> <sharedUrl>
traces:<externalId>
```

One trace per line, `traces:` prefix for namespace isolation, shared URL appended when available.

### Adapter Interface

```typescript
interface AgentAdapter {
  id: AgentId;
  detect(cwd: string): boolean;                    // Check if agent used in directory
  discoverSessions(cwd: string): SessionInfo[];    // Find all sessions
  parseSession(session: SessionInfo): Message[];   // Convert to normalized messages
}

interface SessionInfo {
  id: string;
  sourcePath: string;
  agent: AgentId;
  timestamp: number;
  messageCount?: number;
}
```

### Storage Format Reference (Per Agent)

| Agent | Format | Typical Path |
|-------|--------|-------------|
| Claude Code | JSONL | `.claude/projects/*/session.jsonl` (inferred) |
| Cursor | SQLite | Global storage DB |
| OpenCode | JSON + SQLite | `.opencode/` or XDG data dirs |
| Codex | JSON | `.codex/` or `codex.json` |
| Gemini CLI | JSON | `.gemini/` |
| Pi | JSON | `.pi/` |
| Amp | JSON | `.amp/` and XDG data dirs |
| Copilot | SQLite | `.copilot/` |
| Cline | JSON | `.cline/` |
| OpenClaw | JSONL | `.openclaw/agents/main/sessions` |

---

## Integration Analysis: opentraces.ai

### Fit Assessment

**Direct Competitor / Primary Reference Implementation**

Traces.com and opentraces.ai address overlapping problem spaces, both normalize agent sessions and make them shareable, but with fundamentally different architectures (proprietary SaaS vs. open-source HF-native) and different primary consumers (team collaboration vs. ML training data).

### Where Traces.com Leads (What We Should Learn From)

1. **Adapter architecture**: 10 agents with auto-detection via directory signatures is the right pattern. Their session resolution priority system (env var > source path > directory match > agent fallback) is well-thought-out. Our adapter system should follow the same tiered resolution.

2. **CLI DX**: Multiple install paths (Homebrew, npm, curl), structured JSON output on all commands, `--follow` for real-time streaming, `--agent auto` for zero-config. The TUI for browsing local traces is a strong addition over pure CLI.

3. **Git integration**: Post-commit hooks writing to `refs/notes/traces` is elegant. The `traces:` prefix prevents collisions. CLI discovery from git notes enables "clone repo, see all traces" without manual imports. This pattern works equally well with decentralized backends.

4. **Non-human identities**: Agent identities for CI/CD attribution (separate from human users, linked to API keys) is a thoughtful enterprise feature. Shows maturity in thinking about automated workflows.

5. **Normalized message types**: The 5-type classification (`user_message`, `agent_text`, `agent_thinking`, `agent_context`, `tool_call`) with open-ended tool type counts (`terminal_command`, `edit`, `mcp__*`) is a clean abstraction. Server-side AI summarization (ai_title, ai_summary) and 64-dim embeddings for semantic search add real discovery value.

6. **Privacy defaults**: `direct` visibility as default for individuals, `private` for orgs. Team-level visibility policies. Automatic scrubbing. These defaults protect users from accidental exposure.

### Where opentraces.ai Can Differentiate

1. **Open data / ML-first**: Traces.com stores data in a proprietary backend with no bulk export. We publish to HuggingFace datasets in standard formats (JSONL, Parquet), directly consumable by training pipelines. This is the fundamental differentiator.

2. **Outcome signals**: Traces.com has zero success/failure signals. Our `outcome` field (success bool, signal_source, description) makes traces useful for RL/reward modeling, not just conversation replay.

3. **Sub-agent hierarchy**: Traces.com counts messages by type but does not model parent-child agent relationships. Our explicit delegation tracking with agent roles (explore/plan/main) provides richer training signal.

4. **Configurable redaction**: Traces.com offers automatic scrubbing with no user control. We offer DataClaw-inspired 3-tier security (Open/Guarded/Strict) with configurable regex patterns, entropy analysis, custom redaction strings, and attestation gates.

5. **Per-message metadata**: Traces.com stores only trace-level aggregates (messageTypeCounts, toolTypeCounts). Our schema includes per-message timestamps, token counts, and cost data, critical for training data quality and cost analysis.

6. **Schema richness**: Environment metadata (OS, language, framework), git diff correlation, annotation support, schema versioning, all absent from Traces.com.

7. **Self-hostable**: No vendor lock-in. Data lives on HF Hub or local filesystem. No proprietary API dependency.

### What Traces.com Has That We Don't Need to Replicate

1. **Team collaboration SaaS** (orgs, roles, invites, team analytics) - our focus is open data contribution, not team workflow management
2. **Real-time Convex backend** - we push to HF datasets, no need for real-time sync
3. **Community social feed** - HF Hub serves as our discovery layer
4. **PR bot comments** - nice-to-have but not core to open data mission

### Architectural Insights for Implementation

1. **Adapter-per-agent with auto-detection** is the proven pattern. Both Traces.com (10 agents) and DataClaw (7 agents) independently converged on this.
2. **Server-side AI processing** (title generation, summarization, embeddings) is valuable for discoverability. We could run this client-side before upload or as a HF Space post-processor.
3. **The upload API is simple**: just PUT trace metadata + POST batch messages. Our HF push can be similarly simple, JSONL write + `huggingface_hub` upload.
4. **Git notes for trace-commit linking** is decentralized and works with any backend. We should adopt the `traces:` prefix convention (or our own `opentraces:` prefix).
5. **The download endpoint** suggests they store normalized traces in a retrievable format. Understanding this format (by downloading public traces) could inform our schema design.

### Strategic Position

Traces.com is building the **GitHub of agent sessions**, a centralized platform for sharing and collaboration. We are building the **Commons of agent traces**, open data for the ML training ecosystem. These are complementary rather than directly competitive:

- A user could share a trace on Traces.com for team review AND contribute it to opentraces.ai for training data
- Our CLI could eventually support `--backend traces` alongside `--backend hf` and `--backend local`
- Traces.com's proprietary lock-in is our strongest recruiting argument for open-source advocates

### Effort Estimate

**Medium (weeks)** for core parity on adapter coverage and CLI DX. The adapters are the primary engineering cost, the 10 agent formats, detection signatures, and normalization logic. Schema and backend are simpler than Traces.com since we push to HF rather than maintaining a real-time SaaS.

### Open Questions

- Should we support importing traces from Traces.com's download endpoint as a migration path?
- Can we adopt their 5 message types (`user_message`, `agent_text`, `agent_thinking`, `agent_context`, `tool_call`) as-is, or do we need a richer taxonomy for training data consumers?
- Their tool type naming (`mcp__{server}__{tool}`) is a useful convention. Should we adopt it directly?
- Is their 64-dim embedding approach worth replicating for HF dataset discovery, or does HF's built-in search suffice?

---

## Key Takeaways

1. **Traces.com has nailed the DX for a CLI-first agent session sharing tool.** The adapter architecture (auto-detect + parse + normalize + upload) is the core innovation, independently validated by DataClaw's similar approach. An open-source equivalent should replicate this pipeline exactly, but with pluggable storage backends (local files, HF Hub, S3) instead of a single proprietary API.

2. **The normalized schema is a 5-type message classification with open-ended tool types.** `user_message`, `agent_text`, `agent_thinking`, `agent_context`, `tool_call` with tool subtypes like `terminal_command`, `edit`, `mcp__{server}__{tool}`. Server-side AI processing generates titles, summaries, and 64-dim embeddings. The hard work is in the 10 adapters that parse agent-specific formats.

3. **Git integration via post-commit hooks + git notes is elegant and decentralized.** This pattern works equally well with a self-hosted or federated backend. The `traces:` prefix convention in git notes is a good standard to adopt (or use `opentraces:` for our namespace).

4. **The open data gap is our strongest differentiator.** Traces.com stores everything in a proprietary backend with no bulk export for training pipelines. No outcome signals, no per-message token counts, no sub-agent hierarchy. An open-source tool that publishes to HF datasets with rich schema serves a fundamentally different (and underserved) consumer: the ML training ecosystem.

5. **The competitive landscape is fragmenting fast.** Traces.com (team collab), DEV Community (blog embeds), Agent Trace spec (code attribution), DataClaw (open training data). Each attacks a different facet of the same problem. Our positioning as the open data commons, complementary to Traces.com rather than directly competitive, is the strongest strategic position.

---

## Sources

- [Traces.com Homepage](https://traces.com/) - homepage source analysis for schema fields
- [Traces Documentation](https://traces.com/docs)
- [Traces CLI Commands](https://traces.com/docs/cli/commands)
- [Traces Supported Agents](https://traces.com/docs/cli/supported-agents)
- [Traces Sharing from CLI](https://traces.com/docs/sharing/from-cli)
- [Traces Sharing from Agent](https://traces.com/docs/sharing/from-agent)
- [Traces Sandboxed Environments](https://traces.com/docs/sharing/sandboxed-environments)
- [Traces Git Hooks](https://traces.com/docs/git-integration/hooks)
- [Traces CLI Discovery](https://traces.com/docs/git-integration/cli-discovery)
- [Traces Organizations](https://traces.com/docs/organizations)
- [Traces Members](https://traces.com/docs/organizations/members)
- [Traces Agents](https://traces.com/docs/organizations/agents)
- [Traces API Keys](https://traces.com/docs/organizations/api)
- [Traces Authentication](https://traces.com/docs/getting-started/authentication)
- [Traces Troubleshooting](https://traces.com/docs/cli/troubleshooting)
- [npm: @traces-sh/traces](https://www.npmjs.com/package/@traces-sh/traces)
- [market-dot-dev GitHub Organization](https://github.com/market-dot-dev)
- [DEV Community Agent Session Sharing](https://dev.to/devteam/share-embed-and-curate-agent-sessions-on-dev-beta-5bj6)
- [Agent Trace Specification](https://agent-trace.dev/)
- [Live trace: Codex 1065-msg session](https://traces.com/s/jn7cws0esztwg9acj80p346h3983p6qq) - page source analysis for message type counts, tool type counts, AI-generated fields, download endpoint
- [Live trace: Claude Code 4-msg session](https://traces.com/s/jn79406n9pm27x7wb7qj4468w983kzs8) - page source analysis confirming schema across agents
