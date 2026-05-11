import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Label } from "../Panel";
import { api } from "../../lib/api";

type DotKind = "ok" | "warn" | "bad" | "pending";

function Dot({ t, kind }: { t: Theme; kind: DotKind }) {
  const color = kind === "ok" ? t.green
    : kind === "warn" ? t.yellow
    : kind === "bad" ? t.red
    : t.textMuted;
  const glyph = kind === "ok" ? "✓" : "●";
  return <span style={{ color, marginRight: 6 }}>{glyph}</span>;
}

export function TraceSecurityModal({
  t, traceId, visibleStage, onClose,
}: {
  t: Theme;
  traceId: string;
  visibleStage: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "i") { e.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const traceQ = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => api.trace(traceId),
    staleTime: 10_000,
  });

  const trace = traceQ.data;
  const sec = trace?.security ?? {};
  const meta = trace?.metadata ?? {};
  const secMeta = meta.security ?? {};
  const tier1Residual = trace?._security_flags ?? [];
  const redactions = sec.redactions_applied ?? 0;
  const flagsReviewed = sec.flags_reviewed ?? 0;
  const regexScanned = !!sec.scanned;

  const thMarker = secMeta.trufflehog;
  const thFindings = secMeta.trufflehog_findings;
  const blockReason = (trace?.block_reason ?? "").toString().trim();
  const thFromState = blockReason.toLowerCase().startsWith("trufflehog");

  const lr = meta.llm_review ?? {};

  // Regex / entropy row
  let regexKind: DotKind = "pending";
  let regexText = "not scanned yet";
  if (tier1Residual.length > 0) {
    regexKind = "warn";
    regexText = `${tier1Residual.length} residual finding(s)`;
  } else if (redactions > 0) {
    regexKind = "ok";
    regexText = `${flagsReviewed} reviewed, ${redactions} auto-redacted`;
  } else if (regexScanned) {
    regexKind = "ok";
    regexText = "scanned, no findings";
  }

  // TruffleHog row — mirrors TUI logic
  let thKind: DotKind = "pending";
  let thText = "not run";
  let thHint: string | null = "opt-in: opentraces setup trufflehog";
  if (thFindings && thFindings.length > 0) {
    thKind = "bad";
    thText = `${thFindings.length} finding(s)`;
    thHint = null;
  } else if (thMarker?.status === "clean") {
    thKind = "ok";
    thText = "scanned, no findings";
    thHint = thMarker.version || null;
  } else if (thFromState) {
    thKind = "bad";
    const tail = blockReason.includes(":") ? blockReason.split(":", 2)[1]?.trim() : blockReason;
    thText = tail || "blocked";
    thHint = null;
  }

  // Manual review row
  let manualKind: DotKind = "pending";
  let manualText = "—";
  let manualHint: string | null = null;
  if (visibleStage === "inbox") {
    manualKind = "pending";
    manualText = "pending";
    manualHint = "press space to add to staged";
  } else if (visibleStage === "staged") {
    manualKind = "ok";
    manualText = "staged";
  } else if (visibleStage === "pushed") {
    manualKind = "ok";
    manualText = "pushed";
  }

  // LLM review row
  let lrKind: DotKind = "pending";
  let lrText = "not run";
  let lrHint: string | null = "opt-in: opentraces setup llm-review";
  const lrStatus = lr.status;
  const lrShareable = lr.shareable;
  const lrMissed = lr.missed_sensitive_data;
  if (lrStatus === "complete" && lrShareable === "yes" && lrMissed !== "yes") {
    lrKind = "ok";
    lrText = "shareable";
    lrHint = lr.model || null;
  } else if (lrStatus === "complete") {
    lrKind = "bad";
    lrText = "blocked";
    lrHint = `shareable=${lrShareable}, missed=${lrMissed}`;
  }

  const taskPreview = (trace?.task?.description || "").slice(0, 60);
  const shortId = traceId.slice(0, 8);

  return (
    <div
      onClick={onClose}
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
          maxWidth: 560, width: "90%",
          fontFamily: F.code, fontSize: 12,
        }}
      >
        <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
          Security pipeline
        </div>
        <div style={{ color: t.textDim, fontSize: 11, marginBottom: 16 }}>
          <span style={{ color: t.accent }}>{shortId}</span>
          {taskPreview && <span>  {taskPreview}</span>}
        </div>
        {traceQ.isLoading ? (
          <div style={{ color: t.textMuted }}>Loading…</div>
        ) : traceQ.isError ? (
          <div style={{ color: t.red }}>Failed to load trace detail.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10, color: t.textSec, lineHeight: 1.7 }}>
            <div>
              <Label t={t} style={{ marginBottom: 4 }}>TIER 1 · REGEX + ENTROPY</Label>
              <div><Dot t={t} kind={regexKind} />{regexText}</div>
            </div>
            <div>
              <Label t={t} style={{ marginBottom: 4 }}>TIER 1.5 · TRUFFLEHOG</Label>
              <div>
                <Dot t={t} kind={thKind} />{thText}
                {thHint && <span style={{ color: t.textDim }}> — {thHint}</span>}
              </div>
            </div>
            <div>
              <Label t={t} style={{ marginBottom: 4 }}>MANUAL REVIEW</Label>
              <div>
                <Dot t={t} kind={manualKind} />{manualText}
                {manualHint && <span style={{ color: t.textDim }}> — {manualHint}</span>}
              </div>
            </div>
            <div>
              <Label t={t} style={{ marginBottom: 4 }}>TIER 2 · LLM REVIEW</Label>
              <div>
                <Dot t={t} kind={lrKind} />{lrText}
                {lrHint && <span style={{ color: t.textDim }}> — {lrHint}</span>}
              </div>
            </div>
          </div>
        )}
        <div style={{ color: t.textDim, fontSize: 11, marginTop: 20 }}>Esc or i to close</div>
      </div>
    </div>
  );
}
