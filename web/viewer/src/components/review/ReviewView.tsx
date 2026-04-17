import { useEffect, useMemo, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F, trunc } from "../../tokens";
import { Panel } from "../Panel";
import { TracePreview } from "./TracePreview";
import { TraceSecurityModal } from "../modals/TraceSecurityModal";
import { HelpModal } from "../modals/HelpModal";
import { api } from "../../lib/api";
import type { TraceListItem } from "../../lib/api";
import { isRecentlyTouched, relativeAge } from "../../lib/conversation";

function RowActionButton({
  t, label, title, color, visible, onClick,
}: {
  t: Theme; label: string; title: string; color: string;
  visible: boolean; onClick: (e: ReactMouseEvent) => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      title={title}
      aria-label={title}
      onClick={(e) => { e.stopPropagation(); onClick(e); }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `1px solid ${hover ? color : t.border}`,
        background: hover ? `${color}18` : "transparent",
        color: hover ? color : t.textMuted,
        fontFamily: F.code, fontSize: 11, lineHeight: 1,
        width: 18, height: 18, padding: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer",
        opacity: visible ? 1 : 0,
        transition: "opacity 120ms ease",
        flex: "0 0 auto",
      }}
    >{label}</button>
  );
}

function TraceMarker({ t, item }: { t: Theme; item: TraceListItem }) {
  const blocked = item._stage === "blocked";
  const hasFlags = item.security_flags > 0;
  if (blocked) return <span style={{ color: t.red, fontSize: 8 }}>●</span>;
  if (hasFlags) return <span style={{ color: t.yellow, fontSize: 8 }}>●</span>;
  if (isRecentlyTouched(item.timestamp)) {
    return <span style={{ color: t.cyan, opacity: 0.75, fontSize: 9 }}>◐</span>;
  }
  return <span style={{ color: t.textDim, fontSize: 8 }}>·</span>;
}

function GenerationBadge({ t, generation }: { t: Theme; generation?: number }) {
  const safeGeneration = generation ?? 0;
  if (safeGeneration <= 0) return null;
  const title = safeGeneration > 1
    ? "Later generation from the same session replacing an older trace"
    : "First captured trace for this session";
  return (
    <span
      title={title}
      style={{
        fontFamily: F.code,
        fontSize: 10,
        color: t.cyan,
        opacity: 0.75,
        flex: "0 0 auto",
      }}
    >
      ↑{safeGeneration}
    </span>
  );
}

function InboxRow({
  t, item, selected, wide, onSelect, onStage, onReject, onInfo,
}: {
  t: Theme; item: TraceListItem; selected: boolean; wide: boolean;
  onSelect: () => void; onStage: () => void; onReject: () => void;
  onInfo: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 14px", cursor: "pointer",
        background: selected ? `${t.cyan}18` : "transparent",
        borderLeft: selected ? `2px solid ${t.cyan}` : "2px solid transparent",
      }}
    >
      <TraceMarker t={t} item={item} />
      <span style={{
        fontFamily: F.code, fontSize: 11,
        color: selected ? t.cyan : t.accent, minWidth: 58,
      }}>{item.trace_id.slice(0, 8)}</span>
      <span style={{
        fontFamily: F.body, fontSize: 12, color: selected ? t.text : t.textSec,
        fontWeight: selected ? 600 : 400, flex: 1, minWidth: 0,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      }}>{trunc(item.task, wide ? 28 : 50)}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "0 0 auto" }}>
        {hover ? (
          <div style={{ display: "flex", gap: 4, flex: "0 0 auto" }}>
            <RowActionButton t={t} label="+" title="Stage" color={t.green} visible onClick={onStage} />
            <RowActionButton t={t} label="✕" title="Reject" color={t.red} visible onClick={onReject} />
            <RowActionButton t={t} label="i" title="Trace security" color={t.cyan} visible onClick={onInfo} />
          </div>
        ) : (
          <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
            {relativeAge(item.timestamp)}
          </span>
        )}
        <GenerationBadge t={t} generation={item.generation_index} />
      </div>
    </div>
  );
}

function StagedRow({
  t, item, selected, onSelect, onUnstage,
}: {
  t: Theme; item: TraceListItem; selected: boolean;
  onSelect: () => void; onUnstage: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={onSelect}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", gap: 10, padding: "4px 8px",
        marginLeft: -8, marginRight: -6,
        alignItems: "center", cursor: "pointer",
        background: selected ? `${t.cyan}18` : "transparent",
        borderLeft: selected ? `2px solid ${t.cyan}` : "2px solid transparent",
      }}
    >
      <TraceMarker t={t} item={item} />
      <span style={{ fontFamily: F.code, fontSize: 11, color: selected ? t.cyan : t.accent, minWidth: 58 }}>
        {item.trace_id.slice(0, 8)}
      </span>
      <span style={{
        fontSize: 12, color: selected ? t.text : t.textSec,
        fontWeight: selected ? 600 : 400, flex: 1, minWidth: 0,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      }}>{trunc(item.task, 24)}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "0 0 auto" }}>
        {hover ? (
          <RowActionButton t={t} label="−" title="Unstage" color={t.yellow} visible onClick={onUnstage} />
        ) : (
          <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
            {relativeAge(item.timestamp)}
          </span>
        )}
        <GenerationBadge t={t} generation={item.generation_index} />
      </div>
    </div>
  );
}

