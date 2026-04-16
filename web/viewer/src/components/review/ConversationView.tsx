import { useEffect, useEffectEvent, useRef } from "react";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { RichText } from "../RichText";
import type { TraceStep } from "../../lib/api";
import {
  conversationAnchorId,
  traceObservationNodeId,
  traceStepNodeId,
  traceSubagentNodeId,
  traceToolCallNodeId,
} from "../../lib/traceTree";

const PROGRAMMATIC_SCROLL_IDLE_MS = 160;

function sortedStepNodes(stepRefs: Map<number, HTMLDivElement>): Array<[number, HTMLDivElement]> {
  return [...stepRefs.entries()].sort((a, b) => a[0] - b[0]);
}

export function pickActiveConversationStep(
  container: HTMLElement,
  stepRefs: Map<number, HTMLDivElement>,
): number | null {
  const nodes = sortedStepNodes(stepRefs);
  if (nodes.length === 0) return null;

  const containerRect = container.getBoundingClientRect();
  const focusY = containerRect.top + containerRect.height / 2;
  let bestVisible: { stepIndex: number; overlap: number; distance: number } | null = null;
  let nearest: { stepIndex: number; distance: number } | null = null;

  for (const [stepIndex, node] of nodes) {
    const rect = node.getBoundingClientRect();
    const overlap = Math.min(rect.bottom, containerRect.bottom) - Math.max(rect.top, containerRect.top);
    const visible = overlap > 4;
    if (!visible) continue;

    const center = (rect.top + rect.bottom) / 2;
    const distance = Math.abs(center - focusY);
    if (!bestVisible || overlap > bestVisible.overlap || (overlap === bestVisible.overlap && distance < bestVisible.distance)) {
      bestVisible = { stepIndex, overlap, distance };
    }
    if (!nearest || distance < nearest.distance) nearest = { stepIndex, distance };
  }

  return bestVisible?.stepIndex ?? nearest?.stepIndex ?? nodes[0]?.[0] ?? null;
}

