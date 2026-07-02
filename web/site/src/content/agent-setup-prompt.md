Set up opentraces on this machine for agent trace collection.

Work through this as an interview: at each decision point below, ASK ME the question, show me the options, and WAIT for my answer before doing anything. If your interface has a structured way to ask multiple-choice questions, use it; otherwise just ask in chat. Do not assume defaults without confirming.

Step 1 - Install or update:
Check if `opentraces --version` works.
If not installed, run: pipx install opentraces
If already installed, run: opentraces upgrade

Step 2 - Choose tracking mode:
Ask: "How should opentraces track your projects?"
- Global (recommended): auto-enroll Claude/Codex projects when capture fires.
- Manual: only projects where I explicitly run `opentraces init` are tracked.
Pi is extension-backed and still requires explicit per-project `opentraces init --agent pi` consent before sidecars are written.
Apply with: `opentraces config tracking-mode global` or `opentraces config tracking-mode manual`

Step 3 - Install capture hooks and the shared skill:
Ask which agents to connect for capture hooks.
- Claude Code: `opentraces setup claude-code`
- Codex CLI: `opentraces setup codex-cli`
- Pi: first `pi install npm:opentraces-pi`, then `opentraces setup pi --dry-run --json` or use `/ot-setup` inside Pi.
Then ask: "Install the shared opentraces skill so supported agents can drive the CLI?"
- Yes, recommended: run `opentraces setup skill`. Use `--harness claude-code`, `--harness codex-cli`, or `--harness pi` only if I want to limit the skill to one harness.
- No: skip the skill install; capture hooks can still run, but agents may not see the opentraces command reference automatically.
Also run `opentraces setup git` for post-commit Trace Trails.
Before installing a selected agent hook, check that agent's own CLI is installed and authenticated enough to start a session:
- Claude Code: `command -v claude`; if missing or logged out, tell me to run `claude login` outside this session.
- Codex CLI: `command -v codex`; if missing or logged out, tell me to run `codex login` outside this session. This does not cover Codex Desktop.
- Pi: `command -v pi`; if missing or logged out, tell me to run `pi /login` outside this session before using Pi capture.
Codex hooks are observational; they must not approve or deny permission prompts. Pi setup manages package resources only; it does not silently install Python, start services, authenticate, or enable capture.
After installing hooks or the shared skill, tell me to start a new Claude Code, Codex CLI, or Pi session before expecting capture hooks or the opentraces skill to be available in that agent. Do not ask for a machine reboot; a fresh agent session is enough.

Step 4 - Authenticate:
Run `opentraces --json auth whoami` and inspect the JSON.
If already authenticated, continue.
If unauthenticated, ask whether to connect HuggingFace now or skip. Auth is needed for bucket sync and dataset remotes; local capture works offline.
- Browser/device flow: only run `opentraces auth login --device-timeout 180` if I can open the shown URL and enter the code while you wait. When the URL and code appear, clearly tell me: "Open the URL, enter the code, then come back here; this command is waiting." If it times out, stop and show the outside-session steps below.
- Outside this session: tell me to run `opentraces auth login` in a normal terminal, or `opentraces auth login --token` for a personal token, or export `HF_TOKEN=hf_...`; then rerun `opentraces --json auth whoami`.
- Skip for now: continue local-only and do not run `opentraces bucket connect --remote`.
Do not ask me to paste an HF token into agent chat.

Step 5 - Initialize a project when needed:
If tracking mode is global, the project auto-enrolls on first capture. To enroll explicitly:
`opentraces init --agent claude-code --import-existing`
`opentraces init --agent codex-cli`
`opentraces init --agent pi`
Codex and Pi capture start with future sessions after setup and init. `--import-existing` is a Claude Code backfill path, not a Codex/Pi backfill path.

Step 6 - Optional bucket sync:
Ask whether to configure private bucket sync. If yes:
`opentraces bucket connect`
`opentraces bucket sync status`
Only run this after `opentraces --json auth whoami` reports authenticated. If auth is still missing, clearly say: "Bucket sync needs HuggingFace auth; run `opentraces auth login` outside this session, then rerun setup."

