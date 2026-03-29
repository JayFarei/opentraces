export const AGENT_PROMPT = `I want you to set up opentraces in this project for trace collection.

Step 1 - Install:
pip install opentraces

Step 2 - Authenticate:
Run \`opentraces auth status\` to check if already logged in.
If not authenticated, ask me to run \`opentraces login\` myself,
I need to authorize in the browser.

Step 3 - Choose agent:
Ask me which coding agent I'm using. Supported agents can be
listed with \`opentraces init --help\` (the --agent option).
If I'm using you (the agent running this prompt), detect which
agent you are and suggest that. I can pick multiple.

Step 4 - Initialize and install skill:
opentraces init --agent <chosen-agent> --review-policy review --push-policy manual

This sets up automatic trace collection with manual review before
anything is shared, and installs the opentraces agent skill into
.agents/skills/opentraces/ (plus a symlink in .<agent>/skills/)
so you have the full CLI reference for future sessions.

After setup, the workflow is:
- \`opentraces web\` to inspect traces before sharing
- \`opentraces commit --all\` to bundle ready traces
- \`opentraces push\` to publish committed traces to HuggingFace`;
