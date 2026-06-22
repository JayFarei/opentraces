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

  const githubLink = "[View all releases on GitHub →](https://github.com/JayFarei/opentraces/releases)";

  if (!releases.length) return `# Releases\n\n${githubLink}\n\nNo releases published yet.`;

  const sections = releases.map((r) => {
    const date = new Date(r.published_at).toLocaleDateString("en-US", {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
    // Some historical release bodies carry backslash-escaped backticks
    // (\`) because the skill template over-escaped fenced code blocks.
    // Unescape them here so react-markdown sees real fences.
    const body = (r.body || "No release notes.").trim().replace(/\\`/g, "`");
    return `## ${r.tag_name} — ${date}\n\n${body}`;
  });

  return `# Releases\n\n${githubLink}\n\n${sections.join("\n\n---\n\n")}`;
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

const SITE = "https://opentraces.ai";

/** First prose paragraph of a doc, cleaned into a meta description (≤155 chars). */
function extractDescription(markdown: string): string | null {
  const noCode = markdown.replace(/```[\s\S]*?```/g, "");
  const buf: string[] = [];
  for (const raw of noCode.split("\n")) {
    const line = raw.trim();
    if (!line) {
      if (buf.length) break; // blank line ends the first paragraph
      continue;
    }
    if (line.startsWith("#")) continue; // headings
    if (line.startsWith(">")) continue; // blockquotes
    if (/^([-*+]|\d+\.)\s/.test(line)) continue; // list items
    buf.push(line);
  }
  if (!buf.length) return null;
  let text = buf
    .join(" ")
    .replace(/\s+/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links → text
    .replace(/[*_`]+/g, "") // emphasis/code marks
    .trim();
  if (text.length > 155) text = text.slice(0, 152).replace(/\s+\S*$/, "") + "…";
  return text || null;
}

export interface DocMeta {
  title: string;
  heading: string;
  description: string;
  canonical: string;
}

/**
 * Page-specific metadata for a docs slug. Reads the markdown directly (no
 * network) so it is cheap during static generation; the releases page, which
 * is fetched at runtime, uses a generic description.
 */
export function getDocMeta(slug: string): DocMeta | null {
  const navTitle = DOC_NAV.find((d) => d.slug === slug)?.title;

  let content = "";
  if (slug !== "overview/releases") {
    const exactPath = path.join(CONTENT_DIR, `${slug || "index"}.md`);
    const indexPath = path.join(CONTENT_DIR, slug, "index.md");
    if (fs.existsSync(exactPath)) content = fs.readFileSync(exactPath, "utf-8");
    else if (fs.existsSync(indexPath)) content = fs.readFileSync(indexPath, "utf-8");
    else return null;
  }

  const heading = slug === "" ? "opentraces Documentation" : navTitle ?? "Documentation";
  const title = slug === "" ? "Documentation — opentraces" : `${heading} — opentraces docs`;
  const description =
    extractDescription(content) ??
    `${navTitle ?? "opentraces"} documentation — the open-source CLI for capturing AI coding agent traces and publishing them as datasets on Hugging Face Hub.`;

  return { title, heading, description, canonical: `${SITE}/docs${slug ? "/" + slug : ""}` };
}
