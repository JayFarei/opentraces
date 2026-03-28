import { useMemo } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { cleanContent } from "../../lib/format";
import { flattenTree } from "../../lib/tree";
import type { TreeNode } from "../../types/trace";

/**
 * Context Review panel: groups all trace content by source for quick scanning.
 * Helps the reviewer identify sensitive content at a glance.
 */

type ContextSource = "user" | "project" | "external" | "llm";

interface SourceItem {
  nodeId: string;
  label: string;
  content: string;
  hasRedaction: boolean;
  hasFlag: boolean;
}

interface SourceGroup {
  source: ContextSource;
  label: string;
  color: string;
  items: SourceItem[];
}

function classifyNode(node: TreeNode): ContextSource {
  if (node.type === "user") return "user";
  if (node.type === "tool" && node.toolCall) {
    const tool = node.toolCall.tool_name;
    if (["Read", "Edit", "Write", "Glob", "Grep", "Bash"].includes(tool)) return "project";
    if (["WebSearch", "WebFetch"].includes(tool)) return "external";
  }
  if (node.type === "subagent" || node.type === "agent") return "llm";
  if (node.type === "system") return "project";
  return "llm";
}

function extractContent(node: TreeNode): string {
  // For tool calls, show the observation output (what was returned)
  if (node.toolCall && node.observation?.content) {
    return cleanContent(node.observation.content);
  }
  // For tool calls with input, show the primary arg
  if (node.toolCall) {
    const input = node.toolCall.input;
    const vals = Object.values(input).filter((v): v is string => typeof v === "string");
    return vals[0] ?? "";
  }
  // For steps, show content
  if (node.step?.content) {
    return cleanContent(node.step.content);
  }
  return "";
}

const SOURCE_CONFIG: Record<ContextSource, { label: string; color: string }> = {
  user: { label: "USER INPUT", color: "var(--blue)" },
  project: { label: "FILESYSTEM", color: "var(--green)" },
  external: { label: "EXTERNAL", color: "var(--accent)" },
  llm: { label: "LLM OUTPUT", color: "var(--purple, #A855F7)" },
};

export function ContextReview() {
  const { selectedSessionId, setSelectedNodeId } = useSelection();
  const { tree } = useTraceData(selectedSessionId);

  const groups = useMemo(() => {
    const result: Record<ContextSource, SourceGroup> = {
      user: { source: "user", ...SOURCE_CONFIG.user, items: [] },
      project: { source: "project", ...SOURCE_CONFIG.project, items: [] },
      external: { source: "external", ...SOURCE_CONFIG.external, items: [] },
      llm: { source: "llm", ...SOURCE_CONFIG.llm, items: [] },
    };

    const flat = flattenTree(tree);
    for (const node of flat) {
      const source = classifyNode(node);
      const content = extractContent(node);
      if (!content || content.length < 3) continue;

      result[source].items.push({
        nodeId: node.id,
        label: node.toolCall ? node.toolCall.tool_name : node.type,
        content: content.length > 200 ? content.slice(0, 199) + "\u2026" : content,
        hasRedaction: node.hasRedaction,
        hasFlag: node.hasFlag,
      });
    }

    return result;
  }, [tree]);

  if (tree.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a session to review context
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-3 space-y-3">
      {(["user", "project", "external", "llm"] as ContextSource[]).map((source) => {
        const group = groups[source];
        if (group.items.length === 0) return null;

        return (
          <div key={source}>
            {/* Source header */}
            <div
              className="flex items-center justify-between mb-1 pb-1 border-b"
              style={{ borderColor: group.color }}
            >
              <span
                className="text-[10px] font-[family-name:var(--font-mono)] font-bold uppercase tracking-wider"
                style={{ color: group.color }}
              >
                {group.label}
              </span>
              <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
                {String(group.items.length)} items
              </span>
            </div>

            {/* Items */}
            <div className="space-y-1">
              {group.items.map((item, i) => (
                <button
                  key={`${item.nodeId}-${String(i)}`}
                  onClick={() => setSelectedNodeId(item.nodeId)}
                  className="w-full text-left px-2 py-1 text-[10px] font-[family-name:var(--font-mono)] bg-[var(--surface)] hover:bg-[var(--surface-hover)] transition-colors duration-100 cursor-pointer border-l-2"
                  style={{
                    borderLeftColor: item.hasFlag
                      ? "var(--red)"
                      : item.hasRedaction
                        ? "var(--yellow)"
                        : "transparent",
                  }}
                >
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[var(--text-muted)] text-[9px]">{item.label}</span>
                    {item.hasFlag && (
                      <span className="text-[8px] text-[var(--red)] border border-[var(--red)] px-1">flag</span>
                    )}
                    {item.hasRedaction && (
                      <span className="text-[8px] text-[var(--yellow)] border border-[var(--yellow)] px-1">redacted</span>
                    )}
                  </div>
                  <div className="text-[var(--text-secondary)] leading-tight whitespace-pre-wrap break-all">
                    {item.content}
                  </div>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
