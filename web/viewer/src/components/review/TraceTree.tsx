import { useEffect, useMemo, useRef, useState } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import type { TraceTreeNode } from "../../lib/api";
import { flattenTree, type FilterMode, traceStepNodeId } from "../../lib/traceTree";

const FILTER_OPTIONS: Array<{ value: FilterMode; label: string }> = [
  { value: "everything", label: "all" },
  { value: "user-only", label: "prompts" },
];

function buildParentMap(nodes: TraceTreeNode[]): Map<string, string | null> {
  const parents = new Map<string, string | null>();
  const walk = (node: TraceTreeNode) => {
    parents.set(node.id, node.parent_id ?? null);
    node.children.forEach(walk);
  };
  nodes.forEach(walk);
  return parents;
}

function buildOwnerStepMap(nodes: TraceTreeNode[]): Map<string, string | null> {
  const owners = new Map<string, string | null>();
  const walk = (node: TraceTreeNode, ownerStepId: string | null) => {
    const nextOwnerStepId = node.kind === "step" ? node.id : ownerStepId;
    owners.set(node.id, nextOwnerStepId);
    node.children.forEach((child) => walk(child, nextOwnerStepId));
  };
  nodes.forEach((node) => walk(node, null));
  return owners;
}

function buildPromptDescendantCounts(nodes: TraceTreeNode[]): Map<string, number> {
  const counts = new Map<string, number>();
  const walk = (node: TraceTreeNode): number => {
    let total = 0;
    node.children.forEach((child) => {
      total += walk(child);
      if (child.kind === "step" && child.role === "user") total += 1;
    });
    counts.set(node.id, total);
    return total;
  };
  nodes.forEach(walk);
  return counts;
}

