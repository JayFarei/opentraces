export interface DocEntry {
  slug: string;
  title: string;
  group?: string;
}

export const DOC_NAV: DocEntry[] = [
  { slug: "", title: "Overview" },
  { slug: "overview/releases", title: "Releases", group: "Overview" },

  // Getting Started
  { slug: "getting-started/installation", title: "Installation", group: "Getting Started" },
  { slug: "getting-started/authentication", title: "Authentication", group: "Getting Started" },
  { slug: "getting-started/quickstart", title: "Quick Start", group: "Getting Started" },

  // CLI
  { slug: "cli/commands", title: "Commands", group: "CLI" },
  { slug: "cli/supported-agents", title: "Supported Agents", group: "CLI" },
  { slug: "cli/troubleshooting", title: "Troubleshooting", group: "CLI" },

  // Security
  { slug: "security/tiers", title: "Security Modes", group: "Security" },
  { slug: "security/scanning", title: "Scanning & Redaction", group: "Security" },
  { slug: "security/configuration", title: "Configuration", group: "Security" },

  // Schema
  { slug: "schema/overview", title: "Overview", group: "Schema" },
  { slug: "schema/trace-record", title: "TraceRecord", group: "Schema" },
  { slug: "schema/steps", title: "Steps", group: "Schema" },
  { slug: "schema/outcome-attribution", title: "Outcome & Attribution", group: "Schema" },
  { slug: "schema/standards", title: "Standards Alignment", group: "Schema" },
  { slug: "schema/versioning", title: "Versioning", group: "Schema" },

  // Workflow
  { slug: "workflow/parsing", title: "Parse", group: "Workflow" },
  { slug: "workflow/review", title: "Review", group: "Workflow" },
  { slug: "workflow/quality", title: "Assess", group: "Workflow" },
  { slug: "workflow/pushing", title: "Push", group: "Workflow" },
  { slug: "workflow/consume", title: "Consume", group: "Workflow" },

  // Integration
  { slug: "integration/ci-cd", title: "CI/CD & Automation", group: "Integration" },
  { slug: "integration/agent-setup", title: "Agent Setup", group: "Integration" },

  // Contributing
  { slug: "contributing/schema-changes", title: "Schema Changes", group: "Contributing" },
  { slug: "contributing/development", title: "Development", group: "Contributing" },
];
