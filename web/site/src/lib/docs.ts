import fs from "fs";
import path from "path";
import { DOC_NAV } from "./doc-nav";
import { AGENT_PROMPT } from "./agent-prompt";

const CONTENT_DIR = path.join(process.cwd(), "docs/docs");

export { DOC_NAV };

/** Replace template placeholders in doc content. */
function applyTemplates(content: string): string {
  return content.replace(/\{\{AGENT_PROMPT\}\}/g, AGENT_PROMPT);
}

export function getDocContent(slug: string): string | null {
  // Try exact match: docs/docs/getting-started/installation.md
  const exactPath = path.join(CONTENT_DIR, `${slug || "index"}.md`);
  if (fs.existsSync(exactPath)) {
    return applyTemplates(fs.readFileSync(exactPath, "utf-8"));
  }

  // Try index file: docs/docs/getting-started/index.md
  const indexPath = path.join(CONTENT_DIR, slug, "index.md");
  if (fs.existsSync(indexPath)) {
    return applyTemplates(fs.readFileSync(indexPath, "utf-8"));
  }

  return null;
}

export function getDocTitle(slug: string): string {
  const entry = DOC_NAV.find((d) => d.slug === slug);
  return entry?.title || "Documentation";
}

export function getSkillContent(): string {
  const skillPath = path.join(process.cwd(), "../../skill/SKILL.md");
  if (fs.existsSync(skillPath)) {
    return fs.readFileSync(skillPath, "utf-8");
  }
  return "";
}