Step 7 - Optional security tools:
Ask: "Enable any extra security tools? All per-record tools are optional and default off."
- TruffleHog: `opentraces setup trufflehog`
- Privacy-filter NER: `opentraces setup privacy-filter`
- LLM-assisted row review before publishing: use `opentraces dataset review` and `opentraces dataset publish` (the canonical row-review/publication surface; `opentraces setup llm-review` still works but is no longer the primary path).
For explicit workflow sanitization, use `opentraces security sanitize --tools regex,entropy,path_anonymizer` or `--use-config`.
Then run `opentraces doctor` and `opentraces security tools list`.

Once set up, read the skill at `~/.agents/skills/opentraces/SKILL.md` (or `.agents/skills/opentraces/SKILL.md` inside an initialized project) for the full command reference and workflows.

Working with retained traces:
- `opentraces status` is the fleet bucket safety dashboard (scanned/unscanned counts, a "safe to sync" verdict)
- `opentraces bucket list` is the bounded, paginated per-trace inventory
- `opentraces bucket repair --json` refreshes derived bucket projections
- `opentraces trace query` searches retained traces
- `opentraces trace query --skill <name> --json` finds traces that invoked a given skill
- `opentraces trace map <id> --bursts` renders deterministic edit/intent bursts
- `opentraces trace slice <id> --template bursts` creates workflow packets
- `opentraces trace get <id>` resolves a trace, unit, or ot:// resource; the same `<trace-id>[:<step>|:last|:A-B]` address also root-dispatches as `opentraces <trace-id>[:step|:last|:A-B]`

Inside Pi, the package exposes slash commands `/ot-capture-status`, `/ot-setup`, `/ot-search <query>`, `/ot-trace <trace-id>`, `/ot-standup`, `/ot-capsule [trace-id]`, and `/ot-dataset`. The model-facing tools are `ot_capture_status`, `ot_search`, `ot_trace`, `ot_standup`, `ot_capsule`, and `ot_dataset`; use `/ot-search` or `ot_search` first, then `/ot-trace`/`ot_trace` for the selected bucket trace.

Trace Trails:
- `opentraces trail blame commit <sha>` resolves a commit to contributing traces
- `opentraces trail blame commit <sha> <path> --lines` scopes blame to one file
- `opentraces trail pr render` renders a PR body from branch lineage
- `opentraces trail graph` renders commit + trace history
- `opentraces trail track <trace-id>` walks a trace's lineage through Git

Context Tree:
- `opentraces ctx <trace-id>` prints the context overview for the trace (shape + capture method)
- `opentraces ctx <trace-id>:<step-index>` resolves the model input at one step (`--layer system|messages|tools|runtime` to render one layer; `:last` for the final step)
- `opentraces ctx resume <context-node-id>` creates a resume packet
- `opentraces setup capture-otlp` enables higher-fidelity Claude Code context capture
Codex Context Tree capture uses transcript reconstruction and does not decrypt encrypted reasoning. Pi provider/context sidecars use `capture_method=live_capture` when available; raw provider bodies remain default-off/local unless explicitly opted in. Snapshot-backed `--at-step` resume is Claude Code only; Pi native resume uses `pi --session <session-id>` through `opentraces trace get <trace-id> --resume`.

Dataset workflows and datasets:
- `opentraces workflow templates` lists row-projection templates
- `opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1`
- `opentraces dataset new my-set --workflow ./workflows/my-workflow/`
- `opentraces dataset run my-set` fills it with workflow-projected rows
- `opentraces dataset new my-skill-set --from-skill opentraces`
- `opentraces dataset run my-skill-set --executor script --json`
- `opentraces dataset review my-set --json` lists rows
- `opentraces dataset review approve my-set <row-id>` approves one row
- `opentraces dataset remote create my-set <owner>/<repo> --private`
- `opentraces dataset publish my-set` publishes approved rows only
