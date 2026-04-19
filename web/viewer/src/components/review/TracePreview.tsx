import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Panel } from "../Panel";
import { ConversationView } from "./ConversationView";
import { InverseBlameView } from "./InverseBlame";
import { TraceTree } from "./TraceTree";
import type { TraceTreeNode } from "../../lib/api";
import { api } from "../../lib/api";
import { traceMeta } from "../../lib/conversation";
import { traceStepNodeId } from "../../lib/traceTree";

type Tab = "conv" | "blame";

function indexTree(roots: TraceTreeNode[]) {
  const nodes = new Map<string, TraceTreeNode>();
  const parents = new Map<string, string | null>();

  const walk = (node: TraceTreeNode, parentId: string | null) => {
    nodes.set(node.id, node);
    parents.set(node.id, parentId);
    node.children.forEach((child) => walk(child, node.id));
  };

  roots.forEach((node) => walk(node, null));
  return { nodes, parents };
}

function findOwningStep(
  nodeId: string,
  nodes: Map<string, TraceTreeNode>,
  parents: Map<string, string | null>,
): TraceTreeNode | null {
  let current: string | null = nodeId;
  while (current) {
    const node = nodes.get(current) ?? null;
    if (!node) return null;
    if (node.kind === "step") return node;
    current = parents.get(current) ?? null;
  }
  return null;
}

export function resolveTraceTreeSelection(
  roots: TraceTreeNode[],
  nodeId: string,
  stepIndex: number | null,
): { activeStepIndex: number; scrollTargetNodeId: string } | null {
  if (stepIndex != null) {
    return { activeStepIndex: stepIndex, scrollTargetNodeId: nodeId };
  }

  const { nodes, parents } = indexTree(roots);
  const node = nodes.get(nodeId) ?? null;
  if (!node) return null;
  const ownerStep = findOwningStep(nodeId, nodes, parents);
  if (!ownerStep || ownerStep.step_index == null) return null;

  return {
    activeStepIndex: ownerStep.step_index,
    scrollTargetNodeId: node.kind === "compaction" ? ownerStep.id : node.id,
  };
}

