import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Panel } from "../Panel";
import { ConversationView } from "./ConversationView";
import { InverseBlameView } from "./InverseBlame";
import { TraceTree } from "./TraceTree";
import { api } from "../../lib/api";
import { traceMeta } from "../../lib/conversation";
import { traceStepNodeId } from "../../lib/traceTree";

type Tab = "conv" | "blame";

export function TracePreview({ t, traceId }: { t: Theme; traceId: string | null }) {
  const [tab, setTab] = useState<Tab>("conv");
  const [activeStepIndex, setActiveStepIndex] = useState<number | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [scrollTargetNodeId, setScrollTargetNodeId] = useState<string | null>(null);
  const [viewportWidth, setViewportWidth] = useState(() => (
    typeof window === "undefined" ? 1440 : window.innerWidth
  ));
  const [treeDrawerOpen, setTreeDrawerOpen] = useState(false);
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
  const meta = trace ? traceMeta(trace) : null;
  const hasTree = (treeQ.data?.tree?.length ?? 0) > 0;
  const treeDocked = viewportWidth >= 1280;
  const firstStepIndex = trace?.steps?.length
    ? trace.steps[0]?.step_index ?? null
    : null;
  const firstStepNodeId = firstStepIndex != null ? traceStepNodeId(firstStepIndex) : null;
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
    if (treeDocked) setTreeDrawerOpen(false);
  }, [treeDocked, traceId]);

  return (
    <Panel n={5} label="Trace Preview" t={t} style={{ flex: 1, minHeight: 0 }}>
      {trace && meta ? (
        <>
          <div style={{
            padding: "0 18px 10px", fontFamily: F.code, fontSize: 11,
            display: "flex", flexWrap: "wrap", gap: 8, lineHeight: 1.8,
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
            display: "flex", gap: 16, padding: "0 18px",
            borderBottom: `1px solid ${t.border}`,
            fontFamily: F.code, fontSize: 12,
            alignItems: "center",
          }}>
            {(["conv", "blame"] as const).map((id) => (
              <span
                key={id}
                onClick={() => setTab(id)}
                style={{
                  padding: "8px 0", cursor: "pointer",
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
                onClick={() => setTreeDrawerOpen((current) => !current)}
                style={{
                  marginLeft: "auto",
                  border: `1px solid ${t.border}`,
                  background: treeDrawerOpen ? `${t.cyan}12` : t.bgAlt,
                  color: treeDrawerOpen ? t.text : t.textMuted,
                  fontFamily: F.code,
                  fontSize: 11,
                  padding: "4px 8px",
                  cursor: "pointer",
                }}
              >
                ☰ map
              </button>
            ) : null}
          </div>
          <div style={{
            flex: 1,
            minHeight: 0,
            overflow: tab === "conv" ? "hidden" : "auto",
            padding: "16px 18px",
          }}>
            {tab === "conv"
              ? (
                <div style={{ display: "flex", gap: 16, minHeight: "100%", height: "100%", position: "relative" }}>
                  {hasTree && treeDocked ? (
                    <TraceTree
                      t={t}
                      traceId={trace.trace_id}
                      roots={treeQ.data?.tree ?? []}
                      selectedNodeId={selectedNodeId}
                      activePathNodeId={activePathNodeId}
                      onSelectNode={(nodeId, stepIndex) => {
                        setSelectedNodeId(nodeId);
                        if (stepIndex == null) return;
                        setActiveStepIndex(stepIndex);
                        setScrollTargetNodeId(nodeId);
                      }}
                    />
                  ) : null}
                  <div style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
                    <ConversationView
                      t={t}
                      steps={trace.steps || []}
                      traceId={trace.trace_id}
                      activeStepIndex={activeStepIndex}
                      selectedNodeId={selectedNodeId}
                      scrollTargetNodeId={scrollTargetNodeId}
                      onScrollTargetConsumed={() => setScrollTargetNodeId(null)}
                      onActiveStepChange={(stepIndex) => {
                        setActiveStepIndex((current) => (current === stepIndex ? current : stepIndex));
                        setSelectedNodeId((current) => {
                          const stepNodeId = traceStepNodeId(stepIndex);
                          return current === stepNodeId ? current : stepNodeId;
                        });
                      }}
                    />
                  </div>
                  {hasTree && !treeDocked && treeDrawerOpen ? (
                    <>
                      <button
                        onClick={() => setTreeDrawerOpen(false)}
                        aria-label="Close conversation map"
                        style={{
                          position: "absolute",
                          inset: 0,
                          border: "none",
                          background: "rgba(0, 0, 0, 0.36)",
                          cursor: "pointer",
                          padding: 0,
                        }}
                      />
                      <div style={{
                        position: "absolute",
                        left: 0,
                        top: 0,
                        bottom: 0,
                        width: "min(420px, calc(100% - 24px))",
                        zIndex: 1,
                      }}>
                        <TraceTree
                          t={t}
                          traceId={trace.trace_id}
                          roots={treeQ.data?.tree ?? []}
                          selectedNodeId={selectedNodeId}
                          activePathNodeId={activePathNodeId}
                          onSelectNode={(nodeId, stepIndex) => {
                            setSelectedNodeId(nodeId);
                            if (stepIndex != null) {
                              setActiveStepIndex(stepIndex);
                              setScrollTargetNodeId(nodeId);
                            }
                            setTreeDrawerOpen(false);
                          }}
                          presentation="drawer"
                          onClose={() => setTreeDrawerOpen(false)}
                        />
                      </div>
                    </>
                  ) : null}
                </div>
              )
              : <InverseBlameView t={t} traceId={trace.trace_id} />}
          </div>
        </>
      ) : (
        <div style={{
          flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
          fontFamily: F.code, fontSize: 12, color: t.textMuted,
        }}>
          {traceId ? (q.isError ? "failed to load trace" : "loading…") : "no trace selected"}
        </div>
      )}
    </Panel>
  );
}
