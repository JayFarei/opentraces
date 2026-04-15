import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F, pctColor } from "../../tokens";
import { Label } from "../Panel";
import { api } from "../../lib/api";

export function InverseBlameView({ t, traceId }: { t: Theme; traceId: string }) {
  const q = useQuery({
    queryKey: ["inverse-blame", traceId],
    queryFn: () => api.inverseBlame(traceId),
  });

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
          {data.trace.lines} total lines{data.trace.model ? ` · ${data.trace.model}` : ""} · contributed to {data.commits.length} commit{data.commits.length === 1 ? "" : "s"}
        </div>
      </div>

      <Label t={t} style={{ marginBottom: 8 }}>COMMITS ATTRIBUTED</Label>
      {data.commits.map((c) => {
        const pc = pctColor(parseInt(c.pct, 10) || 0, t);
        return (
          <div key={c.sha} style={{ marginBottom: 14, paddingLeft: 8 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <span style={{ color: pc, fontSize: 10 }}>●</span>
              <span style={{
                color: t.yellow, fontWeight: 500, textDecoration: "underline",
                textDecorationColor: `${t.yellow}40`, textUnderlineOffset: 2,
              }}>{c.id}</span>
              <span style={{ color: t.textSec }}>{c.msg || "(no subject)"}</span>
            </div>
            <div style={{ fontSize: 11, color: t.textMuted, paddingLeft: 18, marginTop: 2 }}>
              {c.linesInCommit} lines in this commit ({c.pct} of {c.totalCommitLines} total)
            </div>
          </div>
        );
      })}

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
