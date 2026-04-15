import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { RichText } from "../RichText";
import { toTurns } from "../../lib/conversation";
import type { TraceStep } from "../../lib/api";

/** Full, un-redacted conversation: user turns in green, agent turns in orange
 *  (accent), system turns dim. Reasoning, tool calls (formatted JSON), and
 *  tool results are all expanded inline in the agent turn that produced them.
 */
export function ConversationView({ t, steps }: { t: Theme; steps: TraceStep[] }) {
  const turns = toTurns(steps);
  return (
    <div>
      {turns.map((turn, i) => {
        const isUser = turn.role === "user";
        const isSystem = turn.role === "system";
        const roleColor = isUser ? t.green : isSystem ? t.textMuted : t.accent;
        const roleLabel = isUser ? "User" : isSystem ? "System" : "Agent";
        return (
          <div key={i} style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <span style={{ color: t.textDim }}>——</span>
              <span style={{ fontFamily: F.code, fontSize: 12, fontWeight: 700, color: roleColor }}>
                {roleLabel}
              </span>
              <span style={{ color: t.textDim }}>——</span>
            </div>
            <div style={{ paddingLeft: 2 }}>
              {turn.events.map((ev) => {
                if (ev.kind === "reasoning") {
                  return (
                    <div key={ev.id} style={{
                      marginBottom: 10, fontFamily: F.body, fontSize: 12,
                      color: t.textMuted, fontStyle: "italic",
                      borderLeft: `2px solid ${t.border}`, paddingLeft: 10,
                    }}>
                      <RichText text={ev.text} t={t} />
                    </div>
                  );
                }
                if (ev.kind === "tool_call") {
                  return (
                    <div key={ev.id} style={{ marginBottom: 10 }}>
                      <div style={{ fontFamily: F.code, fontSize: 11, color: t.cyan, marginBottom: 4 }}>
                        ▸ {ev.toolName}
                      </div>
                      <pre style={{
                        margin: 0, padding: "8px 10px",
                        fontFamily: F.code, fontSize: 11, color: t.textSec,
                        background: `${t.cyan}08`, border: `1px solid ${t.border}`,
                        whiteSpace: "pre-wrap", wordBreak: "break-all",
                        maxHeight: 260, overflow: "auto",
                      }}>{ev.text}</pre>
                    </div>
                  );
                }
                if (ev.kind === "tool_result") {
                  return (
                    <div key={ev.id} style={{ marginBottom: 10 }}>
                      <div style={{ fontFamily: F.code, fontSize: 11, color: ev.error ? t.red : t.textMuted, marginBottom: 4 }}>
                        {ev.error ? "✗" : "◂"} {ev.toolName || "result"}{ev.error ? ` — ${ev.error}` : ""}
                      </div>
                      <pre style={{
                        margin: 0, padding: "8px 10px",
                        fontFamily: F.code, fontSize: 11, color: t.textMuted,
                        background: t.bgAlt, border: `1px solid ${t.border}`,
                        whiteSpace: "pre-wrap", wordBreak: "break-all",
                        maxHeight: 260, overflow: "auto",
                      }}>{ev.text || "(empty)"}</pre>
                    </div>
                  );
                }
                // user / agent_text / system
                return (
                  <div key={ev.id} style={{
                    fontFamily: F.body, fontSize: 13, color: t.textSec,
                    lineHeight: 1.65, marginBottom: 8,
                  }}>
                    <RichText text={ev.text} t={t} />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
