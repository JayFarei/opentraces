export interface DocEntry {
  slug: string;
  title: string;
  group?: string;
}

export const DOC_NAV: DocEntry[] = [
  { slug: "", title: "Overview" },

  // Getting Started
  { slug: "getting-started/installation", title: "Installation", group: "Getting Started" },
  { slug: "getting-started/authentication", title: "Authentication", group: "Getting Started" },
  { slug: "getting-started/quickstart", title: "Quick Start", group: "Getting Started" },

  // CLI
  { slug: "cli/commands", title: "Commands", group: "CLI" },
  { slug: "cli/supported-agents", title: "Supported Agents", group: "CLI" },
  { slug: "cli/troubleshooting", title: "Troubleshooting", group: "CLI" },

  // Security
  { slug: "security/tiers", title: "Security Tools", group: "Security" },
  { slug: "security/scanning", title: "Scanning & Redaction", group: "Security" },
  { slug: "security/configuration", title: "Configuration", group: "Security" },

  // Schema
  { slug: "schema/overview", title: "Overview", group: "Schema" },
  { slug: "schema/trace-record", title: "TraceRecord", group: "Schema" },
  { slug: "schema/steps", title: "Steps", group: "Schema" },
  { slug: "schema/outcome-attribution", title: "Outcome & Attribution", group: "Schema" },
  { slug: "schema/standards", title: "Standards Alignment", group: "Schema" },
  { slug: "schema/versioning", title: "Versioning", group: "Schema" },

  // Trace Workflow
  { slug: "workflow/parsing", title: "Capture", group: "Trace Workflow" },
  { slug: "workflow/bucket", title: "Portable Bucket", group: "Trace Workflow" },
  { slug: "workflow/trace-discovery", title: "Trace Discovery", group: "Trace Workflow" },
  { slug: "workflow/blame", title: "Trace Trails", group: "Trace Workflow" },
  { slug: "workflow/context-tree", title: "Context Tree", group: "Trace Workflow" },

  // Dataset Workflows
  { slug: "workflow/workflow-templates", title: "Dataset Workflows", group: "Dataset Workflows" },

  // Datasets
  { slug: "workflow/datasets", title: "Dataset Rows", group: "Datasets" },
  { slug: "workflow/review", title: "Review", group: "Datasets" },
  { slug: "workflow/pushing", title: "Publish", group: "Datasets" },

  // Clients
  { slug: "workflow/consume", title: "Clients & Use Cases", group: "Clients" },

  // Integration
  { slug: "integration/ci-cd", title: "CI/CD & Automation", group: "Integration" },
  { slug: "integration/agent-setup", title: "Agent Setup", group: "Integration" },
  { slug: "integration/capture-integration", title: "Capture Integration", group: "Integration" },
  { slug: "integration/post-processor-contract", title: "Post-Processor Contract", group: "Integration" },

  // Contributing
  { slug: "contributing/schema-changes", title: "Schema Changes", group: "Contributing" },
  { slug: "contributing/development", title: "Development", group: "Contributing" },
];
