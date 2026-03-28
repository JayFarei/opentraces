import { useSelection } from "../../contexts/SelectionContext";
import type { TreeNode } from "../../types/trace";

const ROLE_COLORS: Record<TreeNode["type"], string> = {
  user: "var(--blue)",
  agent: "var(--green)",
  tool: "var(--purple, #A855F7)",
  system: "var(--text-muted)",
  subagent: "var(--cyan)",
};

const ROLE_LETTERS: Record<TreeNode["type"], string> = {
  user: "U",
  agent: "A",
  tool: "T",
  system: "S",
  subagent: "S",
};

interface StepNodeProps {
  node: TreeNode;
}

export function StepNode({ node }: StepNodeProps) {
  const { selectedNodeId, setSelectedNodeId } = useSelection();
  const isSelected = selectedNodeId === node.id;
  const color = ROLE_COLORS[node.type];
  const letter = ROLE_LETTERS[node.type];

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
      style={{ paddingLeft: `${node.depth * 20 + 8}px` }}
    >
      {/* Role icon */}
      <span
        className="flex-none flex items-center justify-center w-4 h-4 text-[9px] font-[family-name:var(--font-mono)] font-bold mr-2"
        style={{ color, border: `1px solid ${color}` }}
      >
        {letter}
      </span>

      {/* Label */}
      <span className="flex-1 text-[11px] font-[family-name:var(--font-mono)] text-[var(--text)] truncate text-left">
        {node.label}
      </span>

      {/* Indicators */}
      <span className="flex items-center gap-1 ml-2 flex-none">
        {node.hasRedaction && (
          <span className="w-1.5 h-1.5 bg-[var(--yellow)]" title="has redaction" />
        )}
        {node.hasFlag && (
          <span className="w-1.5 h-1.5 bg-[var(--red)]" title="has flag" />
        )}
      </span>

      {/* Meta */}
      <span className="flex-none text-[8px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] ml-2 tabular-nums">
        {tokenCount !== null && tokenCount > 0 && `${tokenCount.toLocaleString()}t`}
        {duration !== null && `${duration}ms`}
      </span>
    </button>
  );
}
