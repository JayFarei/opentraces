export const AGENT_PROMPT = `Set up opentraces in this project for trace collection.

Step 1 - Install or update:
Check if \`opentraces --version\` works.
If not installed, run: pipx install opentraces
If already installed, run: opentraces upgrade

Step 2 - Authenticate:
Run \`opentraces auth status\` to check if already logged in.
If not authenticated, ask the user to run \`opentraces login\` themselves
to authorize in the browser (this pushes to existing datasets).
If they want to create new datasets directly from opentraces,
they need a write-scope token: \`opentraces login --token\` with a
token from https://huggingface.co/settings/tokens.

Step 3 - Initialize:
Detect which agent you are (claude-code, cursor, etc.) and run:
\`opentraces init --agent <agent> --review-policy review --import-existing\`

This creates a private HuggingFace dataset, installs the session capture
hook, and installs the opentraces skill into this project.

Once initialized, read the skill at .agents/skills/opentraces/SKILL.md
for the full command reference and workflows.

Optional hardening:
Run \`opentraces doctor\` to verify environment and security pipeline.
For extra coverage, \`opentraces setup trufflehog\` enables TruffleHog
scanning (800+ detectors, opt-in, requires the binary), and
\`opentraces review-llm\` runs a local LLM session-level privacy pass.
To gate uploads on that review, push with \`opentraces push --llm-review\`.

Optional commit anchoring (schema 0.3.0+):
Run \`opentraces setup git\` to install a post-commit hook that links
each commit to the trace(s) that produced it, tagging the link with an
evidence tier (tool_emitted, tool_emitted_with_divergence, overlapping,
or orphan). Once installed:
- \`opentraces notes <ref>\` prints the notes attached to a commit
- \`opentraces blame <file>:<line>\` resolves a line to its originating trace
- \`opentraces session list --by-commit\` groups traces by commit
- \`opentraces session show --markdown\` renders a prompt-injection-safe view
- \`opentraces export --format agent-trace\` exports to the Agent Trace spec`;
