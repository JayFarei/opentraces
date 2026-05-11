export const AGENT_PROMPT = `Set up opentraces in this project for trace collection.

Step 1 - Install or update:
Check if \`opentraces --version\` works.
If not installed, run: pipx install opentraces
If already installed, run: opentraces setup upgrade

Step 2 - Authenticate:
Run \`opentraces auth whoami\` to check if already logged in.
If not authenticated, ask the user to run \`opentraces auth login\` themselves
to authorize in the browser.
If they are headless or on CI, use \`opentraces auth login --token\` with a
token from https://huggingface.co/settings/tokens, or set \`HF_TOKEN\`.

Step 3 - Initialize:
Run:
\`opentraces init --agent claude-code --import-existing\`

This enrolls the project and installs the Claude Code capture hooks.
Then install the global skill and per-project hooks:
\`opentraces setup skill\` (installs the opentraces skill into your agents)
\`opentraces setup git\` (post-commit hook for trace blame, recommended)

Once initialized, read the skill at .agents/skills/opentraces/SKILL.md
for the full command reference and workflows.

Optional hardening:
Run \`opentraces doctor\` to verify environment and security pipeline.
For extra coverage, \`opentraces setup trufflehog\` enables TruffleHog
scanning (800+ detectors, opt-in, requires the binary), and
\`opentraces setup llm-review\` configures a local LLM session-level
privacy pass that runs as part of \`dataset publish\`.

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
