import { useMemo } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { formatTokens } from "../../lib/format";
import type { TreeNode } from "../../types/trace";

/**
 * Context source categories for trace visualization.
 * Classifies each action by where context comes from.
 */
type ContextSource = "user" | "project" | "external" | "llm";

interface ContextGroup {
  source: ContextSource;
  label: string;
  color: string;
  nodes: TreeNode[];
  tokenTotal: number;
}

/** Classify a tree node by its context source. */
function classifyNode(node: TreeNode): ContextSource {
  // User messages
  if (node.type === "user") return "user";

  // Tool calls: classify by tool type
  if (node.type === "tool" && node.toolCall) {
    const tool = node.toolCall.tool_name;
    // Local file operations = project context
    if (["Read", "Edit", "Write", "Glob", "Grep"].includes(tool)) return "project";
    // Network/external = external context
    if (["WebSearch", "WebFetch"].includes(tool)) return "external";
    // Bash could be either, but most are local
    if (tool === "Bash") return "project";
    // Agent/subagent spawning
    if (tool === "Agent") return "llm";
  }

  // Subagent steps = LLM
  if (node.type === "subagent") return "llm";
  // Agent steps = LLM (reasoning/generation)
  if (node.type === "agent") return "llm";
  // System = project/environment
  if (node.type === "system") return "project";

  return "llm";
}

const SOURCE_CONFIG: Record<ContextSource, { label: string; color: string }> = {
  user: { label: "user", color: "var(--blue)" },
  project: { label: "project", color: "var(--green)" },
  external: { label: "external", color: "var(--accent)" },
  llm: { label: "llm", color: "var(--purple, #A855F7)" },
};

interface ContextFlowProps {
  tree: TreeNode[];
}

export function ContextFlow({ tree }: ContextFlowProps) {
  const { selectedNodeId, setSelectedNodeId } = useSelection();

  const groups = useMemo(() => {
    const result: Record<ContextSource, ContextGroup> = {
      user: { source: "user", ...SOURCE_CONFIG.user, nodes: [], tokenTotal: 0 },
      project: { source: "project", ...SOURCE_CONFIG.project, nodes: [], tokenTotal: 0 },
      external: { source: "external", ...SOURCE_CONFIG.external, nodes: [], tokenTotal: 0 },
      llm: { source: "llm", ...SOURCE_CONFIG.llm, nodes: [], tokenTotal: 0 },
    };

    // Flatten tree and classify each node
    function walk(nodes: TreeNode[]): void {
      for (const node of nodes) {
        const source = classifyNode(node);
        result[source].nodes.push(node);
        const tokens = node.step
          ? node.step.token_usage.input_tokens + node.step.token_usage.output_tokens
          : 0;
        result[source].tokenTotal += tokens;
        walk(node.children);
      }
    }
    walk(tree);

    return result;
  }, [tree]);

  const totalNodes = Object.values(groups).reduce((sum, g) => sum + g.nodes.length, 0);

  if (totalNodes === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a session
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-2">
      {/* Header: source distribution bar */}
      <div className="flex h-3 mb-3 overflow-hidden">
        {(["user", "project", "external", "llm"] as ContextSource[]).map((source) => {
          const group = groups[source];
          const pct = totalNodes > 0 ? (group.nodes.length / totalNodes) * 100 : 0;
          if (pct === 0) return null;
          return (
            <div
              key={source}
              className="h-full transition-all duration-200"
              style={{ width: `${String(pct)}%`, backgroundColor: group.color, opacity: 0.7 }}
              title={`${group.label}: ${String(group.nodes.length)} actions (${String(Math.round(pct))}%)`}
            />
          );
        })}
      </div>

      {/* Source columns */}
      <div className="space-y-2">
        {(["user", "project", "external", "llm"] as ContextSource[]).map((source) => {
          const group = groups[source];
          if (group.nodes.length === 0) return null;

          return (
            <div key={source} className="border border-[var(--border)]">
              {/* Source header */}
              <div
                className="flex items-center justify-between px-2 py-1 border-b border-[var(--border)]"
                style={{ borderLeftWidth: 3, borderLeftColor: group.color }}
              >
                <span
                  className="text-[10px] font-[family-name:var(--font-mono)] font-semibold uppercase tracking-wider"
                  style={{ color: group.color }}
                >
                  {group.label}
                </span>
                <span className="text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
                  {String(group.nodes.length)} actions
                  {group.tokenTotal > 0 && ` / ${formatTokens(group.tokenTotal)}`}
                </span>
              </div>

              {/* Actions in this source */}
              <div className="max-h-32 overflow-y-auto">
                {group.nodes.slice(0, 20).map((node) => {
                  const isSelected = selectedNodeId === node.id;
                  return (
                    <button
                      key={node.id}
                      onClick={() => setSelectedNodeId(node.id)}
                      className={`w-full text-left px-2 py-0.5 text-[10px] font-[family-name:var(--font-mono)] truncate cursor-pointer transition-colors duration-100 ${
                        isSelected
                          ? "bg-[var(--surface-hover)] text-[var(--text)]"
                          : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"
                      }`}
                    >
                      {node.toolCall ? (
                        <>
                          <span style={{ color: group.color }}>{node.toolCall.tool_name}</span>
                          {" "}
                          <span className="text-[var(--text-muted)]">
                            {node.label.startsWith(node.toolCall.tool_name + ": ")
                              ? node.label.slice(node.toolCall.tool_name.length + 2)
                              : ""}
                          </span>
                        </>
                      ) : (
                        node.label
                      )}
                    </button>
                  );
                })}
                {group.nodes.length > 20 && (
                  <div className="px-2 py-0.5 text-[9px] text-[var(--text-muted)]">
                    +{String(group.nodes.length - 20)} more
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