export function ConversationView({
  t,
  steps,
  traceId,
  activeStepIndex,
  selectedNodeId,
  scrollTargetNodeId,
  onScrollTargetConsumed,
  onActiveStepChange,
}: {
  t: Theme;
  steps: TraceStep[];
  traceId: string;
  activeStepIndex: number | null;
  selectedNodeId: string | null;
  scrollTargetNodeId: string | null;
  onScrollTargetConsumed: () => void;
  onActiveStepChange: (stepIndex: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stepRefs = useRef(new Map<number, HTMLDivElement>());
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const programmaticScrollActive = useRef(false);
  const programmaticScrollIdleTimer = useRef<number | null>(null);

  const clearProgrammaticScrollIdleTimer = () => {
    if (programmaticScrollIdleTimer.current == null) return;
    window.clearTimeout(programmaticScrollIdleTimer.current);
    programmaticScrollIdleTimer.current = null;
  };

  const markProgrammaticScrollIdle = useEffectEvent(() => {
    clearProgrammaticScrollIdleTimer();
    programmaticScrollIdleTimer.current = window.setTimeout(() => {
      programmaticScrollActive.current = false;
      programmaticScrollIdleTimer.current = null;
    }, PROGRAMMATIC_SCROLL_IDLE_MS);
  });

  const registerNodeRef = (nodeId: string, stepIndex?: number) => (node: HTMLDivElement | null) => {
    if (node) nodeRefs.current.set(nodeId, node);
    else nodeRefs.current.delete(nodeId);

    if (stepIndex == null) return;
    if (node) stepRefs.current.set(stepIndex, node);
    else stepRefs.current.delete(stepIndex);
  };

  const syncActiveStep = useEffectEvent(() => {
    const container = containerRef.current;
    if (!container) return;
    if (programmaticScrollActive.current) return;

    const nextStepIndex = pickActiveConversationStep(container, stepRefs.current);
    if (nextStepIndex != null) onActiveStepChange(nextStepIndex);
  });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    programmaticScrollActive.current = false;
    clearProgrammaticScrollIdleTimer();
    container.scrollTop = 0;
  }, [traceId]);

  useEffect(() => {
    if (!scrollTargetNodeId) return;
    const target = nodeRefs.current.get(scrollTargetNodeId);
    if (!target) return;

    programmaticScrollActive.current = true;
    markProgrammaticScrollIdle();
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    onScrollTargetConsumed();
  }, [markProgrammaticScrollIdle, onScrollTargetConsumed, scrollTargetNodeId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let rafId = 0;
    const scheduleSync = () => {
      if (programmaticScrollActive.current) {
        markProgrammaticScrollIdle();
        return;
      }
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        syncActiveStep();
      });
    };

    scheduleSync();
    container.addEventListener("scroll", scheduleSync, { passive: true });
    window.addEventListener("resize", scheduleSync);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      clearProgrammaticScrollIdleTimer();
      container.removeEventListener("scroll", scheduleSync);
      window.removeEventListener("resize", scheduleSync);
    };
  }, [markProgrammaticScrollIdle, steps.length]);

  return (
    <div
      ref={containerRef}
      data-testid="conversation-scroll"
      style={{ height: "100%", overflow: "auto", paddingRight: 8 }}
    >
      {(steps || []).map((step) => {
        const isUser = step.role === "user";
        const isSystem = step.role === "system";
        const roleColor = isUser ? t.green : isSystem ? t.textMuted : t.accent;
        const roleLabel = isUser ? "User" : isSystem ? "System" : "Agent";
        const stepNodeId = traceStepNodeId(step.step_index);
        const stepSelected = activeStepIndex === step.step_index;
        return (
          <div
            key={step.step_index}
            id={conversationAnchorId(stepNodeId)}
            ref={registerNodeRef(stepNodeId, step.step_index)}
            data-step-index={step.step_index}
            style={{
              marginBottom: 24,
              padding: "8px 10px",
              background: stepSelected ? `${t.cyan}10` : "transparent",
              borderLeft: `2px solid ${stepSelected ? t.cyan : t.border}`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <span style={{ color: t.textDim }}>——</span>
              <span style={{ fontFamily: F.code, fontSize: 12, fontWeight: 700, color: roleColor }}>
                {roleLabel} · s{step.step_index}
              </span>
              <span style={{ color: t.textDim }}>——</span>
            </div>
            {step.reasoning_content ? (
              <div style={{
                marginBottom: 10, fontFamily: F.body, fontSize: 12,
                color: t.textMuted, fontStyle: "italic",
                borderLeft: `2px solid ${t.border}`, paddingLeft: 10,
              }}>
                <RichText text={step.reasoning_content} t={t} />
              </div>
            ) : null}
            {step.content ? (
              <div style={{
                fontFamily: F.body, fontSize: 13, color: t.textSec,
                lineHeight: 1.65, marginBottom: 8,
              }}>
                <RichText text={step.content} t={t} />
              </div>
            ) : null}
            {(step.tool_calls ?? []).map((toolCall, index) => {
              const nodeId = traceToolCallNodeId(step.step_index, index);
              const selected = selectedNodeId === nodeId;
              return (
                <div
                  key={nodeId}
                  id={conversationAnchorId(nodeId)}
                  ref={registerNodeRef(nodeId)}
                  style={{
                    marginBottom: 10,
                    padding: selected ? "6px 8px" : 0,
                    background: selected ? `${t.cyan}10` : "transparent",
                    borderLeft: `2px solid ${selected ? t.cyan : "transparent"}`,
                  }}
                >
                <div style={{ fontFamily: F.code, fontSize: 11, color: t.cyan, marginBottom: 4 }}>
                  ▸ {toolCall.tool_name}
                </div>
                <pre style={{
                  margin: 0, padding: "8px 10px",
                  fontFamily: F.code, fontSize: 11, color: t.textSec,
                  background: `${t.cyan}08`, border: `1px solid ${t.border}`,
                  whiteSpace: "pre-wrap", wordBreak: "break-all",
                  maxHeight: 260, overflow: "auto",
                }}>{JSON.stringify(toolCall.input, null, 2)}</pre>
              </div>
              );
            })}
            {(step.observations ?? []).map((observation, index) => {
              const nodeId = traceObservationNodeId(step.step_index, index);
              const selected = selectedNodeId === nodeId;
              return (
                <div
                  key={nodeId}
                  id={conversationAnchorId(nodeId)}
                  ref={registerNodeRef(nodeId)}
                  style={{
                    marginBottom: 10,
                    padding: selected ? "6px 8px" : 0,
                    background: selected ? `${t.cyan}10` : "transparent",
                    borderLeft: `2px solid ${selected ? t.cyan : "transparent"}`,
                  }}
                >
                <div style={{ fontFamily: F.code, fontSize: 11, color: observation.error ? t.red : t.textMuted, marginBottom: 4 }}>
                  {observation.error ? "✗" : "◂"} {observation.tool_name || "result"}{observation.error ? ` — ${observation.error}` : ""}
                </div>
                <pre style={{
                  margin: 0, padding: "8px 10px",
                  fontFamily: F.code, fontSize: 11, color: t.textMuted,
                  background: t.bgAlt, border: `1px solid ${t.border}`,
                  whiteSpace: "pre-wrap", wordBreak: "break-all",
                  maxHeight: 260, overflow: "auto",
                }}>{observation.content || observation.output_summary || "(empty)"}</pre>
              </div>
              );
            })}
            {step.subagent_trajectory_ref ? (() => {
              const nodeId = traceSubagentNodeId(step.step_index);
              const selected = selectedNodeId === nodeId;
              return (
                <div
                  id={conversationAnchorId(nodeId)}
                  ref={registerNodeRef(nodeId)}
                  style={{
                    marginBottom: 10,
                    padding: selected ? "6px 8px" : 0,
                    background: selected ? `${t.cyan}10` : "transparent",
                    borderLeft: `2px solid ${selected ? t.cyan : "transparent"}`,
                  }}
                >
                  <div style={{
                    fontFamily: F.code,
                    fontSize: 11,
                    color: t.textMuted,
                    border: `1px solid ${t.border}`,
                    background: t.bgAlt,
                    padding: "8px 10px",
                  }}>
                    ↳ subagent {step.subagent_trajectory_ref.slice(0, 8)}
                  </div>
                </div>
              );
            })() : null}
          </div>
        );
      })}
    </div>
  );
}
