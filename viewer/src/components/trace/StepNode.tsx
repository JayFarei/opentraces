import { useSelection } from "../../contexts/SelectionContext";
import { formatTokens, formatDuration } from "../../lib/format";
import type { TreeNode } from "../../types/trace";

/** Tool-specific icons (monospace-friendly characters). */
const TOOL_ICONS: Record<string, string> = {
  Read: "\u{1F4C4}",      // 📄
  Edit: "\u{270F}\u{FE0F}", // ✏️
  Write: "\u{1F4DD}",     // 📝
  Bash: "\u{1F4BB}",      // 💻
  Grep: "\u{1F50D}",      // 🔍
  Glob: "\u{1F4C1}",      // 📁
  WebSearch: "\u{1F310}",  // 🌐
  WebFetch: "\u{1F310}",   // 🌐
  Agent: "\u{1F916}",     // 🤖
  ToolSearch: "\u{1F50E}", // 🔎
  AskUserQuestion: "\u{2753}", // ❓
  Skill: "\u{26A1}",      // ⚡
};

/** Role icons for step-level nodes. */
const ROLE_ICONS: Record<TreeNode["type"], string> = {
  user: "\u{1F464}",    // 👤
  agent: "\u{1F916}",   // 🤖
  tool: "\u{1F527}",    // 🔧
  system: "\u{2699}\u{FE0F}", // ⚙️
  subagent: "\u{1F500}", // 🔀
};

const ROLE_COLORS: Record<TreeNode["type"], string> = {
  user: "var(--blue)",
  agent: "var(--green)",
  tool: "var(--purple, #A855F7)",
  system: "var(--text-muted)",
  subagent: "var(--cyan)",
};

const TOOL_COLORS: Record<string, string> = {
  Read: "var(--blue)",
  Edit: "var(--yellow, #EAB308)",
  Write: "var(--accent)",
  Bash: "var(--green)",
  Grep: "var(--purple, #A855F7)",
  Glob: "var(--cyan)",
  WebSearch: "var(--blue)",
  WebFetch: "var(--blue)",
  Agent: "var(--cyan)",
};

interface StepNodeProps {
  node: TreeNode;
}

export function StepNode({ node }: StepNodeProps) {
  const { selectedNodeId, setSelectedNodeId } = useSelection();
  const isSelected = selectedNodeId === node.id;

  const isTool = node.type === "tool" && node.toolCall;
  const toolName = node.toolCall?.tool_name ?? "";

  // Icon: tool-specific or role-based
  const icon = isTool
    ? (TOOL_ICONS[toolName] ?? "\u{1F527}")
    : ROLE_ICONS[node.type];

  // Color for the tool name text
  const nameColor = isTool
    ? (TOOL_COLORS[toolName] ?? "var(--text)")
    : ROLE_COLORS[node.type];

  // Metrics
  const tokenCount = node.step
    ? node.step.token_usage.input_tokens + node.step.token_usage.output_tokens
    : null;
  const duration = node.toolCall?.duration_ms ?? null;

  return (
    <button
      onClick={() => setSelectedNodeId(node.id)}
      className={`w-full h-full flex items-center pr-3 transition-colors duration-100 cursor-pointer ${
        isSelected
          ? "bg-[var(--surface-hover)] border-l-2 border-l-[var(--accent)]"
          : "hover:bg-[var(--surface-hover)] border-l-2 border-l-transparent"
      } ${node.type === "subagent" ? "!border-l-2 !border-l-[var(--cyan)]" : ""}`}
      style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
    >
      {/* Icon */}
      <span className="flex-none w-5 text-center text-[12px] mr-1.5">
        {icon}
      </span>

      {/* Tool name (colored) + label */}
      {isTool ? (
        <span className="flex-1 flex items-baseline gap-1.5 truncate text-left min-w-0">
          <span
            className="flex-none text-[11px] font-[family-name:var(--font-mono)] font-semibold"
            style={{ color: nameColor }}
          >
            {toolName}
          </span>
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] truncate">
            {node.label.startsWith(toolName + ": ")
              ? node.label.slice(toolName.length + 2)
              : node.label}
          </span>
        </span>
      ) : (
        <span
          className="flex-1 text-[11px] font-[family-name:var(--font-mono)] truncate text-left"
          style={{ color: nameColor }}
        >
          {node.label}
        </span>
      )}

      {/* Indicators */}
      {(node.hasRedaction || node.hasFlag) && (
        <span className="flex items-center gap-1 ml-1.5 flex-none">
          {node.hasRedaction && (
            <span className="w-1.5 h-1.5 bg-[var(--yellow)]" title="has redaction" />
          )}
          {node.hasFlag && (
            <span className="w-1.5 h-1.5 bg-[var(--red)]" title="has flag" />
          )}
        </span>
      )}

      {/* Metrics (Langfuse-style: inline duration + tokens) */}
      <span className="flex items-center gap-1.5 flex-none text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] ml-2 tabular-nums">
        {duration !== null && (
          <span>{formatDuration(duration)}</span>
        )}
        {tokenCount !== null && tokenCount > 0 && (
          <span>{formatTokens(tokenCount)}</span>
        )}
      </span>
    </button>
  );
}
