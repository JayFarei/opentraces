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
for the full command reference and workflows.`;
