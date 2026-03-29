export const AGENT_PROMPT = `Set up opentraces in this project for trace collection.

Step 1 - Install:
pipx install opentraces

Step 2 - Authenticate:
Run \`opentraces auth status\` to check if already logged in.
If not authenticated, ask me to run \`opentraces login\` myself,
I need to authorize in the browser.

Step 3 - Initialize:
Detect which agent you are (Claude Code, Cursor, etc.) and run:
\`opentraces init --agent <agent> --review-policy auto --import-existing\`

This will:
- set the review policy to auto (traces are captured, sanitized, committed, and pushed automatically)
- create a private dataset on HuggingFace
- install the agent hook so traces are captured at the end of every session
- import any existing sessions from this project

If I want manual review instead, use \`--review-policy review\`.

Step 4 - Review (if review policy):
Open the inbox to review, commit, redact, or reject traces:
\`opentraces tui\` or \`opentraces web\`

Step 5 - Push:
Sync committed traces to the remote dataset:
\`opentraces push\`

With auto review policy, the hook handles this automatically
at the end of each session, no manual push needed.

After setup, the agent can also:
- \`opentraces tui\` to open the inbox and review pending traces
- verify that no information about a specific client or topic leaks
- \`opentraces push\` to sync committed traces to HuggingFace`;
