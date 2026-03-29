import { useMemo, useState } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { cleanContent } from "../../lib/format";
import { flattenTree } from "../../lib/tree";
import type { TreeNode } from "../../types/trace";

/**
 * Context Review: groups all trace content by source for quick scanning.
 * In fullView mode, takes over the main panel with collapsible categories.
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
  if (node.toolCall && node.observation?.content) {
    return cleanContent(node.observation.content);
  }
  if (node.toolCall) {
    const input = node.toolCall.input;
    const vals = Object.values(input).filter((v): v is string => typeof v === "string");
    return vals[0] ?? "";
  }
  if (node.step?.content) {
    return cleanContent(node.step.content);
  }
  return "";
}

const SOURCE_CONFIG: Record<ContextSource, { label: string; color: string; description: string }> = {
  user: { label: "USER INPUT", color: "var(--blue)", description: "Prompts and messages from the user" },
  project: { label: "FILESYSTEM", color: "var(--green)", description: "File reads, edits, bash commands, and grep results" },
  external: { label: "EXTERNAL", color: "var(--accent)", description: "Web searches, fetches, and external API calls" },
  llm: { label: "LLM OUTPUT", color: "var(--purple, #A855F7)", description: "Agent reasoning, subagent calls, and model responses" },
};

const SOURCES: ContextSource[] = ["user", "project", "external", "llm"];

interface ContextReviewProps {
  fullView?: boolean;
}

export function ContextReview({ fullView = false }: ContextReviewProps) {
  const { selectedSessionId, setSelectedNodeId } = useSelection();
  const { tree } = useTraceData(selectedSessionId);
  const [expandedSources, setExpandedSources] = useState<Record<ContextSource, boolean>>({
    user: true,
    project: false,
    external: false,
    llm: false,
  });

  const groups = useMemo(() => {
    const result: Record<ContextSource, SourceGroup> = {
      user: { source: "user", label: SOURCE_CONFIG.user.label, color: SOURCE_CONFIG.user.color, items: [] },
      project: { source: "project", label: SOURCE_CONFIG.project.label, color: SOURCE_CONFIG.project.color, items: [] },
      external: { source: "external", label: SOURCE_CONFIG.external.label, color: SOURCE_CONFIG.external.color, items: [] },
      llm: { source: "llm", label: SOURCE_CONFIG.llm.label, color: SOURCE_CONFIG.llm.color, items: [] },
    };

    const flat = flattenTree(tree);
    for (const node of flat) {
      const source = classifyNode(node);
      const content = extractContent(node);
      if (!content || content.length < 3) continue;

      const maxLen = fullView ? 500 : 200;
      result[source].items.push({
        nodeId: node.id,
        label: node.toolCall ? node.toolCall.tool_name : node.type,
        content: content.length > maxLen ? content.slice(0, maxLen - 1) + "\u2026" : content,
        hasRedaction: node.hasRedaction,
        hasFlag: node.hasFlag,
      });
    }

    return result;
  }, [tree, fullView]);

  if (tree.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a session to review context
      </div>
    );
  }

  const toggleSource = (source: ContextSource) => {
    setExpandedSources((prev) => ({ ...prev, [source]: !prev[source] }));
  };

  const totalItems = SOURCES.reduce((acc, s) => acc + groups[s].items.length, 0);

  if (fullView) {
    return (
      <div className="h-full overflow-y-auto">
        {/* Summary bar */}
        <div className="sticky top-0 z-10 bg-[var(--surface)] border-b border-[var(--border)] px-4 py-2 flex items-center gap-4">
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] uppercase tracking-wider">
            {totalItems} context items
          </span>
          <div className="flex items-center gap-3">
            {SOURCES.map((source) => {
              const group = groups[source];
              if (group.items.length === 0) return null;
              return (
                <span
                  key={source}
                  className="text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-wider"
                  style={{ color: group.color }}
                >
                  {group.items.length} {group.label.toLowerCase()}
                </span>
              );
            })}
          </div>
        </div>

        {/* Category sections */}
        <div className="p-3 space-y-1">
          {SOURCES.map((source) => {
            const group = groups[source];
            if (group.items.length === 0) return null;
            const isExpanded = expandedSources[source];
            const config = SOURCE_CONFIG[source];
            const flagCount = group.items.filter((i) => i.hasFlag).length;
            const redactCount = group.items.filter((i) => i.hasRedaction).length;

            return (
              <div key={source} className="border border-[var(--border)] bg-[var(--surface)]">
                {/* Category header - clickable to expand/collapse */}
                <button
                  onClick={() => toggleSource(source)}
                  className="w-full flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-[var(--surface-hover)] transition-colors duration-100"
                >
                  <div className="flex items-center gap-3">
                    <span
                      className="text-[10px] font-[family-name:var(--font-mono)] font-bold uppercase tracking-wider"
                      style={{ color: group.color }}
                    >
                      {isExpanded ? "\u25BC" : "\u25B6"} {group.label}
                    </span>
                    <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)]">
                      {config.description}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {flagCount > 0 && (
                      <span className="text-[8px] text-[var(--red)] border border-[var(--red)] px-1">
                        {flagCount} flagged
                      </span>
                    )}
                    {redactCount > 0 && (
                      <span className="text-[8px] text-[var(--yellow)] border border-[var(--yellow)] px-1">
                        {redactCount} redacted
                      </span>
                    )}
                    <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
                      {group.items.length}
                    </span>
                  </div>
                </button>

                {/* Expanded items */}
                {isExpanded && (
                  <div className="border-t border-[var(--border)]">
                    {group.items.map((item, i) => (
                      <button
                        key={`${item.nodeId}-${String(i)}`}
                        onClick={() => setSelectedNodeId(item.nodeId)}
                        className="w-full text-left px-3 py-2 text-[11px] font-[family-name:var(--font-mono)] hover:bg-[var(--surface-hover)] transition-colors duration-100 cursor-pointer border-b border-[var(--border)] last:border-b-0 border-l-2"
                        style={{
                          borderLeftColor: item.hasFlag
                            ? "var(--red)"
                            : item.hasRedaction
                              ? "var(--yellow)"
                              : group.color,
                        }}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[var(--text-muted)] text-[9px] uppercase">{item.label}</span>
                          {item.hasFlag && (
                            <span className="text-[8px] text-[var(--red)] border border-[var(--red)] px-1">flag</span>
                          )}
                          {item.hasRedaction && (
                            <span className="text-[8px] text-[var(--yellow)] border border-[var(--yellow)] px-1">redacted</span>
                          )}
                        </div>
                        <div className="text-[var(--text-secondary)] text-[10px] leading-relaxed whitespace-pre-wrap break-all">
                          {item.content}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // Compact view (used inside detail panel if ever needed)
  return (
    <div className="h-full overflow-y-auto p-3 space-y-3">
      {SOURCES.map((source) => {
        const group = groups[source];
        if (group.items.length === 0) return null;

        return (
          <div key={source}>
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