export function TracePreview({ t, traceId }: { t: Theme; traceId: string | null }) {
  const [tab, setTab] = useState<Tab>("conv");
  const [viewportWidth, setViewportWidth] = useState(() => (
    typeof window === "undefined" ? 1440 : window.innerWidth
  ));
  const [treePanelOpen, setTreePanelOpen] = useState(false);
  const [treeExpanded, setTreeExpanded] = useState(false);

  const q = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => api.trace(traceId!),
    enabled: !!traceId,
  });
  const treeQ = useQuery({
    queryKey: ["trace-tree", traceId],
    queryFn: () => api.traceTree(traceId!),
    enabled: !!traceId,
  });

  const trace = q.data;
  // Seed the first-step selection from cached query data so a hot mount does
  // not pay for an immediate selection-reset rerender.
  const firstStepIndex = trace?.steps?.length
    ? trace.steps[0]?.step_index ?? null
    : null;
  const firstStepNodeId = firstStepIndex != null ? traceStepNodeId(firstStepIndex) : null;
  const [activeStepIndex, setActiveStepIndex] = useState<number | null>(() => firstStepIndex);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(() => firstStepNodeId);
  const [scrollTargetNodeId, setScrollTargetNodeId] = useState<string | null>(null);
  const meta = trace ? traceMeta(trace) : null;
  const hasTree = (treeQ.data?.tree?.length ?? 0) > 0;
  const treeDocked = viewportWidth >= 1280;
  const activePathNodeId = activeStepIndex != null ? traceStepNodeId(activeStepIndex) : null;

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize, { passive: true });
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!trace?.steps?.length) {
      setActiveStepIndex(null);
      setSelectedNodeId(null);
      setScrollTargetNodeId(null);
      return;
    }
    setActiveStepIndex(firstStepIndex);
    setSelectedNodeId(firstStepNodeId);
    setScrollTargetNodeId(null);
  }, [traceId, trace, firstStepNodeId, firstStepIndex]);

  useEffect(() => {
    setTreePanelOpen(false);
    setTreeExpanded(false);
  }, [traceId, treeDocked]);

  const handleTreeSelect = (nodeId: string, stepIndex: number | null) => {
    setSelectedNodeId(nodeId);
    const resolved = resolveTraceTreeSelection(treeQ.data?.tree ?? [], nodeId, stepIndex);
    if (!resolved) return;
    setActiveStepIndex(resolved.activeStepIndex);
    setScrollTargetNodeId(resolved.scrollTargetNodeId);
  };

  const handleActiveStepChange = (stepIndex: number) => {
    const stepNodeId = traceStepNodeId(stepIndex);
    setActiveStepIndex((current) => (current === stepIndex ? current : stepIndex));
    setSelectedNodeId((current) => {
      if (!current) return stepNodeId;
      if (current === stepNodeId) return current;
      if (current.startsWith(`${stepNodeId}-`)) return current;
      return stepNodeId;
    });
  };

  const conversationPane = trace ? (
    <div
      data-testid="trace-preview-conversation-pane"
      style={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%" }}
    >
      <ConversationView
        t={t}
        steps={trace.steps || []}
        traceId={trace.trace_id}
        activeStepIndex={activeStepIndex}
        selectedNodeId={selectedNodeId}
        scrollTargetNodeId={scrollTargetNodeId}
        onScrollTargetConsumed={() => setScrollTargetNodeId(null)}
        onActiveStepChange={handleActiveStepChange}
      />
    </div>
  ) : null;

  return (
    <Panel n={5} label="Trace Preview" t={t} style={{ flex: 1, minHeight: 0 }}>
      {trace && meta ? (
        <>
          <div style={{
            padding: "0 18px 10px",
            fontFamily: F.code,
            fontSize: 11,
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            lineHeight: 1.8,
            borderBottom: `1px solid ${t.border}`,
          }}>
            {[
              ["agent", meta.agent, t.textSec],
              ["model", meta.model, t.textSec],
              ["steps", String(meta.steps), t.cyan],
              ["tools", String(meta.tools), t.cyan],
              ["flags", String(meta.flags), meta.flags > 0 ? t.yellow : t.green],
              ["in", meta.tokensIn, t.cyan],
              ["out", meta.tokensOut, t.cyan],
              ["cost", meta.cost, t.green],
              ["started", meta.started, t.textMuted],
            ].map(([k, v, c]) => (
              <span key={k}>
                <span style={{ color: t.textDim }}>{k}</span>{" "}
                <span style={{ color: c as string }}>{v}</span>
              </span>
            ))}
          </div>
          <div style={{
            display: "flex",
            gap: 16,
            padding: "0 18px",
            borderBottom: `1px solid ${t.border}`,
            fontFamily: F.code,
            fontSize: 12,
            alignItems: "center",
          }}>
            {(["conv", "blame"] as const).map((id) => (
              <span
                key={id}
                onClick={() => setTab(id)}
                style={{
                  padding: "8px 0",
                  cursor: "pointer",
                  color: tab === id ? t.text : t.textMuted,
                  borderBottom: tab === id ? `1px solid ${t.accent}` : "1px solid transparent",
                  fontWeight: tab === id ? 500 : 400,
                }}
              >
                {id === "conv" ? "conversation" : "blame"}
              </span>
            ))}
            {tab === "conv" && hasTree && !treeDocked ? (
              <button
                data-testid="trace-map-toggle"
                onClick={() => setTreePanelOpen((current) => !current)}
                style={{
                  marginLeft: "auto",
                  border: `1px solid ${t.border}`,
                  background: treePanelOpen ? `${t.cyan}12` : t.bgAlt,
                  color: treePanelOpen ? t.text : t.textMuted,
                  fontFamily: F.code,
                  fontSize: 11,
                  padding: "4px 8px",
                  cursor: "pointer",
                }}
              >
                {treePanelOpen ? "conversation" : "map"}
              </button>
            ) : null}
          </div>
          <div style={{
            flex: 1,
            minHeight: 0,
            overflow: tab === "conv" ? "hidden" : "auto",
            padding: "16px 18px",
          }}>
            {tab === "conv" ? (
              treeDocked ? (
                <div style={{ display: "flex", gap: 16, minHeight: "100%", height: "100%" }}>
                  <TraceTree
                    t={t}
                    traceId={trace.trace_id}
                    roots={treeQ.data?.tree ?? []}
                    selectedNodeId={selectedNodeId}
                    activePathNodeId={activePathNodeId}
                    layout={treeExpanded ? "expanded" : "compact"}
                    onToggleLayout={() => setTreeExpanded((current) => !current)}
                    onSelectNode={handleTreeSelect}
                  />
                  {conversationPane}
                </div>
              ) : treePanelOpen && hasTree ? (
                <TraceTree
                  t={t}
                  traceId={trace.trace_id}
                  roots={treeQ.data?.tree ?? []}
                  selectedNodeId={selectedNodeId}
                  activePathNodeId={activePathNodeId}
                  presentation="panel"
                  layout="expanded"
                  onSelectNode={(nodeId, stepIndex) => {
                    handleTreeSelect(nodeId, stepIndex);
                    setTreePanelOpen(false);
                  }}
                />
              ) : (
                conversationPane
              )
            ) : (
              <InverseBlameView t={t} traceId={trace.trace_id} />
            )}
          </div>
        </>
      ) : (
        <div style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: F.code,
          fontSize: 12,
          color: t.textMuted,
        }}>
          {traceId ? (q.isError ? "failed to load trace" : "loading…") : "no trace selected"}
        </div>
      )}
    </Panel>
  );
}
