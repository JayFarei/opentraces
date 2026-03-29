import fs from "fs";
import path from "path";
import { DOC_NAV } from "./doc-nav";

const CONTENT_DIR = path.join(process.cwd(), "docs/docs");

export { DOC_NAV };

export function getDocContent(slug: string): string | null {
  // Try exact match: docs/docs/getting-started/installation.md
  const exactPath = path.join(CONTENT_DIR, `${slug || "index"}.md`);
  if (fs.existsSync(exactPath)) {
    return fs.readFileSync(exactPath, "utf-8");
  }

  // Try index file: docs/docs/getting-started/index.md
  const indexPath = path.join(CONTENT_DIR, slug, "index.md");
  if (fs.existsSync(indexPath)) {
    return fs.readFileSync(indexPath, "utf-8");
  }

  return null;
}

export function getDocTitle(slug: string): string {
  const entry = DOC_NAV.find((d) => d.slug === slug);
  return entry?.title || "Documentation";
}
