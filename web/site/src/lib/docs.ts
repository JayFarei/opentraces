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

interface GitHubRelease {
  tag_name: string;
  name: string;
  published_at: string;
  body: string;
  html_url: string;
}

async function fetchReleasesContent(): Promise<string> {
  const res = await fetch(
    "https://api.github.com/repos/JayFarei/opentraces/releases",
    {
      headers: { Accept: "application/vnd.github+json" },
      next: { revalidate: 3600 },
    }
  );

  if (!res.ok) return "# Releases\n\nUnable to load releases at this time.";

  const releases: GitHubRelease[] = await res.json();

  if (!releases.length) return "# Releases\n\nNo releases published yet.";

  const sections = releases.map((r) => {
    const date = new Date(r.published_at).toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
    const body = (r.body || "No release notes.").trim();
    return `## ${r.tag_name} — ${date}\n\n${body}`;
  });

  return `# Releases\n\n${sections.join("\n\n---\n\n")}`;
}

export async function getDocContent(slug: string): Promise<string | null> {
  if (slug === "overview/releases") {
    return fetchReleasesContent();
  }

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
