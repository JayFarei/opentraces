import { useRef, type ComponentType } from "react";
import { useSelection } from "../../contexts/SelectionContext";
import { formatTokens, formatDuration, formatTimeOffset } from "../../lib/format";
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

/**
 * Color system: each context source gets ONE color, no collisions.
 *   user = blue
 *   agent/LLM = purple (reasoning, distinct from tools)
 *   proj/local = green (file operations)
 *   ext/network = orange/accent
 *   subagent = cyan
 */
const ROLE_COLORS: Record<TreeNode["type"], string> = {
  user: "var(--blue)",
  agent: "var(--purple, #A855F7)",
  tool: "var(--text-secondary)",
  system: "var(--text-muted)",
  subagent: "var(--cyan)",
};

const TOOL_COLORS: Record<string, string> = {
  Read: "var(--green)",
  Edit: "var(--yellow, #EAB308)",
  Write: "var(--yellow, #EAB308)",
  Bash: "var(--green)",
  Grep: "var(--green)",
  Glob: "var(--green)",
  WebSearch: "var(--accent)",
  WebFetch: "var(--accent)",
  Agent: "var(--cyan)",
  ToolSearch: "var(--accent)",
};

/** Classify a node's context source for the source tag. */
type ContextSource = "user" | "agent" | "proj" | "ext";

function classifySource(node: TreeNode): ContextSource {
  if (node.type === "user") return "user";

  if (node.type === "tool" && node.toolCall) {
    const tool = node.toolCall.tool_name;
    if (["Read", "Edit", "Write", "Glob", "Grep", "Bash"].includes(tool)) return "proj";
    if (["WebSearch", "WebFetch"].includes(tool)) return "ext";
  }

  if (node.type === "subagent") return "agent";
  if (node.type === "agent") return "agent";
  if (node.type === "system") return "proj";

  return "agent";
}

const SOURCE_COLORS: Record<ContextSource, string> = {
  user: "var(--blue)",
  agent: "var(--purple, #A855F7)",
  proj: "var(--green)",
  ext: "var(--accent)",
};

interface StepNodeProps {
  node: TreeNode;
  traceStartMs: number | null;
}

export function StepNode({ node, traceStartMs }: StepNodeProps) {
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

  // Time offset: only on step-level nodes (not tool calls without their own step)
  const isStepLevel = node.step && !node.toolCall;
  let timeOffsetStr: string | null = null;
  if (isStepLevel && traceStartMs !== null && node.step?.timestamp) {
    try {
      const stepMs = new Date(node.step.timestamp).getTime();
      const offsetMs = stepMs - traceStartMs;
      if (offsetMs >= 0) {
        timeOffsetStr = formatTimeOffset(offsetMs);
      }
    } catch {
      /* ignore */
    }
  }

  // Source tag
  const source = classifySource(node);
  const sourceColor = SOURCE_COLORS[source];

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
    >
      {/* Time column: fixed 40px */}
      <span className="flex-none w-[40px] text-[9px] font-[family-name:var(--font-mono)] text-[var(--text-dim)] tabular-nums text-right pr-2">
        {timeOffsetStr ?? ""}
      </span>

      {/* Source tag: fixed 44px */}
      <span
        className="flex-none w-[44px] text-[9px] font-[family-name:var(--font-mono)] tabular-nums"
        style={{ color: sourceColor }}
      >
        [{source}]
      </span>

      {/* Indent spacer for depth */}
      {node.depth > 0 && (
        <span className="flex-none" style={{ width: `${node.depth * 14}px` }} />
      )}

      {/* Animated icon */}
      <span className="flex-none flex items-center justify-center w-4 h-4 mr-1.5" style={{ color: iconColor }}>
        <IconComponent ref={iconRef} size={13} color={iconColor} strokeWidth={2} />
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

      {/* Metrics (right-aligned: duration + tokens) */}
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
