export const AGENT_PROMPT = `Set up opentraces on this machine for agent trace collection.

Work through this as an interview: at each decision point below, ASK ME the question, show me the options, and WAIT for my answer before doing anything. If your interface has a structured way to ask multiple-choice questions, use it; otherwise just ask in chat. Do not assume defaults without confirming.

Step 1 - Install or update:
Check if \`opentraces --version\` works.
If not installed, run: pipx install opentraces
If already installed, run: opentraces setup upgrade

Step 2 - Choose tracking mode (ask me, then apply):
Ask: "How should opentraces track your projects?"
- Global (recommended): auto-enroll every project an agent touches, no per-project setup. New projects start capturing the first time you use an agent in them. Each is enrolled private + review-required.
- Manual: only projects where you explicitly run \`opentraces init\` are tracked.
Apply my answer with: \`opentraces config tracking-mode global\` or \`opentraces config tracking-mode manual\`

Step 3 - Install capture hooks:
Run \`opentraces setup skill\` to install the opentraces skill into your agents, and install the Claude Code capture hooks into your global settings (these fire across all projects, which is what powers global tracking).
Also run \`opentraces setup git\` for the post-commit hook that powers trace blame (recommended).

Step 4 - Authenticate (ask me, then apply):
Run \`opentraces auth whoami\` to check if I am already logged in.
If not, ask: "Log into HuggingFace now, or skip for later?" HuggingFace auth is only needed to publish datasets to a remote; capture and review work fully offline without it.
- If I want to log in: ask me to run \`opentraces auth login\` myself to authorize in the browser. On a headless machine or CI, use \`opentraces auth login --token\` with a token from https://huggingface.co/settings/tokens, or set \`HF_TOKEN\`.
- If I skip: continue, and remind me I can run \`opentraces setup auth\` later.

Step 5 - Initialize a project (only if I chose Manual in Step 2):
If tracking mode is global, skip this - the project you are in is auto-enrolled on first use. To enroll the current project explicitly anyway, run \`opentraces init --agent claude-code --import-existing\`.
If tracking mode is manual, run \`opentraces init --agent claude-code --import-existing\` here to enroll this project and import existing sessions.

Step 6 - Optional hardening (ask me, then apply):
Ask: "Enable any extra security passes? (all optional, off by default)"
- TruffleHog secret scanning (800+ detectors, redacts findings and forces review): \`opentraces setup trufflehog\`
- Local LLM session-level privacy review, runs during \`dataset publish\`: \`opentraces setup llm-review\`
Apply only the ones I pick. Then run \`opentraces doctor\` to verify the environment and security pipeline.

Once set up, read the skill at .agents/skills/opentraces/SKILL.md for the full command reference and workflows.

Working with traces and datasets:
- \`opentraces status\` shows inbox counts and recent traces
- \`opentraces trace query\` searches local retained traces
- \`opentraces trace get <id>\` resolves a trace, unit, or ot:// resource
- \`opentraces trace map <id>\` renders a deterministic Trace Map

Trace Trails (commit lineage):
- \`opentraces trail blame <sha>\` resolves a commit to its contributing traces
- \`opentraces trail blame <sha> <path>\` scopes blame to one file; add \`--lines\`
  for per-line git-blame-style output
- \`opentraces trail graph\` renders the commit + trace history
- \`opentraces trail track <trace-id>\` walks a trace's lineage through Git

Datasets (HuggingFace publication):
- \`opentraces dataset new my-set\` scaffolds a local executable dataset
- \`opentraces dataset run my-set\` fills it with rows from your retained traces
- \`opentraces dataset review my-set --web\` opens the browser reviewer
- \`opentraces dataset remote create my-set <owner>/<repo>\` binds a private
  HuggingFace remote (use \`remote add\` for an existing dataset)
- \`opentraces dataset publish my-set\` publishes approved rows to the remote
- \`opentraces dataset remote visibility my-set --public\` flips a dataset
  from private to public when you are ready to share`;
