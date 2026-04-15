import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { Panel } from "../Panel";
import { ConversationView } from "./ConversationView";
import { InverseBlameView } from "./InverseBlame";
import { api } from "../../lib/api";
import { traceMeta } from "../../lib/conversation";

type Tab = "conv" | "blame";

export function TracePreview({ t, traceId }: { t: Theme; traceId: string | null }) {
  const [tab, setTab] = useState<Tab>("conv");
  const q = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => api.trace(traceId!),
    enabled: !!traceId,
  });

  const trace = q.data;
  const meta = trace ? traceMeta(trace) : null;

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
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "16px 18px" }}>
            {tab === "conv"
              ? <ConversationView t={t} steps={trace.steps || []} />
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
