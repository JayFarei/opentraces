import { useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F, trunc } from "../../tokens";
import { Panel } from "../Panel";
import { TracePreview } from "./TracePreview";
import { api } from "../../lib/api";
import type { TraceListItem } from "../../lib/api";
import { relativeAge } from "../../lib/conversation";

function classify(traces: TraceListItem[]) {
  const inbox: TraceListItem[] = [];
  const staged: TraceListItem[] = [];
  const pushed: TraceListItem[] = [];
  for (const t of traces) {
    if (t._stage === "staged") staged.push(t);
    else if (t._stage === "pushed") pushed.push(t);
    else if (t._stage === "inbox" || t._stage === "blocked") inbox.push(t);
  }
  return { inbox, staged, pushed };
}

export function ReviewView({
  t, wide, selectedId, setSelectedId, openPush, openInfo,
}: {
  t: Theme; wide: boolean;
  selectedId: string | null; setSelectedId: (id: string | null) => void;
  openPush: () => void; openInfo: () => void;
}) {
  const qc = useQueryClient();
  const tracesQ = useQuery({ queryKey: ["traces"], queryFn: api.traces });
  const ctxQ = useQuery({ queryKey: ["context"], queryFn: api.context });

  const { inbox, staged, pushed } = useMemo(
    () => classify(tracesQ.data ?? []),
    [tracesQ.data],
  );

  // The selected trace can live in inbox, staged, or pushed. j/k walks the
  // inbox, but space/r act on whichever trace is currently in the preview.
  const selectedTrace = useMemo(
    () => (tracesQ.data ?? []).find((x) => x.trace_id === selectedId) ?? null,
    [tracesQ.data, selectedId],
  );
  const inboxIdx = inbox.findIndex((i) => i.trace_id === selectedId);

  useEffect(() => {
    if (!selectedId && inbox.length > 0 && inbox[0]) setSelectedId(inbox[0].trace_id);
  }, [inbox, selectedId, setSelectedId]);

  const stageM = useMutation({
    mutationFn: (id: string) => api.addTrace(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["traces"] }),
  });
  const unstageM = useMutation({
    mutationFn: (id: string) => api.unstage(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["traces"] }),
  });
  const rejectM = useMutation({
    mutationFn: (id: string) => api.reject(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["traces"] }),
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        // Enter the inbox from whichever pane we're in; from inbox, step down.
        const start = inboxIdx < 0 ? -1 : inboxIdx;
        const n = Math.min(start + 1, inbox.length - 1);
        if (inbox[n]) setSelectedId(inbox[n].trace_id);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const start = inboxIdx < 0 ? 0 : inboxIdx;
        const n = Math.max(start - 1, 0);
        if (inbox[n]) setSelectedId(inbox[n].trace_id);
      } else if (e.key === " ") {
        e.preventDefault();
        // Toggle based on the previewed trace's current stage.
        if (!selectedTrace) return;
        if (selectedTrace._stage === "staged") unstageM.mutate(selectedTrace.trace_id);
        else if (selectedTrace._stage === "inbox" || selectedTrace._stage === "blocked") {
          stageM.mutate(selectedTrace.trace_id);
        }
      } else if (e.key === "p") {
        e.preventDefault(); openPush();
      } else if (e.key === "i") {
        e.preventDefault(); openInfo();
      } else if (e.key === "r") {
        e.preventDefault();
        if (selectedTrace) rejectM.mutate(selectedTrace.trace_id);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [inbox, inboxIdx, selectedTrace, setSelectedId, openPush, openInfo, stageM, unstageM, rejectM]);

  const ctx = ctxQ.data;
  const remote = ctx?.remote || "not set";
  const projectName = ctx?.project_name || "…";

  const left = (
    <div style={{
      width: wide ? 340 : "100%", minWidth: wide ? 340 : 0,
      display: "flex", flexDirection: "column", gap: 20, minHeight: 0,
      ...(wide ? {} : { marginBottom: 20 }),
    }}>
      <Panel n={1} label="Info" t={t}>
        <div style={{ padding: "0 14px 10px", fontFamily: F.code, fontSize: 12 }}>
          <div style={{ color: t.text }}>{projectName}</div>
          <div style={{ color: t.textMuted, fontSize: 11 }}>
            → <span style={{ color: t.cyan }}>{remote}</span>
            {ctx?.authenticated && ctx?.username && (
              <span style={{ color: t.textDim }}> ({ctx.username})</span>
            )}
          </div>
        </div>
      </Panel>

      <Panel n={2} label="Inbox" t={t} style={{ flex: 3 }}>
        <div style={{ flex: 1, overflow: "auto" }}>
          {inbox.length === 0 && (
            <div style={{ padding: "10px 14px", fontFamily: F.code, fontSize: 11, color: t.textMuted }}>
              (empty)
            </div>
          )}
          {inbox.map((item) => {
            const s = item.trace_id === selectedId;
            const blocked = item._stage === "blocked";
            const hasFlags = item.security_flags > 0;
            return (
              <div
                key={item.trace_id}
                onClick={() => setSelectedId(item.trace_id)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "6px 14px", cursor: "pointer",
                  background: s ? `${t.cyan}18` : "transparent",
                  borderLeft: s ? `2px solid ${t.cyan}` : "2px solid transparent",
                }}
              >
                <span style={{ color: blocked ? t.red : hasFlags ? t.yellow : t.textDim, fontSize: 8 }}>●</span>
                <span style={{
                  fontFamily: F.code, fontSize: 11,
                  color: s ? t.cyan : t.accent, minWidth: 58,
                }}>{item.trace_id.slice(0, 8)}</span>
                <span style={{
                  fontFamily: F.body, fontSize: 12, color: s ? t.text : t.textSec,
                  fontWeight: s ? 600 : 400, flex: 1, minWidth: 0,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>{trunc(item.task, wide ? 28 : 50)}</span>
                <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                  {relativeAge(item.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{
          padding: "6px 14px 8px", textAlign: "right",
          fontFamily: F.code, fontSize: 11, color: t.textDim,
        }}>
          {inbox.length === 0
            ? "— / 0"
            : inboxIdx < 0
              ? `— / ${inbox.length}`
              : `${inboxIdx + 1} / ${inbox.length}`}
        </div>
      </Panel>

      <Panel n={3} label="Staged" t={t} style={{ flex: 1 }}>
        <div style={{ flex: 1, overflow: "auto", padding: "0 14px 6px" }}>
          {staged.length === 0 && (
            <div style={{ fontFamily: F.code, fontSize: 11, color: t.textMuted, padding: "2px 0" }}>(empty)</div>
          )}
          {staged.map((s) => {
            const sel = s.trace_id === selectedId;
            return (
              <div
                key={s.trace_id}
                onClick={() => setSelectedId(s.trace_id)}
                style={{
                  display: "flex", gap: 10, padding: "4px 8px",
                  marginLeft: -8, marginRight: -6,
                  alignItems: "center", cursor: "pointer",
                  background: sel ? `${t.cyan}18` : "transparent",
                  borderLeft: sel ? `2px solid ${t.cyan}` : "2px solid transparent",
                }}
              >
                <span style={{ fontFamily: F.code, fontSize: 11, color: sel ? t.cyan : t.accent, minWidth: 58 }}>
                  {s.trace_id.slice(0, 8)}
                </span>
                <span style={{
                  fontSize: 12, color: sel ? t.text : t.textSec,
                  fontWeight: sel ? 600 : 400, flex: 1,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>{trunc(s.task, 24)}</span>
                <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                  {relativeAge(s.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "4px 14px 8px", textAlign: "right", fontFamily: F.code, fontSize: 11, color: t.textDim }}>
          {staged.length === 0 ? "— / 0" : `— / ${staged.length}`}
        </div>
      </Panel>

      <Panel n={4} label="Pushed" t={t} style={{ flex: 1 }}>
        <div style={{ flex: 1, overflow: "auto", padding: "0 14px 4px" }}>
          {pushed.length === 0 && (
            <div style={{ fontFamily: F.code, fontSize: 11, color: t.textMuted, padding: "2px 0" }}>(empty)</div>
          )}
          {pushed.map((s) => {
            const sel = s.trace_id === selectedId;
            return (
              <div
                key={s.trace_id}
                onClick={() => setSelectedId(s.trace_id)}
                style={{
                  display: "flex", gap: 10, padding: "4px 8px",
                  marginLeft: -8, marginRight: -6,
                  alignItems: "center", cursor: "pointer",
                  background: sel ? `${t.cyan}18` : "transparent",
                  borderLeft: sel ? `2px solid ${t.cyan}` : "2px solid transparent",
                }}
              >
                <span style={{ fontFamily: F.code, fontSize: 11, color: sel ? t.cyan : t.textMuted, minWidth: 58 }}>
                  {s.trace_id.slice(0, 8)}
                </span>
                <span style={{
                  fontSize: 12, color: sel ? t.text : t.textSec,
                  fontWeight: sel ? 600 : 400, flex: 1,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>{trunc(s.task, 24)}</span>
                <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                  {relativeAge(s.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "2px 14px 8px", textAlign: "right", fontFamily: F.code, fontSize: 11, color: t.textDim }}>
          {pushed.length === 0 ? "— / 0" : `— / ${pushed.length}`}
        </div>
      </Panel>

      {unstageM.isPending && null}
    </div>
  );

  const right = (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
      <TracePreview t={t} traceId={selectedId} />
    </div>
  );

  if (!wide) {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "18px 16px 8px", minHeight: 0, overflow: "auto" }}>
        {left}{right}
      </div>
    );
  }
  return (
    <div style={{ flex: 1, display: "flex", padding: "20px 16px 8px", gap: 16, minHeight: 0, overflow: "hidden" }}>
      {left}{right}
    </div>
  );
}
