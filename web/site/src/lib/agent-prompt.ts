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
\`opentraces init --agent claude-code --review-policy review --import-existing\`

This creates a private HuggingFace dataset, installs the session capture
hook, and installs the opentraces skill into this project.

Once initialized, read the skill at .agents/skills/opentraces/SKILL.md
for the full command reference and workflows.

Optional hardening:
Run \`opentraces doctor\` to verify environment and security pipeline.
For extra coverage, \`opentraces setup trufflehog\` enables TruffleHog
scanning (800+ detectors, opt-in, requires the binary), and
\`opentraces llm-review\` runs a local LLM session-level privacy pass.
To gate uploads on that review, push with \`opentraces push --llm-review\`.

Optional commit anchoring (schema 0.3.0+):
Run \`opentraces setup git\` to install a post-commit hook that links
each commit to the trace(s) that produced it, tagging the link with an
evidence tier (tool_emitted, tool_emitted_with_divergence, overlapping,
or orphan). Once installed:
- \`opentraces blame <sha>\` resolves a commit to its contributing traces
- \`opentraces blame <sha> <path>\` scopes blame to one file; add \`--lines\`
  for per-line git-blame-style output
- \`opentraces graph\` renders the commit + trace history
- \`opentraces list --by-commit\` groups traces by commit
- \`opentraces show <trace-id> --markdown\` renders a prompt-injection-safe view
- \`opentraces export --format agent-trace\` exports to the Agent Trace spec`;
