import fs from "fs";
import path from "path";

// Single source of truth for the agent setup prompt. Editing the markdown
// file changes both the docs ({{AGENT_PROMPT}} injection) and the homepage
// "Copy Prompt" button — no code change required. Read once at module load
// (server-only; the value is passed to client components as a prop).
const PROMPT_PATH = path.join(process.cwd(), "src/content/agent-setup-prompt.md");

export const AGENT_PROMPT = fs.readFileSync(PROMPT_PATH, "utf-8").trim();
