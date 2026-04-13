import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SelectionProvider } from "./contexts/SelectionContext";
import { ViewPreferencesProvider } from "./contexts/ViewPreferencesContext";
import { Header } from "./components/layout/Header";
import { AppLayout } from "./components/layout/AppLayout";
import { ReviewBar } from "./components/layout/ReviewBar";
import { KeyboardHelp } from "./components/layout/KeyboardHelp";
import { Onboarding } from "./components/Onboarding";
import { useTraceList } from "./hooks/useTraceList";
import { useKeyboardNav } from "./hooks/useKeyboardNav";
import { SAMPLE_TRACES, SAMPLE_CONTEXT, buildSampleTrace } from "./lib/sample-data";

function AppInner() {
  const { data: traces, isLoading, isError } = useTraceList();
  const { showHelp, setShowHelp } = useKeyboardNav();
  const [sampleMode, setSampleMode] = useState(false);
  const qc = useQueryClient();

  const loadSample = useCallback(() => {
    // Seed react-query cache with sample data so all hooks just work
    qc.setQueryData(["traces"], SAMPLE_TRACES);
    qc.setQueryData(["app-context"], SAMPLE_CONTEXT);
    for (const trace of SAMPLE_TRACES) {
      qc.setQueryData(["trace", trace.trace_id], buildSampleTrace(trace));
    }
    setSampleMode(true);
  }, [qc]);

  // Show onboarding when: still loading, errored (API not running), or genuinely empty.
  // Once sampleMode is on, always show the app layout.
  const hasTraces = sampleMode || (traces != null && traces.length > 0);
  const showOnboarding = !hasTraces && (isLoading || isError || traces?.length === 0);

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
