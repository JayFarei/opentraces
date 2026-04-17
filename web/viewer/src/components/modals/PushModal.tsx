import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { api } from "../../lib/api";

type Mode = "llm" | "skip";

export function PushModal({
  t, onClose, remote,
}: { t: Theme; onClose: () => void; remote: string | null }) {
  const qc = useQueryClient();
  const tracesQ = useQuery({ queryKey: ["traces"], queryFn: api.traces });
  const stagedCount = (tracesQ.data ?? []).filter((x) => x._stage === "staged").length;
  const [mode, setMode] = useState<Mode | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  const push = useMutation({
    mutationFn: (m: Mode) => api.push({ llmReview: m === "llm" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["traces"] }),
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        if (push.isPending) return;
        onClose();
        return;
      }
      if (stagedCount === 0) return;
      if (mode !== null) return;
      if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        setMode("llm");
        push.mutate("llm");
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        setMode("skip");
        push.mutate("skip");
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [mode, onClose, push, stagedCount]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [push.data, push.error]);

  const n = stagedCount;
  const suffix = n === 1 ? "" : "s";

  const pick = (m: Mode) => { setMode(m); push.mutate(m); };

  return (
    <div
      onClick={() => { if (!push.isPending) onClose(); }}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: t.panelBg,
          border: `1px solid ${t.cyan}40`,
          boxShadow: `0 0 40px ${t.cyan}10, 0 8px 32px rgba(0,0,0,0.4)`,
          padding: "28px 36px",
          maxWidth: mode ? 680 : 480, width: "90%",
          fontFamily: F.code, fontSize: 13,
        }}
      >
        {mode === null ? (
          <SelectStage t={t} n={n} suffix={suffix} remote={remote} onPick={pick} />
        ) : (
          <RunnerStage
            t={t}
            mode={mode}
            pending={push.isPending}
            error={push.error as Error | null}
            data={push.data}
            onClose={onClose}
            logRef={logRef}
          />
        )}
      </div>
    </div>
  );
}

function SelectStage({
  t, n, suffix, remote, onPick,
}: {
  t: Theme; n: number; suffix: string; remote: string | null;
  onPick: (m: Mode) => void;
}) {
  return (
    <>
      <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
        Push <span style={{ color: t.cyan }}>{n}</span> staged trace{suffix}
      </div>
      <div style={{ color: t.cyan, fontSize: 12, marginBottom: 20 }}>
        remote&nbsp;&nbsp;<span style={{ color: t.textSec }}>{remote || "not set"}</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 20 }}>
        <OptionRow t={t} keyLabel="L" title="Local LLM review then push" cmd="opentraces push --llm-review" onClick={() => onPick("llm")} disabled={n === 0} />
        <OptionRow t={t} keyLabel="S" title="Skip review and push" cmd="opentraces push" onClick={() => onPick("skip")} disabled={n === 0} />
      </div>

      <div style={{ color: t.textDim, fontSize: 11 }}>
        {n === 0 ? "Nothing staged." : "Esc to cancel"}
      </div>
    </>
  );
}

function OptionRow({
  t, keyLabel, title, cmd, onClick, disabled,
}: {
  t: Theme; keyLabel: string; title: string; cmd: string;
  onClick: () => void; disabled?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const active = hover && !disabled;
  return (
    <div
      onClick={() => { if (!disabled) onClick(); }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", alignItems: "baseline", gap: 12, padding: "8px 12px",
        border: `1px solid ${active ? t.cyan : t.border}`,
        cursor: disabled ? "default" : "pointer",
        background: active ? `${t.cyan}10` : "transparent",
        opacity: disabled ? 0.4 : 1,
      }}
    >
      <span style={{ color: active ? t.cyan : t.text, fontWeight: 700, minWidth: 16 }}>{keyLabel}</span>
      <span style={{ color: t.text }}>{title}</span>
      <span style={{ color: t.textDim, fontSize: 11, marginLeft: "auto" }}>{cmd}</span>
    </div>
  );
}

function RunnerStage({
  t, mode, pending, error, data, onClose, logRef,
}: {
  t: Theme; mode: Mode; pending: boolean; error: Error | null;
  data: Awaited<ReturnType<typeof api.push>> | undefined;
  onClose: () => void; logRef: React.RefObject<HTMLPreElement | null>;
}) {
  const title = mode === "llm"
    ? "Running opentraces llm-review --scope staged → push --llm-review"
    : "Running opentraces push";
  const serverError = data?.error;
  const status = data?.status;
  const log = data?.log;
  const message = data?.message;
  const count = data?.count;

  const doneOk = !pending && !error && !serverError && status === "pushed";
  const doneFail = !pending && (error != null || serverError != null || status === "failed");

  return (
    <>
      <div style={{ color: t.text, fontWeight: 700, fontSize: 14, marginBottom: 12 }}>
        {title}
      </div>

      {pending && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 14px", marginBottom: 14,
          border: `1px solid ${t.cyan}40`, background: `${t.cyan}10`,
          color: t.cyan, fontSize: 12,
        }}>
          <Spinner color={t.cyan} />
          <span>Running… this can take a minute on first push.</span>
        </div>
      )}

      {doneOk && (
        <div style={{
          padding: "10px 14px", marginBottom: 14,
          border: `1px solid ${t.green}40`, background: `${t.green}10`,
          color: t.green, fontSize: 12,
        }}>
          ✓ Pushed {count ?? ""} trace{count === 1 ? "" : "s"}
          {message ? ` — ${message}` : ""}
        </div>
      )}

      {doneFail && (
        <div style={{
          padding: "10px 14px", marginBottom: 14,
          border: `1px solid ${t.red}40`, background: `${t.red}10`,
          color: t.red, fontSize: 12,
        }}>
          ✕ {error?.message || serverError || "Push failed"}
        </div>
      )}

      {log && (
        <pre
          ref={logRef}
          style={{
            margin: 0, padding: 12, maxHeight: 280, overflow: "auto",
            border: `1px solid ${t.border}`, background: t.bgAlt,
            color: t.textSec, fontFamily: F.code, fontSize: 11,
            whiteSpace: "pre-wrap", lineHeight: 1.5,
          }}
        >{log}</pre>
      )}

      <div style={{ color: t.textDim, fontSize: 11, marginTop: 12 }}>
        {pending ? "Please wait…" : (
          <button
            onClick={onClose}
            style={{
              border: `1px solid ${t.border}`, background: "transparent",
              color: t.textSec, fontFamily: F.code, fontSize: 11,
              padding: "4px 10px", cursor: "pointer",
            }}
          >Done — Esc to close</button>
        )}
      </div>
    </>
  );
}

function Spinner({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block", width: 10, height: 10,
        border: `2px solid ${color}40`, borderTop: `2px solid ${color}`,
        borderRadius: "50%", animation: "ot-spin 0.8s linear infinite",
      }}
    />
  );
}
