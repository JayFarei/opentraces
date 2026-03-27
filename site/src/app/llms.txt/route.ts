import fs from "fs";
import path from "path";
import { DOC_NAV } from "@/lib/doc-nav";

const CONTENT_DIR = path.join(process.cwd(), "src/content");

function readDoc(slug: string): string {
  const exactPath = path.join(CONTENT_DIR, `${slug || "index"}.md`);
  if (fs.existsSync(exactPath)) return fs.readFileSync(exactPath, "utf-8");
  const indexPath = path.join(CONTENT_DIR, slug, "index.md");
  if (fs.existsSync(indexPath)) return fs.readFileSync(indexPath, "utf-8");
  return "";
}

export async function GET() {
  const sections = DOC_NAV.map((entry) => {
    const content = readDoc(entry.slug);
    return content ? `\n---\n\n${content}` : "";
  }).join("\n");

  const llmsTxt = `# open traces

> Open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Three security tiers. Training-first schema.

## Links

- Documentation: https://opentraces.ai/docs
- GitHub: https://github.com/opentraces
- Dashboard: https://opentraces.ai/dashboard
- Schema: https://opentraces.ai/schema

## Full Documentation
${sections}
`;

  return new Response(llmsTxt, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
