import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DARK, LIGHT, F } from "./tokens";
import { NavBar, type View } from "./components/NavBar";
import { ShortcutBar } from "./components/ShortcutBar";
import { ReviewView } from "./components/review/ReviewView";
import { GraphView } from "./components/graph/GraphView";
import { PushModal } from "./components/modals/PushModal";
import { SecurityInfoModal } from "./components/modals/SecurityInfoModal";
import { api } from "./lib/api";

type Mode = "dark" | "light";
type Modal = "push" | "info" | null;

function useViewportWidth() {
  const [w, setW] = useState(typeof window !== "undefined" ? window.innerWidth : 1200);
  useEffect(() => {
    const h = () => setW(window.innerWidth);
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);
  return w;
}

function createLifecycleClientId() {
  if (typeof window === "undefined") return "server";
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `ot-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function App() {
  const [mode, setMode] = useState<Mode>(() => {
    const stored = typeof localStorage !== "undefined" ? localStorage.getItem("ot-mode") : null;
    return stored === "light" ? "light" : "dark";
  });
  const [view, setView] = useState<View>("review");
  const [modal, setModal] = useState<Modal>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [closing, setClosing] = useState<"stopping" | "closed" | null>(null);
  const [clientId] = useState(createLifecycleClientId);
  const width = useViewportWidth();
  const wide = width >= 768;
  const t = mode === "dark" ? DARK : LIGHT;

  useEffect(() => {
    if (typeof localStorage !== "undefined") localStorage.setItem("ot-mode", mode);
    document.documentElement.setAttribute("data-theme", mode);
  }, [mode]);

  const ctxQ = useQuery({ queryKey: ["context"], queryFn: api.context });

  useEffect(() => {
    let disposed = false;

    const ping = () => {
      void api.lifecyclePing(clientId).catch(() => {});
    };
    const disconnect = () => {
      if (disposed) return;
      api.lifecycleDisconnect(clientId);
    };

    ping();
    const interval = window.setInterval(ping, 10000);
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("beforeunload", disconnect);

    return () => {
      disposed = true;
      window.clearInterval(interval);
      window.removeEventListener("pagehide", disconnect);
      window.removeEventListener("beforeunload", disconnect);
      api.lifecycleDisconnect(clientId);
    };
  }, [clientId]);

  // Global shortcuts not handled by the active view
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape" && modal) { setModal(null); return; }
      if (modal) return;
      if (e.key === "g" && view === "review") { /* top */ }
      if (e.key === "b" && view === "graph") { setView("review"); }
      if (e.key === "q") {
        e.preventDefault();
        if (closing) return;
        setClosing("stopping");
        void api.quit(clientId)
          .catch(() => {})
          .finally(() => {
            setClosing("closed");
            window.setTimeout(() => window.close(), 80);
          });
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [clientId, closing, modal, view]);

  if (closing) {
    return (
      <div style={{
        background: t.pageBg,
        color: t.text,
        fontFamily: F.body,
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
      }}>
        <div style={{
          maxWidth: 420,
          width: "100%",
          border: `1px solid ${t.border}`,
          background: t.panelBg,
          padding: 24,
          boxShadow: "0 24px 80px rgba(0, 0, 0, 0.28)",
        }}>
          <div style={{ fontFamily: F.code, color: t.cyan, fontSize: 12, marginBottom: 8 }}>
            {closing === "stopping" ? "Stopping local inbox…" : "Local inbox stopped."}
          </div>
          <div style={{ color: t.textSec }}>
            {closing === "stopping"
              ? "The browser requested shutdown of the local opentraces web server."
              : "You can close this tab now. Running `opentraces web` will start it again."}
          </div>
        </div>
      </div>
    );
  }

  const shortcuts: [string, string][] = view === "review"
    ? [
        ["j/k", "move"],
        ["space", "add/remove"],
        ["p", "push"],
        ["r", "reject"],
        ["g/G", "top/bot"],
        ["[/]", "page"],
        ["i", "sec info"],
        ["?", "help"],
        ["q", "quit"],
      ]
    : [
        ["j/k", "navigate"],
        ["enter", "blame"],
        ["b", "back"],
        ["f", "filter"],
        ["?", "help"],
        ["q", "quit"],
      ];

  const gotoTrace = (id: string) => {
    setSelectedId(id);
    setView("review");
  };

  return (
    <div style={{
      background: t.pageBg, color: t.textSec, fontFamily: F.body, fontSize: 13,
      lineHeight: 1.55, height: "100vh", display: "flex", flexDirection: "column",
      position: "relative", overflow: "hidden",
    }}>
      <NavBar t={t} view={view} setView={setView} mode={mode} setMode={setMode} />

      {view === "review" && (
        <ReviewView
          t={t} wide={wide}
          selectedId={selectedId} setSelectedId={setSelectedId}
          openPush={() => setModal("push")}
          openInfo={() => setModal("info")}
        />
      )}
      {view === "graph" && (
        <GraphView t={t} wide={wide} mode={mode} gotoTrace={gotoTrace} />
      )}

      <ShortcutBar t={t} items={shortcuts} />

      {modal === "push" && (
        <PushModal t={t} onClose={() => setModal(null)} remote={ctxQ.data?.remote ?? null} />
      )}
      {modal === "info" && (
        <SecurityInfoModal t={t} onClose={() => setModal(null)} remote={ctxQ.data?.remote ?? null} />
      )}
    </div>
  );
}
