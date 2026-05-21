export const AGENT_PROMPT = `Set up opentraces on this machine for agent trace collection.

Work through this as an interview: at each decision point below, ASK ME the question, show me the options, and WAIT for my answer before doing anything. If your interface has a structured way to ask multiple-choice questions, use it; otherwise just ask in chat. Do not assume defaults without confirming.

Step 1 - Install or update:
Check if \`opentraces --version\` works.
If not installed, run: pipx install opentraces
If already installed, run: opentraces setup upgrade

Step 2 - Choose tracking mode:
Ask: "How should opentraces track your projects?"
- Global (recommended): auto-enroll every project an agent touches.
- Manual: only projects where I explicitly run \`opentraces init\` are tracked.
Apply with: \`opentraces config tracking-mode global\` or \`opentraces config tracking-mode manual\`

Step 3 - Install capture hooks:
Ask which agents to connect.
- Claude Code: \`opentraces setup claude-code\`
- Codex CLI: \`opentraces setup codex-cli\`
Also run \`opentraces setup skill\` so supported agents can drive the CLI, and \`opentraces setup git\` for post-commit Trace Trails.

Step 4 - Authenticate:
Run \`opentraces auth whoami\`.
If unauthenticated, ask whether to log into HuggingFace now or skip. Auth is needed for bucket sync and dataset remotes; local capture works offline.
- Browser/device flow: \`opentraces auth login\`
- Headless/CI: \`opentraces auth login --token\` or set \`HF_TOKEN\`

Step 5 - Initialize a project when needed:
If tracking mode is global, the project auto-enrolls on first capture. To enroll explicitly:
\`opentraces init --agent claude-code --import-existing\`
\`opentraces init --agent codex-cli\`

Step 6 - Optional bucket sync:
Ask whether to configure private bucket sync. If yes:
\`opentraces setup bucket\`
\`opentraces bucket remote status\`

Step 7 - Optional security tools:
Ask: "Enable any extra security tools? All per-record tools are optional and default off."
- TruffleHog: \`opentraces setup trufflehog\`
- Privacy-filter NER: \`opentraces setup privacy-filter\`
- LLM review for dataset publication: \`opentraces setup llm-review\`
For explicit workflow sanitization, use \`opentraces security sanitize --tools regex,entropy,path_anonymizer\` or \`--use-config\`.
Then run \`opentraces doctor\` and \`opentraces security tools list\`.

Once set up, read the skill at .agents/skills/opentraces/SKILL.md for the full command reference and workflows.

Working with retained traces:
- \`opentraces status\` shows the project snapshot
- \`opentraces bucket status\` inspects the private bucket
- \`opentraces bucket rebuild --json\` refreshes derived bucket projections
- \`opentraces trace query\` searches retained traces
- \`opentraces trace map <id> --bursts\` renders deterministic edit/intent bursts
- \`opentraces trace slice <id> --template bursts\` creates workflow packets
- \`opentraces trace get <id>\` resolves a trace, unit, or ot:// resource

Trace Trails:
- \`opentraces trail blame commit <sha>\` resolves a commit to contributing traces
- \`opentraces trail blame commit <sha> <path> --lines\` scopes blame to one file
- \`opentraces trail blame pr render\` renders a PR body from branch lineage
- \`opentraces trail graph\` renders commit + trace history
- \`opentraces trail track <trace-id>\` walks a trace's lineage through Git

Context Tree:
- \`opentraces ctx tree <trace-id>\` prints what the agent saw across the trace
- \`opentraces ctx step <trace-id> <step-index>\` resolves one step's context
- \`opentraces ctx resume <context-node-id>\` creates a resume packet
- \`opentraces setup capture-otlp\` enables higher-fidelity Claude Code context capture

Dataset workflows and datasets:
- \`opentraces workflow templates\` lists row-projection templates
- \`opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1\`
- \`opentraces dataset new my-set --workflow ./workflows/my-workflow/\`
- \`opentraces dataset run my-set\` fills it with workflow-projected rows
- \`opentraces dataset review my-set --json\` lists rows
- \`opentraces dataset review approve my-set <row-id>\` approves one row
- \`opentraces dataset remote create my-set <owner>/<repo> --private\`
- \`opentraces dataset publish my-set\` publishes approved rows only`;
