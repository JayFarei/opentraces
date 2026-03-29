import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SelectionProvider } from "./contexts/SelectionContext";
import { ViewPreferencesProvider } from "./contexts/ViewPreferencesContext";
import { Header } from "./components/layout/Header";
import { AppLayout } from "./components/layout/AppLayout";
import { ReviewBar } from "./components/layout/ReviewBar";
import { KeyboardHelp } from "./components/layout/KeyboardHelp";
import { Onboarding } from "./components/Onboarding";
import { useSessionList } from "./hooks/useSessionList";
import { useKeyboardNav } from "./hooks/useKeyboardNav";
import { SAMPLE_SESSIONS, SAMPLE_CONTEXT, buildSampleTrace } from "./lib/sample-data";

function AppInner() {
  const { data: sessions, isLoading, isError } = useSessionList();
  const { showHelp, setShowHelp } = useKeyboardNav();
  const [sampleMode, setSampleMode] = useState(false);
  const qc = useQueryClient();

  const loadSample = useCallback(() => {
    // Seed react-query cache with sample data so all hooks just work
    qc.setQueryData(["sessions"], SAMPLE_SESSIONS);
    qc.setQueryData(["app-context"], SAMPLE_CONTEXT);
    for (const session of SAMPLE_SESSIONS) {
      qc.setQueryData(["trace", session.trace_id], buildSampleTrace(session));
    }
    setSampleMode(true);
  }, [qc]);

  // Show onboarding when: still loading, errored (API not running), or genuinely empty.
  // Once sampleMode is on, always show the app layout.
  const hasSessions = sampleMode || (sessions != null && sessions.length > 0);
  const showOnboarding = !hasSessions && (isLoading || isError || sessions?.length === 0);

  return (
    <div className="h-screen flex flex-col bg-[var(--bg)]">
      <Header />
      {showOnboarding ? (
        <Onboarding onLoadSample={loadSample} />
      ) : (
        <>
          <AppLayout />
          <ReviewBar />
        </>
      )}
      {showHelp && <KeyboardHelp onDismiss={() => setShowHelp(false)} />}
    </div>
  );
}

export function App() {
  return (
    <SelectionProvider>
      <ViewPreferencesProvider>
        <AppInner />
      </ViewPreferencesProvider>
    </SelectionProvider>
  );
}
