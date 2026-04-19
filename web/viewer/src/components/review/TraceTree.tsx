import { useEffect, useMemo, useRef, useState } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import type { TraceTreeNode } from "../../lib/api";
import { flattenTree, type FilterMode, traceStepNodeId } from "../../lib/traceTree";

const FILTER_OPTIONS: Array<{ value: FilterMode; label: string }> = [
  { value: "everything", label: "all" },
  { value: "user-only", label: "prompts" },
];

function ChevronIcon({
  color,
  open,
}: {
  color: string;
  open: boolean;
}) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path
        d={open ? "M2.5 4.5 6 8l3.5-3.5" : "M4.5 2.5 8 6l-3.5 3.5"}
        stroke={color}
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function UserIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="5" r="2.5" stroke={color} strokeWidth="1.3" />
      <path
        d="M3.5 13c.6-2.1 2.4-3.4 4.5-3.4s3.9 1.3 4.5 3.4"
        stroke={color}
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function AgentIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="3.5" y="4" width="9" height="7.5" rx="2" stroke={color} strokeWidth="1.3" />
      <path d="M8 2.5v1.5" stroke={color} strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="6.4" cy="7.3" r="0.7" fill={color} />
      <circle cx="9.6" cy="7.3" r="0.7" fill={color} />
      <path d="M6.2 9.5h3.6" stroke={color} strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  );
}

function ToolIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M10.7 2.9a2.8 2.8 0 0 0-.3 3.3L5 11.6a1.2 1.2 0 0 0 1.7 1.7l5.4-5.4a2.8 2.8 0 0 0 3.3-.3l-2-2 1.1-1.1a2.8 2.8 0 0 0-3.8-.6Z"
        stroke={color}
        strokeWidth="1.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="5.8" cy="10.2" r="0.8" fill={color} />
    </svg>
  );
}

function MapNodeIcon({
  node,
  color,
}: {
  node: TraceTreeNode;
  color: string;
}) {
  if (node.kind === "step" && node.role === "user") return <UserIcon color={color} />;
  if (node.kind === "step") return <AgentIcon color={color} />;
  if (node.kind === "subagent_ref") return <AgentIcon color={color} />;
  return <ToolIcon color={color} />;
}

function compactNodeColor(node: TraceTreeNode, t: Theme): string {
  if (node.kind === "step" && node.role === "user") return t.green;
  if (node.kind === "step") return t.cyan;
  if (node.kind === "subagent_ref") return t.cyan;
  if (node.kind === "tool_call" || node.kind === "observation") return t.yellow;
  return t.textMuted;
}

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

function clearFolded(current: Set<string>): Set<string> {
  return current.size === 0 ? current : new Set();
}

