import { useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F } from "../../tokens";
import { api } from "../../lib/api";

export function PushModal({
  t, onClose, remote,
}: { t: Theme; onClose: () => void; remote: string | null }) {
  const tracesQ = useQuery({ queryKey: ["traces"], queryFn: api.traces });
  const stagedCount = (tracesQ.data ?? []).filter((x) => x._stage === "staged").length;

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "Enter") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const suffix = stagedCount === 1 ? "" : "s";

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
          maxWidth: 540, width: "90%",
          fontFamily: F.code, fontSize: 13,
        }}
      >
        <div style={{ color: t.text, fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
          Dataset publication replaces trace push
        </div>
        <div style={{ color: t.cyan, fontSize: 12, marginBottom: 20 }}>
          remote&nbsp;&nbsp;<span style={{ color: t.textSec }}>{remote || "not set"}</span>
        </div>

        <div style={{
          display: "grid", gap: 10, padding: "14px 16px", marginBottom: 18,
          border: `1px solid ${t.border}`, background: t.bgAlt,
        }}>
          <div data-testid="staged-count" style={{ color: t.textSec }}>
            <span style={{ color: t.cyan }}>{stagedCount}</span> staged trace{suffix} can be included
            when a dataset workflow selects them from the bucket.
          </div>
          <Command t={t}>opentraces dataset run &lt;name&gt;</Command>
          <Command t={t}>opentraces dataset review &lt;name&gt;</Command>
          <Command t={t}>opentraces dataset publish &lt;name&gt;</Command>
        </div>

        <div style={{ color: t.textDim, fontSize: 11 }}>
          Legacy trace push is disabled in this flow. Esc or Enter to close.
        </div>
      </div>
    </div>
  );
}

function Command({ t, children }: { t: Theme; children: ReactNode }) {
  return (
    <code style={{
      display: "block", color: t.text, fontFamily: F.code, fontSize: 12,
      padding: "7px 9px", border: `1px solid ${t.border}`, background: t.panelBg,
    }}>
      {children}
    </code>
  );
}
