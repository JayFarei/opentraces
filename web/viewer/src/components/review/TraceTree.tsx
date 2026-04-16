import { useEffect, useMemo, useRef, useState } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { api, type TraceTreeNode } from "../../lib/api";
import { flattenTree, type FilterMode, traceStepNodeId } from "../../lib/traceTree";

const FILTER_OPTIONS: Array<{ value: FilterMode; label: string }> = [
  { value: "default", label: "everything" },
  { value: "no-tools", label: "no tools" },
  { value: "user-only", label: "prompts only" },
];

export function TraceTree({
  t,
  traceId,
  roots,
  selectedNodeId,
  activePathNodeId,
  onSelectNode,
  presentation = "rail",
  onClose,
}: {
  t: Theme;
  traceId: string;
  roots: TraceTreeNode[];
  selectedNodeId: string | null;
  activePathNodeId: string | null;
  onSelectNode: (nodeId: string, stepIndex: number | null) => void;
  presentation?: "rail" | "drawer";
  onClose?: () => void;
}) {
  const [filterMode, setFilterMode] = useState<FilterMode>("default");
  const [search, setSearch] = useState("");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [detailStepId, setDetailStepId] = useState<string | null>(null);
  const [resumeInfo, setResumeInfo] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(
    () => flattenTree(roots, activePathNodeId, folded, filterMode, search, {
      detailStepId,
      promoteActiveSiblings: false,
    }),
    [roots, activePathNodeId, detailStepId, folded, filterMode, search],
  );

  useEffect(() => {
    if (!selectedNodeId) return;
    rowRefs.current.get(selectedNodeId)?.scrollIntoView({ block: "nearest" });
  }, [selectedNodeId, rows.length]);

  useEffect(() => {
    setDetailStepId(null);
    setFolded(new Set());
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [traceId]);

  const toggleFold = (nodeId: string) => {
    setFolded((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

  const onResume = async (stepId: string) => {
    const result = await api.resumeTrace(traceId, stepId);
    setResumeInfo(`${result.argv.join(" ")}\ntruncated ${result.truncated_at_line} lines`);
  };

  const isDrawer = presentation === "drawer";

  return (
    <div style={{
      width: isDrawer ? "100%" : 390,
      minWidth: isDrawer ? 0 : 360,
      borderRight: isDrawer ? "none" : `1px solid ${t.border}`,
      borderLeft: isDrawer ? `1px solid ${t.borderStrong}` : "none",
      paddingRight: isDrawer ? 0 : 12,
      display: "flex",
      flexDirection: "column",
      gap: 10,
      minHeight: 0,
      background: isDrawer ? t.panelBg : "transparent",
      boxShadow: isDrawer ? "0 0 0 1px rgba(0,0,0,0.18), -20px 0 40px rgba(0,0,0,0.28)" : "none",
    }}>
      {isDrawer ? (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "4px 0 0",
        }}>
          <span style={{
            fontFamily: F.code,
            fontSize: 11,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: t.textMuted,
          }}>
            conversation map
          </span>
          {onClose ? (
            <button
              onClick={onClose}
              style={{
                border: `1px solid ${t.border}`,
                background: t.bgAlt,
                color: t.textMuted,
                fontFamily: F.code,
                fontSize: 11,
                cursor: "pointer",
                padding: "4px 8px",
              }}
            >
              close
            </button>
          ) : null}
        </div>
      ) : null}
      <div style={{ display: "flex", gap: 8 }}>
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
          value={filterMode}
          onChange={(event) => setFilterMode(event.target.value as FilterMode)}
          style={{
            border: `1px solid ${t.border}`,
            background: t.bgAlt,
            color: t.text,
            fontFamily: F.code,
            fontSize: 11,
          }}
        >
          {FILTER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>
      <div ref={scrollRef} data-testid="trace-tree-scroll" style={{ overflow: "auto", minHeight: 0, paddingRight: 6 }}>
        {rows.map((row) => {
          const stepId = row.node.step_index !== null ? traceStepNodeId(row.node.step_index) : null;
          const isStep = row.node.kind === "step" && stepId !== null;
          const resumeStepId = isStep ? stepId : null;
          const selected = selectedNodeId === row.node.id;
          const detailCount = isStep ? row.node.children.filter((child) => child.kind !== "step").length : 0;
          const detailOpen = isStep && detailStepId === row.node.id;
          const compactPreview = row.node.preview.replace(/\s+/g, " ").trim();
          const markerCount = Math.min(row.indent, 4);
          const overflowDepth = Math.max(0, row.indent - markerCount);
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
                {row.node.children.length > 0 ? (
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
                    if (isStep) setDetailStepId(row.node.id);
                    else if (stepId) setDetailStepId(stepId);
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
                {resumeStepId ? (
                  <button
                    onClick={() => void onResume(resumeStepId)}
                    style={{
                      border: `1px solid ${t.border}`,
                      background: t.bgAlt,
                      color: t.textMuted,
                      fontFamily: F.code,
                      fontSize: 10,
                      cursor: "pointer",
                    }}
                  >
                    resume
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {resumeInfo ? (
        <div style={{
          border: `1px solid ${t.border}`,
          background: t.bgAlt,
          padding: 10,
          fontFamily: F.code,
          fontSize: 11,
          whiteSpace: "pre-wrap",
        }}>
          {resumeInfo}
        </div>
      ) : null}
    </div>
  );
}
