# opentraces - Share Agent Traces

When the user says "share this session to opentraces", "publish traces",
"upload my traces", "share traces", or similar:

## Workflow

1. **If the repo is not initialized, gather setup choices in chat first.**
   Do not default to interactive `opentraces init` prompts when you can ask the
   user directly in the coding agent.

   Ask for:
   - review policy: `review` or `auto-ready`
   - push policy: `manual` or `auto-push`
   - whether to configure a Hugging Face remote now
   - if existing Claude Code sessions already exist for this repo, whether to import that backlog now or start fresh

   Then run an explicit init command, for example:
   ```bash
   opentraces init --agent claude-code --review-policy review --push-policy manual --start-fresh
   ```

   If the user chose a remote during setup, include:
   ```bash
   opentraces init --agent claude-code --review-policy review --push-policy manual --import-existing --remote your-name/opentraces
   ```

   Use `--import-existing` when the user wants past Claude Code sessions for this repo
   pulled into the inbox now. Use `--start-fresh` when they only want capture from the
   next session onward.

2. **Check project state** (what's in the inbox, auth status, next action):
   ```bash
   opentraces context
   ```

3. **Review sessions** via CLI:
   ```bash
   opentraces session list --stage inbox
   opentraces session show <TRACE_ID>
   opentraces session approve <TRACE_ID>
   ```

4. **Commit** approved traces:
   ```bash
   opentraces commit --all
   ```

5. **Push** committed traces to HuggingFace Hub:
   ```bash
   opentraces push
   ```

## Session Commands

Full CRUD for trace review, no TUI or web UI needed:

```bash
opentraces session list [--stage inbox|ready|committed|pushed|rejected] [--model MODEL] [--agent AGENT] [--limit N]
opentraces session show <TRACE_ID>
opentraces session approve <TRACE_ID>
opentraces session reject <TRACE_ID>
opentraces session reset <TRACE_ID>          # undo approve/reject, back to inbox
opentraces session redact <TRACE_ID> --step N  # redact a specific step
opentraces session discard <TRACE_ID> --yes    # permanently delete
```

## Agent-Native Workflow

For fully automated operation, use `--json` to get machine-readable output:

```bash
opentraces --json context                    # project state as JSON
opentraces --json session list --stage inbox  # list inbox sessions
opentraces --json session show <ID>          # full trace detail
opentraces --json session approve <ID>       # approve, get next_command
opentraces --json commit --all               # bundle for push
opentraces --json push                       # upload to HF Hub
```

Every command emits structured JSON after a `---OPENTRACES_JSON---` sentinel
with `status`, `next_steps`, and `next_command` fields.

For agent-led onboarding, prefer explicit flags over interactive prompts.
The coding agent should discover setup preferences in conversation, then run
the exact `opentraces init ...` command matching those choices.

## Prerequisites

- opentraces CLI installed: `pip install opentraces`
- HF Hub authenticated: `opentraces auth login` or set `HF_TOKEN` environment variable

## Quick Share

For projects where safe sessions can be moved to `Ready` automatically:

```bash
opentraces init --review-policy auto-ready
```

Then commit and push: `opentraces commit --all && opentraces push`.

## Recommended Onboarding Shape

- Human in terminal: plain `opentraces init` is fine, interactive prompts are expected.
- Agent in code chat: ask the setup questions explicitly, then run a fully specified `opentraces init --agent ... --review-policy ... --push-policy ... <--import-existing|--start-fresh> [--remote ...]`.
- If backlog exists for this repo: do not leave the choice implicit, pass either `--import-existing` or `--start-fresh`.

## Other Commands

- `opentraces stats` - aggregate statistics (traces, tokens, cost, models)
- `opentraces status` - tree view of inbox
- `opentraces config show/set` - configuration management
- `opentraces remote list/use/remove` - manage HF dataset remote

## Security Tiers

- **Tier 1**: Minimal filtering, for open-source projects
- **Tier 2**: Moderate filtering, redacts common secrets
- **Tier 3** (default): Strict filtering, redacts secrets, PII, and sensitive paths

## Troubleshooting

- If `opentraces push` fails with auth errors, run `opentraces auth login` to refresh your token
- If no sessions are found, check that you have Claude Code session files in `~/.claude/projects/`
- Use `opentraces context` or `opentraces status` to inspect the current repo inbox