export function TraceTree({
  t,
  traceId,
  roots,
  selectedNodeId,
  activePathNodeId,
  onSelectNode,
  presentation = "rail",
  layout = "expanded",
  onToggleLayout,
}: {
  t: Theme;
  traceId: string;
  roots: TraceTreeNode[];
  selectedNodeId: string | null;
  activePathNodeId: string | null;
  onSelectNode: (nodeId: string, stepIndex: number | null) => void;
  presentation?: "rail" | "panel";
  layout?: "expanded" | "compact";
  onToggleLayout?: () => void;
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
    setFolded(clearFolded);
    setDetailStepId((current) => (current === null ? current : null));
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [traceId]);

  useEffect(() => {
    setFolded(clearFolded);
    setDetailStepId((current) => (current === null ? current : null));
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
  const isCompact = !isPanel && layout === "compact";

  return (
    <div style={{
      width: isPanel ? "100%" : isCompact ? 148 : 390,
      minWidth: isPanel ? 0 : isCompact ? 136 : 360,
      height: "100%",
      borderRight: isPanel ? "none" : `1px solid ${t.border}`,
      paddingRight: isPanel ? 0 : isCompact ? 8 : 12,
      display: "flex",
      flexDirection: "column",
      gap: isCompact ? 8 : 10,
      minHeight: 0,
      background: "transparent",
    }} data-testid={isCompact ? "trace-tree-compact" : "trace-tree-expanded"}>
      {isCompact ? (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim, letterSpacing: "0.08em", textTransform: "uppercase" }}>
            map
          </span>
          {onToggleLayout ? (
            <button
              data-testid="trace-tree-layout-toggle"
              onClick={onToggleLayout}
              style={{
                border: `1px solid ${t.border}`,
                background: t.bgAlt,
                color: t.textMuted,
                fontFamily: F.code,
                fontSize: 10,
                padding: "4px 6px",
                cursor: "pointer",
              }}
            >
              expand
            </button>
          ) : null}
        </div>
      ) : (
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
          {onToggleLayout ? (
            <button
              data-testid="trace-tree-layout-toggle"
              onClick={onToggleLayout}
              style={{
                border: `1px solid ${t.border}`,
                background: t.bgAlt,
                color: t.textMuted,
                fontFamily: F.code,
                fontSize: 11,
                padding: "8px 10px",
                cursor: "pointer",
              }}
            >
              compact
            </button>
          ) : null}
        </div>
      )}
      <div
        ref={scrollRef}
        data-testid="trace-tree-scroll"
        style={{ overflow: "auto", minHeight: 0, paddingRight: isCompact ? 2 : 6 }}
      >
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
          const baseCompactColor = compactNodeColor(row.node, t);
          const iconColor = selected ? baseCompactColor : row.onActivePath ? baseCompactColor : `${baseCompactColor}B8`;
          const handleSelectRow = () => {
            if (filterMode === "everything") {
              if (isStep) setDetailStepId(row.node.id);
              else if (ownerStepId) setDetailStepId(ownerStepId);
            }
            onSelectNode(row.node.id, row.node.step_index);
          };
          const compactSelectable = isCompact && row.node.step_index !== null;
          const markerWidth = isCompact ? 2 : 3;
          const markerGap = 3;
          const markerHeight = isCompact ? 10 : 12;
          const markerColor = row.onActivePath ? t.cyan : t.borderStrong;

          return (
            <div
              key={row.node.id}
              ref={(node) => {
                if (node) rowRefs.current.set(row.node.id, node);
                else rowRefs.current.delete(row.node.id);
              }}
              data-node-id={row.node.id}
              onClick={compactSelectable ? handleSelectRow : undefined}
              onKeyDown={compactSelectable ? (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                handleSelectRow();
              } : undefined}
              role={compactSelectable ? "button" : undefined}
              tabIndex={compactSelectable ? 0 : undefined}
              style={{
                padding: isCompact ? "2px 0" : "3px 0",
                borderLeft: row.onActivePath ? `2px solid ${t.cyan}` : `2px solid transparent`,
                background: selected ? `${t.cyan}12` : "transparent",
                marginBottom: 2,
                paddingLeft: isCompact ? 4 : 6,
                cursor: compactSelectable ? "pointer" : "default",
              }}
            >
              <div style={{ display: "flex", gap: isCompact ? 6 : 8, alignItems: "center", minWidth: 0 }}>
                {branchCount > 0 ? (
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleFold(row.node.id);
                    }}
                    aria-label={folded.has(row.node.id) ? "expand branch" : "collapse branch"}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: t.textMuted,
                      cursor: "pointer",
                      width: isCompact ? 16 : 18,
                      height: isCompact ? 16 : "auto",
                      padding: 0,
                      flex: "0 0 auto",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {isCompact ? (
                      <ChevronIcon color={t.textMuted} open={!folded.has(row.node.id)} />
                    ) : (
                      folded.has(row.node.id) ? "⊞" : "⊟"
                    )}
                  </button>
                ) : (
                  <span
                    style={{
                      width: isCompact ? 16 : 18,
                      color: t.textDim,
                      flex: "0 0 auto",
                      textAlign: "center",
                    }}
                  >
                    {isCompact ? "·" : "•"}
                  </span>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: 3, minWidth: 0, flex: "0 0 auto" }}>
                  {markerCount > 0 ? (
                    <span
                      aria-hidden="true"
                      style={{
                        width: markerCount * markerWidth + Math.max(0, markerCount - 1) * markerGap,
                        height: markerHeight,
                        borderRadius: 999,
                        background: `repeating-linear-gradient(to right, ${markerColor} 0 ${markerWidth}px, transparent ${markerWidth}px ${markerWidth + markerGap}px)`,
                        opacity: row.onActivePath ? 0.9 : 0.7,
                        flex: "0 0 auto",
                      }}
                    />
                  ) : null}
                  {overflowDepth > 0 ? (
                    <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                      +{overflowDepth}
                    </span>
                  ) : null}
                </div>
                {isCompact ? (
                  <div
                    title={compactPreview}
                    aria-label={compactPreview || row.node.kind}
                    style={{
                      flex: 1,
                      height: 28,
                      color: t.textSec,
                      fontFamily: F.code,
                      fontSize: 11,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 8,
                      minWidth: 0,
                      paddingRight: 4,
                    }}
                  >
                    <span style={{
                      color: selected ? t.text : t.textSec,
                      whiteSpace: "nowrap",
                      minWidth: 0,
                    }}>
                      {stepId ?? row.node.kind}
                    </span>
                    <MapNodeIcon node={row.node} color={iconColor} />
                  </div>
                ) : (
                  <button
                    onClick={handleSelectRow}
                    title={compactPreview}
                    aria-label={compactPreview || row.node.kind}
                    style={{
                      flex: 1,
                      border: "none",
                      borderRadius: 0,
                      background: "transparent",
                      color: t.textSec,
                      textAlign: "left",
                      cursor: row.node.step_index !== null ? "pointer" : "default",
                      fontFamily: F.code,
                      fontSize: 11,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "flex-start",
                      gap: 6,
                      minWidth: 0,
                      padding: 0,
                    }}
                  >
                    <>
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
                    </>
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
