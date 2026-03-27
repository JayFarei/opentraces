export interface DocEntry {
  slug: string;
  title: string;
  group?: string;
}

export const DOC_NAV: DocEntry[] = [
  { slug: "", title: "Overview" },
  { slug: "getting-started", title: "Getting Started" },
  { slug: "schema", title: "Schema Overview", group: "Schema" },
  { slug: "schema/trace-record", title: "TraceRecord", group: "Schema" },
  { slug: "schema/steps", title: "Steps", group: "Schema" },
  { slug: "schema/outcome-attribution", title: "Outcome & Attribution", group: "Schema" },
  { slug: "security-tiers", title: "Security Tiers" },
  { slug: "architecture", title: "Architecture" },
  { slug: "cli-reference", title: "CLI Reference" },
  { slug: "standards", title: "Standards" },
];
