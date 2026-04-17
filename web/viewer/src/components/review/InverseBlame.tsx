import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F, pctColor } from "../../tokens";
import { Label } from "../Panel";
import { api, type InverseBlame, type InverseBlameTier } from "../../lib/api";

type Commit = InverseBlame["commits"][number];

function tierCopy(tier: InverseBlameTier): string | null {
  if (tier === "tool_emitted") return "tool-emitted";
  if (tier === "tool_emitted_with_divergence") return "divergent";
  return null;
}

function tierColor(tier: InverseBlameTier, t: Theme): string {
  if (tier === "tool_emitted") return t.green;
  if (tier === "tool_emitted_with_divergence") return t.yellow;
  return t.textMuted;
}

function AttributedRow({ t, c }: { t: Theme; c: Commit }) {
  const hasLines = c.source === "attribution" && c.linesInCommit > 0;
  const pc = hasLines ? pctColor(parseInt(c.pct, 10) || 0, t) : t.textMuted;
  const tierLabel = c.tier ? tierCopy(c.tier) : null;
  return (
    <div style={{ marginBottom: 14, paddingLeft: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ color: pc, fontSize: 10 }}>●</span>
        <span style={{
          color: t.yellow, fontWeight: 500, textDecoration: "underline",
          textDecorationColor: `${t.yellow}40`, textUnderlineOffset: 2,
        }}>{c.id}</span>
        <span style={{ color: t.textSec }}>{c.msg || "(no subject)"}</span>
        {tierLabel && (
          <span style={{
            fontSize: 10, color: tierColor(c.tier, t), border: `1px solid ${tierColor(c.tier, t)}40`,
            padding: "1px 5px", borderRadius: 3, letterSpacing: 0.3,
          }}>{tierLabel}</span>
        )}
      </div>
      <div style={{ fontSize: 11, color: t.textMuted, paddingLeft: 18, marginTop: 2 }}>
        {hasLines
          ? `${c.linesInCommit} lines (${c.pct} of ${c.totalCommitLines} diff)`
          : c.totalCommitLines > 0
            ? `hook-linked · ${c.totalCommitLines} diff lines (no per-line data)`
            : "hook-linked · no per-line data"}
      </div>
    </div>
  );
}

export function InverseBlameView({ t, traceId }: { t: Theme; traceId: string }) {
  const q = useQuery({
    queryKey: ["inverse-blame", traceId],
    queryFn: () => api.inverseBlame(traceId),
  });

  const [showHookLinked, setShowHookLinked] = useState(false);

  const { attributed, hookLinked } = useMemo(() => {
    const commits = q.data?.commits ?? [];
    return {
      attributed: commits.filter(c => c.source === "attribution"),
      hookLinked: commits.filter(c => c.source === "git_links"),
    };
  }, [q.data]);

  if (q.isLoading) {
    return <div style={{ fontFamily: F.code, fontSize: 12, color: t.textMuted, padding: 4 }}>Loading…</div>;
  }
  if (q.isError || !q.data) {
    return <div style={{ fontFamily: F.code, fontSize: 12, color: t.textMuted, padding: 4 }}>No attribution data for this trace.</div>;
  }
  const data = q.data;
  if (data.commits.length === 0) {
    return <div style={{ fontFamily: F.code, fontSize: 12, color: t.textMuted, padding: 4 }}>This trace hasn't been attributed to any commit yet.</div>;
  }

  return (
    <div style={{ fontFamily: F.code, fontSize: 12, lineHeight: 1.7 }}>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
          <Label t={t} style={{ margin: 0 }}>TRACE</Label>
          <span style={{ color: t.cyan, fontWeight: 500 }}>{data.trace.id}</span>
          {data.trace.name && <span style={{ color: t.green, fontWeight: 600 }}>{data.trace.name}</span>}
        </div>
        <div style={{ fontSize: 11, color: t.textMuted }}>
          {data.trace.lines} attributed lines
          {data.trace.model ? ` · ${data.trace.model}` : ""}
          {" · "}
          {attributed.length} commit{attributed.length === 1 ? "" : "s"} with per-line attribution
          {hookLinked.length > 0 ? ` · +${hookLinked.length} hook-linked` : ""}
        </div>
      </div>

      {attributed.length > 0 && (
        <>
          <Label t={t} style={{ marginBottom: 8 }}>COMMITS WITH LINE-LEVEL ATTRIBUTION</Label>
          {attributed.map((c) => <AttributedRow key={c.sha} t={t} c={c} />)}
        </>
      )}

      {attributed.length === 0 && hookLinked.length === 0 && (
        <div style={{ color: t.textMuted, fontSize: 12 }}>No commits attributed.</div>
      )}

      {hookLinked.length > 0 && (
        <div style={{ marginTop: attributed.length > 0 ? 8 : 0 }}>
          <button
            onClick={() => setShowHookLinked(v => !v)}
            style={{
              fontFamily: F.code, fontSize: 11, color: t.textMuted,
              background: "none", border: `1px dashed ${t.border}`,
              padding: "4px 10px", borderRadius: 4, cursor: "pointer",
              letterSpacing: 0.3,
            }}
            title="Hook-linked commits carry evidence the agent's output text appeared in the commit's diff, but without per-line attribution. More likely to include false positives."
          >
            {showHookLinked ? "▾" : "▸"} {hookLinked.length} hook-linked commit{hookLinked.length === 1 ? "" : "s"} (no line counts)
          </button>
          {showHookLinked && (
            <div style={{ marginTop: 10 }}>
              {hookLinked.map((c) => <AttributedRow key={c.sha} t={t} c={c} />)}
            </div>
          )}
        </div>
      )}

      <div style={{ borderTop: `1px solid ${t.border}`, margin: "16px 0" }} />
      <Label t={t} style={{ marginBottom: 8 }}>FILES TOUCHED BY THIS TRACE</Label>
      <div style={{ fontSize: 11 }}>
        {data.files.length === 0 && (
          <div style={{ color: t.textMuted }}>(no file breakdown)</div>
        )}
        {data.files.map((f, i) => (
          <div key={i} style={{ display: "flex", gap: 12, padding: "3px 0", alignItems: "baseline" }}>
            <span style={{ color: t.textSec, flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{f.path}</span>
            <span style={{ color: t.cyan, whiteSpace: "nowrap" }}>{f.lines} lines</span>
          </div>
        ))}
      </div>
    </div>
  );
}