function PushButton({ t, count, onClick }: { t: Theme; count: number; onClick: () => void }) {
  const [hover, setHover] = useState(false);
  const enabled = count > 0;
  const color = enabled ? t.green : t.textDim;
  return (
    <button
      onClick={onClick}
      disabled={!enabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        border: `1px solid ${enabled && hover ? color : t.border}`,
        background: enabled && hover ? `${color}18` : "transparent",
        color, fontFamily: F.code, fontSize: 11, lineHeight: 1,
        padding: "3px 10px", cursor: enabled ? "pointer" : "default",
        letterSpacing: "0.04em",
      }}
    >push → {count}</button>
  );
}

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
  const [secModalTraceId, setSecModalTraceId] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  const { inbox, staged, pushed } = useMemo(
    () => classify(tracesQ.data ?? []),
    [tracesQ.data],
  );

  // j/k walks the inbox; space toggles the previewed trace's stage.
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
  const refreshM = useMutation({
    mutationFn: () => api.refresh(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["traces"] });
      qc.invalidateQueries({ queryKey: ["context"] });
    },
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "?") {
        e.preventDefault();
        setHelpOpen((current) => !current);
        return;
      }
      if (helpOpen || secModalTraceId) return;
      if (e.key === "r") {
        e.preventDefault();
        if (!refreshM.isPending) refreshM.mutate();
        return;
      }
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
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [
    helpOpen,
    inbox,
    inboxIdx,
    refreshM,
    secModalTraceId,
    selectedTrace,
    setSelectedId,
    stageM,
    unstageM,
  ]);

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
        <div style={{
          padding: "0 14px 10px", fontFamily: F.code, fontSize: 12,
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: t.text }}>{projectName}</div>
            <div style={{ color: t.textMuted, fontSize: 11 }}>
              → <span style={{ color: t.cyan }}>{remote}</span>
              {ctx?.authenticated && ctx?.username && (
                <span style={{ color: t.textDim }}> ({ctx.username})</span>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 4, flex: "0 0 auto" }}>
            <RowActionButton
              t={t}
              label={refreshM.isPending ? "…" : "↻"}
              title="Refresh inbox"
              color={t.green}
              visible
              onClick={() => { if (!refreshM.isPending) refreshM.mutate(); }}
            />
            <RowActionButton
              t={t} label="i" title="Security info"
              color={t.cyan} visible onClick={() => openInfo()}
            />
            <RowActionButton
              t={t} label="?" title="Review help"
              color={t.cyan} visible onClick={() => setHelpOpen(true)}
            />
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
          {inbox.map((item) => (
            <InboxRow
              key={item.trace_id}
              t={t}
              item={item}
              selected={item.trace_id === selectedId}
              wide={wide}
              onSelect={() => setSelectedId(item.trace_id)}
              onStage={() => stageM.mutate(item.trace_id)}
              onReject={() => rejectM.mutate(item.trace_id)}
              onInfo={() => setSecModalTraceId(item.trace_id)}
            />
          ))}
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
          {staged.map((s) => (
            <StagedRow
              key={s.trace_id}
              t={t}
              item={s}
              selected={s.trace_id === selectedId}
              onSelect={() => setSelectedId(s.trace_id)}
              onUnstage={() => unstageM.mutate(s.trace_id)}
            />
          ))}
        </div>
        <div style={{
          padding: "4px 14px 8px",
          fontFamily: F.code, fontSize: 11, color: t.textDim,
          display: "flex", alignItems: "center", gap: 8,
          borderTop: `1px solid ${t.border}`,
        }}>
          <span style={{ flex: 1 }}>
            {staged.length === 0 ? "— / 0" : `— / ${staged.length}`}
          </span>
          <PushButton t={t} count={staged.length} onClick={openPush} />
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
                <TraceMarker t={t} item={s} />
                <span style={{ fontFamily: F.code, fontSize: 11, color: sel ? t.cyan : t.textMuted, minWidth: 58 }}>
                  {s.trace_id.slice(0, 8)}
                </span>
                <span style={{
                  fontSize: 12, color: sel ? t.text : t.textSec,
                  fontWeight: sel ? 600 : 400, flex: 1,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                }}>{trunc(s.task, 24)}</span>
                <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "0 0 auto" }}>
                  <span style={{ fontFamily: F.code, fontSize: 10, color: t.textDim }}>
                    {relativeAge(s.timestamp)}
                  </span>
                  <GenerationBadge t={t} generation={s.generation_index} />
                </div>
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

  const secModalTrace = secModalTraceId
    ? (tracesQ.data ?? []).find((x) => x.trace_id === secModalTraceId)
    : null;
  const secModal = secModalTraceId ? (
    <TraceSecurityModal
      t={t}
      traceId={secModalTraceId}
      visibleStage={secModalTrace?._stage ?? "inbox"}
      onClose={() => setSecModalTraceId(null)}
    />
  ) : null;
  const helpModal = helpOpen ? (
    <HelpModal t={t} onClose={() => setHelpOpen(false)} />
  ) : null;

  if (!wide) {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "18px 16px 8px", minHeight: 0, overflow: "auto" }}>
        {left}{right}
        {secModal}
        {helpModal}
      </div>
    );
  }
  return (
    <div style={{ flex: 1, display: "flex", padding: "20px 16px 8px", gap: 16, minHeight: 0, overflow: "hidden" }}>
      {left}{right}
      {secModal}
      {helpModal}
    </div>
  );
}
