import { createContext, useContext, useState, useCallback, useEffect } from "react";
import type { ReactNode } from "react";

type TraceViewMode = "timeline" | "review";

interface ViewPreferences {
  showReasoning: boolean;
  showTimeline: boolean;
  theme: "dark" | "light";
  traceViewMode: TraceViewMode;
  toggleReasoning: () => void;
  toggleTimeline: () => void;
  toggleTheme: () => void;
  setTraceViewMode: (mode: TraceViewMode) => void;
}

const ViewPreferencesContext = createContext<ViewPreferences | null>(null);

function getInitialTheme(): "dark" | "light" {
  try {
    const stored = localStorage.getItem("opentraces-theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* ignore */
  }
  return "dark";
}

export function ViewPreferencesProvider({ children }: { children: ReactNode }) {
  const [showReasoning, setShowReasoning] = useState(true);
  const [showTimeline, setShowTimeline] = useState(true);
  const [theme, setTheme] = useState<"dark" | "light">(getInitialTheme);
  const [traceViewMode, setTraceViewMode] = useState<TraceViewMode>("timeline");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("opentraces-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggleReasoning = useCallback(() => setShowReasoning((v) => !v), []);
  const toggleTimeline = useCallback(() => setShowTimeline((v) => !v), []);
  const toggleTheme = useCallback(
    () => setTheme((v) => (v === "dark" ? "light" : "dark")),
    [],
  );

  return (
    <ViewPreferencesContext.Provider
      value={{ showReasoning, showTimeline, theme, traceViewMode, toggleReasoning, toggleTimeline, toggleTheme, setTraceViewMode }}
    >
      {children}
    </ViewPreferencesContext.Provider>
  );
}

export function useViewPreferences(): ViewPreferences {
  const ctx = useContext(ViewPreferencesContext);
  if (!ctx) throw new Error("useViewPreferences must be used within ViewPreferencesProvider");
  return ctx;
}
