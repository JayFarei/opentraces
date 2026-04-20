"use client";

import { useEffect } from "react";
import { DOC_NAV } from "@/lib/doc-nav";

interface WebMcpTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  execute: (args: Record<string, unknown>) => unknown;
}

interface ModelContextAPI {
  provideContext?: (context: { tools: WebMcpTool[] }) => unknown;
}

declare global {
  interface Navigator {
    modelContext?: ModelContextAPI;
  }
}

const INSTALL_COMMANDS: Record<string, string> = {
  pipx: "pipx install opentraces",
  brew: "brew install JayFarei/opentraces/opentraces",
  pip: "pip install opentraces",
};

export default function WebMcpTools() {
  useEffect(() => {
    const api = navigator.modelContext;
    if (!api?.provideContext) return;

    const tools: WebMcpTool[] = [
      {
        name: "searchDocs",
        description:
          "Search the opentraces documentation by keyword. Returns matching page titles, groups, and URLs.",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Keywords matched against doc titles and slugs.",
            },
          },
          required: ["query"],
        },
        execute: ({ query }) => {
          const q = String(query ?? "").trim().toLowerCase();
          if (!q) return { count: 0, results: [] };
          const matches = DOC_NAV.filter(
            (d) =>
              d.title.toLowerCase().includes(q) ||
              d.slug.toLowerCase().includes(q) ||
              (d.group?.toLowerCase().includes(q) ?? false),
          ).map((d) => ({
            title: d.title,
            group: d.group ?? null,
            url: `https://opentraces.ai/docs${d.slug ? "/" + d.slug : ""}`,
          }));
          return { count: matches.length, results: matches.slice(0, 20) };
        },
      },
      {
        name: "getInstallCommand",
        description:
          "Return the recommended shell command to install the opentraces CLI. Optionally specify a package manager.",
        inputSchema: {
          type: "object",
          properties: {
            manager: {
              type: "string",
              enum: ["pipx", "brew", "pip"],
              description: "Preferred package manager. Defaults to pipx.",
            },
          },
        },
        execute: ({ manager }) => {
          const key = typeof manager === "string" ? manager : "";
          if (key && INSTALL_COMMANDS[key]) {
            return { manager: key, command: INSTALL_COMMANDS[key] };
          }
          return {
            recommended: "pipx",
            command: INSTALL_COMMANDS.pipx,
            alternatives: INSTALL_COMMANDS,
          };
        },
      },
      {
        name: "openExplorer",
        description:
          "Navigate to the opentraces dataset explorer. Optionally pre-fill a search term via the URL fragment.",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Optional search term to pre-fill in the explorer.",
            },
          },
        },
        execute: ({ query }) => {
          const q = typeof query === "string" ? query.trim() : "";
          const url = q
            ? `/explorer#q=${encodeURIComponent(q)}`
            : "/explorer";
          window.location.assign(url);
          return { url };
        },
      },
    ];

    try {
      Promise.resolve(api.provideContext({ tools })).catch(() => {});
    } catch {
      // WebMCP unsupported in this browser; fail silently
    }
  }, []);

  return null;
}
