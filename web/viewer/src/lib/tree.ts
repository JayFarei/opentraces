import type { Step, ToolCall, SecurityFlag, TreeNode } from "../types/trace";
import { summarizeContent, toolDisplayArg } from "./format";

function stepType(step: Step): TreeNode["type"] {
  if (step.call_type === "subagent") return "subagent";
  switch (step.role) {
    case "user":
      return "user";
    case "agent":
      return "agent";
    case "system":
      return "system";
    default:
      return "agent";
  }
}

function stepLabel(step: Step): string {
  // Try content first, cleaned of XML/HTML
  if (step.content) {
    const summary = summarizeContent(step.content, 80);
    if (summary) return summary;
  }
  // For agent steps with tool calls but no content, describe what they do
  if (step.role === "agent" && step.tool_calls.length > 0) {
    const toolNames = [...new Set(step.tool_calls.map((tc) => tc.tool_name))];
    return toolNames.join(", ");
  }
  if (step.role === "system") return "system prompt";
  if (step.agent_role) return step.agent_role;
  return `step ${String(step.step_index)}`;
}

function toolLabel(tc: ToolCall): string {
  const arg = toolDisplayArg(tc.tool_name, tc.input);
  return arg ? `${tc.tool_name}: ${arg}` : tc.tool_name;
}

function toolNodeId(stepIndex: number, toolIndex: number): string {
  return `tool-${String(stepIndex)}-${String(toolIndex)}`;
}

/**
 * Build a tree of TreeNodes from flat step array and optional security flags.
 *
 * Algorithm:
 * 1. Index steps by step_index
 * 2. Build flag lookup set
 * 3. Detect cycles and orphans, promote to root level
 * 4. Recursively build children, expanding tool_calls as child nodes
 */
export function buildTree(
  steps: Step[],
  securityFlags?: SecurityFlag[],
): TreeNode[] {
  if (steps.length === 0) return [];

  // Index steps
  const stepMap = new Map<number, Step>();
  for (const s of steps) {
    stepMap.set(s.step_index, s);
  }

  // Flag lookup
  const flaggedSteps = new Set<number>();
  if (securityFlags) {
    for (const f of securityFlags) {
      flaggedSteps.add(f.step_index);
    }
  }

  // Identify children per parent
  const childrenOf = new Map<number, Step[]>();
  const rootSteps: Step[] = [];

  // Cycle detection: build parent chain for each step
  function hasAncestor(stepIndex: number, target: number): boolean {
    const visited = new Set<number>();
    let current = stepIndex;
    while (current !== undefined && current !== null) {
      if (visited.has(current)) return true; // cycle
      if (current === target) return true;
      visited.add(current);
      const step = stepMap.get(current);
      if (!step || step.parent_step === null || step.parent_step === undefined) break;
      current = step.parent_step;
    }
    return false;
  }

  for (const step of steps) {
    const parentIdx = step.parent_step;
    if (parentIdx === null || parentIdx === undefined) {
      rootSteps.push(step);
    } else if (!stepMap.has(parentIdx)) {
      // Orphan: parent doesn't exist
      rootSteps.push(step);
    } else if (hasAncestor(parentIdx, step.step_index)) {
      // Cycle detected
      rootSteps.push(step);
    } else {
      const siblings = childrenOf.get(parentIdx) ?? [];
      siblings.push(step);
      childrenOf.set(parentIdx, siblings);
    }
  }

  /** Check if a step has no meaningful content (empty or just system noise). */
  function isContentless(step: Step): boolean {
    if (!step.content) return true;
    const cleaned = summarizeContent(step.content, 10);
    return cleaned.length === 0;
  }

  function buildToolNode(step: Step, tc: ToolCall, index: number, depth: number): TreeNode {
    const obs = step.observations.find(
      (o) => o.source_call_id === tc.tool_call_id,
    );
    return {
      id: toolNodeId(step.step_index, index),
      type: "tool",
      label: toolLabel(tc),
      depth,
      children: [],
      step, // link back to parent step for metadata access
      toolCall: tc,
      observation: obs,
      hasFlag: false,
      hasRedaction: false,
    };
  }

  function buildNode(step: Step, depth: number): TreeNode | TreeNode[] {
    const childSteps = childrenOf.get(step.step_index) ?? [];
    childSteps.sort((a, b) => a.step_index - b.step_index);

    // Merge optimization: if this step has no content and ONLY has tool calls
    // (no child steps), promote tool calls to this level instead of nesting
    if (
      isContentless(step) &&
      step.tool_calls.length > 0 &&
      childSteps.length === 0 &&
      step.role === "agent"
    ) {
      return step.tool_calls.map((tc, i) =>
        buildToolNode(step, tc, i, depth),
      );
    }

    const children: TreeNode[] = [];

    // Child steps (recursively, may return arrays from merging)
    for (const child of childSteps) {
      const result = buildNode(child, depth + 1);
      if (Array.isArray(result)) {
        children.push(...result);
      } else {
        children.push(result);
      }
    }

    // Tool call children (only if step has content, otherwise they were promoted)
    for (let i = 0; i < step.tool_calls.length; i++) {
      children.push(buildToolNode(step, step.tool_calls[i]!, i, depth + 1));
    }

    return {
      id: `step-${String(step.step_index)}`,
      type: stepType(step),
      label: stepLabel(step),
      depth,
      children,
      step,
      hasFlag: flaggedSteps.has(step.step_index),
      hasRedaction: false,
    };
  }

  rootSteps.sort((a, b) => a.step_index - b.step_index);
  const result: TreeNode[] = [];
  for (const s of rootSteps) {
    const node = buildNode(s, 0);
    if (Array.isArray(node)) {
      result.push(...node);
    } else {
      result.push(node);
    }
  }
  return result;
}

/**
 * Flatten tree via DFS for virtualized list rendering.
 */
export function flattenTree(roots: TreeNode[]): TreeNode[] {
  const result: TreeNode[] = [];
  function dfs(node: TreeNode): void {
    result.push(node);
    for (const child of node.children) {
      dfs(child);
    }
  }
  for (const root of roots) {
    dfs(root);
  }
  return result;
}

/**
 * Find a node by id anywhere in the tree.
 */
export function findNode(
  roots: TreeNode[],
  id: string,
): TreeNode | null {
  for (const root of roots) {
    if (root.id === id) return root;
    const found = findNode(root.children, id);
    if (found) return found;
  }
  return null;
}
