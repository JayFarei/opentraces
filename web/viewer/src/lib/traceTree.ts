import type { TraceTreeNode } from "./api";

export type FilterMode = "everything" | "user-only" | "agent-only";

export interface FlatTreeNode {
  node: TraceTreeNode;
  indent: number;
  isLast: boolean;
  onActivePath: boolean;
}

export interface FlattenTreeOptions {
  detailStepId?: string | null;
  promoteActiveSiblings?: boolean;
}

export function traceStepNodeId(stepIndex: number): string {
  return `s${stepIndex}`;
}

export function traceToolCallNodeId(stepIndex: number, toolCallIndex: number): string {
  return `${traceStepNodeId(stepIndex)}-tc${toolCallIndex}`;
}

export function traceObservationNodeId(stepIndex: number, observationIndex: number): string {
  return `${traceStepNodeId(stepIndex)}-ob${observationIndex}`;
}

export function traceSubagentNodeId(stepIndex: number): string {
  return `${traceStepNodeId(stepIndex)}-subagent`;
}

export function conversationAnchorId(nodeId: string): string {
  return `trace-node-${nodeId}`;
}

function parentMap(nodes: TraceTreeNode[]): Map<string, string | null> {
  const map = new Map<string, string | null>();
  const walk = (node: TraceTreeNode) => {
    map.set(node.id, node.parent_id ?? null);
    node.children.forEach(walk);
  };
  nodes.forEach(walk);
  return map;
}

function activePath(selectedId: string | null, parents: Map<string, string | null>): Set<string> {
  const ids = new Set<string>();
  let current = selectedId;
  while (current) {
    ids.add(current);
    current = parents.get(current) ?? null;
  }
  return ids;
}

function passes(node: TraceTreeNode, filterMode: FilterMode, search: string): boolean {
  const text = `${node.preview} ${node.label ?? ""}`.toLowerCase();
  const tokens = search.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length && !tokens.every((token) => text.includes(token))) return false;
  switch (filterMode) {
    case "everything":
      return true;
    case "user-only":
      return node.kind === "step" && node.role === "user";
    case "agent-only":
      return node.kind === "step" && node.role === "agent";
  }
}

export function flattenTree(
  roots: TraceTreeNode[],
  selectedId: string | null,
  folded: Set<string>,
  filterMode: FilterMode,
  search: string,
  options: FlattenTreeOptions = {},
): FlatTreeNode[] {
  const { detailStepId, promoteActiveSiblings = true } = options;
  const parents = parentMap(roots);
  const active = activePath(selectedId, parents);
  const rows: FlatTreeNode[] = [];

  const sortChildren = (children: TraceTreeNode[], parentId: string | null) => [...children]
    .map((child, index) => ({ child, index }))
    .sort((a, b) => {
      const aDetail = detailStepId !== undefined && parentId === detailStepId && a.child.kind !== "step" ? 0 : 1;
      const bDetail = detailStepId !== undefined && parentId === detailStepId && b.child.kind !== "step" ? 0 : 1;
      if (aDetail !== bDetail) return aDetail - bDetail;
      if (promoteActiveSiblings) {
        const aActive = active.has(a.child.id) ? 0 : 1;
        const bActive = active.has(b.child.id) ? 0 : 1;
        if (aActive !== bActive) return aActive - bActive;
      }
      return a.index - b.index;
    })
    .map(({ child }) => child);

  const walk = (node: TraceTreeNode, indent: number, isLast: boolean, ownerStepId: string | null) => {
    const nextOwnerStepId = node.kind === "step" ? node.id : ownerStepId;
    const visible = passes(node, filterMode, search);
    const detailsVisible = detailStepId === undefined || node.kind === "step" || ownerStepId === detailStepId;
    const shouldRender = visible && detailsVisible;

    if (shouldRender) {
      rows.push({ node, indent, isLast, onActivePath: active.has(node.id) });
    }
    if (shouldRender && folded.has(node.id)) return;

    const children = sortChildren(node.children, node.id);
    children.forEach((child, index) => walk(child, shouldRender ? indent + 1 : indent, index === children.length - 1, nextOwnerStepId));
  };

  const orderedRoots = sortChildren(roots, null);
  orderedRoots.forEach((root, index) => walk(root, 0, index === orderedRoots.length - 1, null));
  return rows;
}
