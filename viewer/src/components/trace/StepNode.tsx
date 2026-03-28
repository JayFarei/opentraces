import { useRef, type ComponentType } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { formatTokens, formatDuration } from "../../lib/format";
import type { TreeNode } from "../../types/trace";
import type { AnimatedIconProps, AnimatedIconHandle } from "../icons/types";
import {
  FileDescriptionIcon,
  PenIcon,
  CodeIcon,
  TerminalIcon,
  MagnifierIcon,
  GlobeIcon,
  BrainCircuitIcon,
  UserIcon,
  SparklesIcon,
  CodeXmlIcon,
} from "../icons";

/** Tool name -> itshover icon component. */
const TOOL_ICON_MAP: Record<string, ComponentType<AnimatedIconProps>> = {
  Read: FileDescriptionIcon,
  Edit: PenIcon,
  Write: CodeIcon,
  Bash: TerminalIcon,
  Grep: MagnifierIcon,
  Glob: FileDescriptionIcon,
  WebSearch: GlobeIcon,
  WebFetch: GlobeIcon,
  Agent: BrainCircuitIcon,
  ToolSearch: MagnifierIcon,
  AskUserQuestion: UserIcon,
  Skill: SparklesIcon,
  NotebookEdit: CodeXmlIcon,
};

/** Role type -> itshover icon component. */
const ROLE_ICON_MAP: Record<TreeNode["type"], ComponentType<AnimatedIconProps>> = {
  user: UserIcon,
  agent: BrainCircuitIcon,
  tool: TerminalIcon,
  system: SparklesIcon,
  subagent: BrainCircuitIcon,
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
  const iconRef = useRef<AnimatedIconHandle>(null);
  const isSelected = selectedNodeId === node.id;

  const isTool = node.type === "tool" && node.toolCall;
  const toolName = node.toolCall?.tool_name ?? "";

  // Resolve icon component
  const IconComponent = isTool
    ? (TOOL_ICON_MAP[toolName] ?? TerminalIcon)
    : ROLE_ICON_MAP[node.type];

  // Color for the icon and tool name
  const iconColor = isTool
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
      onMouseEnter={() => iconRef.current?.startAnimation()}
      onMouseLeave={() => iconRef.current?.stopAnimation()}
      className={`w-full h-full flex items-center pr-3 transition-colors duration-100 cursor-pointer ${
        isSelected
          ? "bg-[var(--surface-hover)] border-l-2 border-l-[var(--accent)]"
          : "hover:bg-[var(--surface-hover)] border-l-2 border-l-transparent"
      } ${node.type === "subagent" ? "!border-l-2 !border-l-[var(--cyan)]" : ""}`}
      style={{ paddingLeft: `${node.depth * 16 + 8}px` }}
    >
      {/* Animated icon */}
      <span className="flex-none flex items-center justify-center w-5 h-5 mr-1.5" style={{ color: iconColor }}>
        <IconComponent ref={iconRef} size={14} color={iconColor} strokeWidth={2} />
      </span>

      {/* Tool name (colored) + label */}
      {isTool ? (
        <span className="flex-1 flex items-baseline gap-1.5 truncate text-left min-w-0">
          <span
            className="flex-none text-[11px] font-[family-name:var(--font-mono)] font-semibold"
            style={{ color: iconColor }}
          >
            {toolName}
          </span>
          <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] truncate">
            {node.label.startsWith(toolName + ": ")
              ? node.label.slice(toolName.length + 2)
              : node.label !== toolName ? node.label : ""}
          </span>
        </span>
      ) : (
        <span
          className="flex-1 text-[11px] font-[family-name:var(--font-mono)] truncate text-left"
          style={{ color: node.type === "user" ? iconColor : "var(--text)" }}
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