export function TraceTree({
  t,
  traceId,
  roots,
  selectedNodeId,
  activePathNodeId,
  onSelectNode,
  presentation = "rail",
}: {
  t: Theme;
  traceId: string;
  roots: TraceTreeNode[];
  selectedNodeId: string | null;
  activePathNodeId: string | null;
  onSelectNode: (nodeId: string, stepIndex: number | null) => void;
  presentation?: "rail" | "panel";
}) {
  const [filterMode, setFilterMode] = useState<FilterMode>("everything");
  const [search, setSearch] = useState("");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [detailStepId, setDetailStepId] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const parents = useMemo(() => buildParentMap(roots), [roots]);
  const ownerStepIds = useMemo(() => buildOwnerStepMap(roots), [roots]);
  const promptDescendantCounts = useMemo(() => buildPromptDescendantCounts(roots), [roots]);
  const isPromptMode = filterMode === "user-only";

  const rows = useMemo(
    () => flattenTree(roots, activePathNodeId, folded, filterMode, search, {
      detailStepId: detailStepId ?? null,
      promoteActiveSiblings: false,
    }),
    [activePathNodeId, detailStepId, filterMode, folded, roots, search],
  );

  const focusedRowId = useMemo(() => {
    const visibleIds = new Set(rows.map((row) => row.node.id));
    const pickVisibleAncestor = (startId: string | null) => {
      let current = startId;
      while (current) {
        if (visibleIds.has(current)) return current;
        current = parents.get(current) ?? null;
      }
      return null;
    };
    return pickVisibleAncestor(selectedNodeId) ?? pickVisibleAncestor(activePathNodeId);
  }, [activePathNodeId, parents, rows, selectedNodeId]);

  useEffect(() => {
    if (!focusedRowId) return;
    rowRefs.current.get(focusedRowId)?.scrollIntoView({ block: "nearest" });
  }, [focusedRowId, rows.length]);

  useEffect(() => {
    setFolded(new Set());
    setDetailStepId(null);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [traceId]);

  useEffect(() => {
    setFolded(new Set());
    setDetailStepId(null);
  }, [filterMode]);

  const toggleFold = (nodeId: string) => {
    setFolded((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

  const isPanel = presentation === "panel";

  return (
    <div style={{
      width: isPanel ? "100%" : 390,
      minWidth: isPanel ? 0 : 360,
      height: "100%",
      borderRight: isPanel ? "none" : `1px solid ${t.border}`,
      paddingRight: isPanel ? 0 : 12,
      display: "flex",
      flexDirection: "column",
      gap: 10,
      minHeight: 0,
      background: "transparent",
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="search map"
          style={{
            flex: 1,
            border: `1px solid ${t.border}`,
            background: t.bgAlt,
            color: t.text,
            padding: "8px 10px",
            fontFamily: F.code,
            fontSize: 11,
          }}
        />
        <select
          data-testid="trace-tree-filter"
          value={filterMode}
          onChange={(event) => setFilterMode(event.target.value as FilterMode)}
          style={{
            border: `1px solid ${t.border}`,
            background: t.bgAlt,
            color: t.textMuted,
            fontFamily: F.code,
            fontSize: 11,
            padding: "8px 10px",
          }}
        >
          {FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
      <div ref={scrollRef} data-testid="trace-tree-scroll" style={{ overflow: "auto", minHeight: 0, paddingRight: 6 }}>
        {rows.length === 0 ? (
          <div style={{
            padding: "8px 4px",
            fontFamily: F.code,
            fontSize: 11,
            color: t.textMuted,
          }}>
            {isPromptMode ? "no prompts match" : "no rows match"}
          </div>
        ) : rows.map((row) => {
          const stepId = row.node.step_index !== null ? traceStepNodeId(row.node.step_index) : null;
          const isStep = row.node.kind === "step" && stepId !== null;
          const selected = focusedRowId === row.node.id;
          const compactPreview = isPromptMode
            ? row.node.preview.replace(/^user:\s*/i, "").replace(/\s+/g, " ").trim()
            : row.node.preview.replace(/\s+/g, " ").trim();
          const markerCount = Math.min(row.indent, 4);
          const overflowDepth = Math.max(0, row.indent - markerCount);
          const promptCount = promptDescendantCounts.get(row.node.id) ?? 0;
          const branchCount = isPromptMode ? promptCount : row.node.children.length;
          const detailCount = isStep ? row.node.children.filter((child) => child.kind !== "step").length : 0;
          const detailOpen = isStep && detailStepId === row.node.id;
          const ownerStepId = ownerStepIds.get(row.node.id) ?? null;
          const nodeBadge = isStep
            ? null
            : row.node.kind === "tool_call"
              ? "tool"
              : row.node.kind === "observation"
                ? "result"
                : row.node.kind === "subagent_ref"
                  ? "branch"
                  : row.node.kind;

          return (
            <div
              key={row.node.id}
              ref={(node) => {
                if (node) rowRefs.current.set(row.node.id, node);
                else rowRefs.current.delete(row.node.id);
              }}
              data-node-id={row.node.id}
              style={{
                padding: "3px 0",
                borderLeft: row.onActivePath ? `2px solid ${t.cyan}` : `2px solid transparent`,
                background: selected ? `${t.cyan}12` : "transparent",
                marginBottom: 2,
                paddingLeft: 6,
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center", minWidth: 0 }}>
                {branchCount > 0 ? (
                  <button
                    onClick={() => toggleFold(row.node.id)}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: t.textMuted,
                      cursor: "pointer",
                      width: 18,
                      padding: 0,
                      flex: "0 0 auto",
                    }}
                  >
                    {folded.has(row.node.id) ? "⊞" : "⊟"}
                  </button>
                ) : (
                  <span style={{ width: 18, color: t.textDim, flex: "0 0 auto" }}>•</span>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: 3, minWidth: 0, flex: "0 0 auto" }}>
                  {Array.from({ length: markerCount }).map((_, index) => (
                    <span
                      key={`${row.node.id}-depth-${index}`}
                      style={{
                        width: 3,
                        height: 12,
                        borderRadius: 999,
                        background: row.onActivePath ? t.cyan : `${t.borderStrong}`,
                        opacity: row.onActivePath ? 0.9 : 0.7,
                      }}
                    />
                  ))}
                  {overflowDepth > 0 ? (
                    <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                      +{overflowDepth}
                    </span>
                  ) : null}
                </div>
                <button
                  onClick={() => {
                    if (filterMode === "everything") {
                      if (isStep) setDetailStepId(row.node.id);
                      else if (ownerStepId) setDetailStepId(ownerStepId);
                    }
                    onSelectNode(row.node.id, row.node.step_index);
                  }}
                  style={{
                    flex: 1,
                    border: "none",
                    background: "transparent",
                    color: t.textSec,
                    textAlign: "left",
                    cursor: row.node.step_index !== null ? "pointer" : "default",
                    fontFamily: F.code,
                    fontSize: 11,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    minWidth: 0,
                    padding: 0,
                  }}
                >
                  {isPromptMode ? (
                    <>
                      {stepId ? (
                        <span style={{
                          color: selected ? t.text : t.textMuted,
                          background: selected ? `${t.cyan}18` : t.bgAlt,
                          border: `1px solid ${selected ? t.cyan : t.border}`,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          {stepId}
                        </span>
                      ) : null}
                      {promptCount > 0 ? (
                        <span style={{
                          color: selected ? t.text : t.textDim,
                          border: `1px solid ${selected ? t.cyan : t.border}`,
                          background: selected ? `${t.cyan}18` : t.bgAlt,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          +{promptCount}
                        </span>
                      ) : null}
                    </>
                  ) : (
                    <>
                      {isStep && stepId ? (
                        <span style={{
                          color: selected ? t.text : t.textMuted,
                          background: selected ? `${t.cyan}18` : t.bgAlt,
                          border: `1px solid ${selected ? t.cyan : t.border}`,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          {stepId}
                        </span>
                      ) : null}
                      {nodeBadge ? (
                        <span style={{
                          color: selected ? t.text : t.textMuted,
                          background: selected ? `${t.cyan}18` : t.bgAlt,
                          border: `1px solid ${selected ? t.cyan : t.border}`,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          {nodeBadge}
                        </span>
                      ) : null}
                      {!isStep && stepId ? (
                        <span style={{
                          color: selected ? t.text : t.textDim,
                          border: `1px solid ${selected ? t.cyan : t.border}`,
                          background: selected ? `${t.cyan}18` : t.bgAlt,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          {stepId}
                        </span>
                      ) : null}
                      {isStep && detailCount > 0 ? (
                        <span style={{
                          color: detailOpen ? t.text : t.textMuted,
                          border: `1px solid ${detailOpen ? t.cyan : t.border}`,
                          background: detailOpen ? `${t.cyan}18` : t.bgAlt,
                          padding: "1px 5px",
                          whiteSpace: "nowrap",
                        }}>
                          {detailOpen ? "open" : `+${detailCount}`}
                        </span>
                      ) : null}
                    </>
                  )}
                  {row.node.label ? (
                    <span style={{
                      color: t.yellow,
                      border: `1px solid ${t.border}`,
                      background: `${t.yellow}12`,
                      padding: "1px 5px",
                      whiteSpace: "nowrap",
                    }}>
                      {row.node.label}
                    </span>
                  ) : null}
                  <span style={{
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: selected ? t.text : t.textSec,
                  }}>
                    {compactPreview}
                  </span>
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
