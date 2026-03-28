import { useState } from "react";
import { SelectionProvider } from "./contexts/SelectionContext";
import { ViewPreferencesProvider } from "./contexts/ViewPreferencesContext";
import { Header } from "./components/layout/Header";
import { AppLayout } from "./components/layout/AppLayout";
import { ReviewBar } from "./components/layout/ReviewBar";
import { KeyboardHelp } from "./components/layout/KeyboardHelp";
import { SessionPanel } from "./components/sessions/SessionPanel";
import { TracePanel } from "./components/trace/TracePanel";
import { TimelineStrip } from "./components/trace/TimelineStrip";
import { DetailPanel } from "./components/detail/DetailPanel";
import { Onboarding } from "./components/Onboarding";
import { useSessionList } from "./hooks/useSessionList";
import { useKeyboardNav } from "./hooks/useKeyboardNav";

function AppInner() {
  const { data: sessions, isLoading } = useSessionList();
  const { showHelp, setShowHelp } = useKeyboardNav();
  const [sampleMode, setSampleMode] = useState(false);

  // Show onboarding when no sessions and not in sample mode
  const isEmpty = !isLoading && (!sessions || sessions.length === 0) && !sampleMode;

  if (isEmpty) {
    return (
      <>
        <Onboarding onLoadSample={() => setSampleMode(true)} />
        {showHelp && <KeyboardHelp onDismiss={() => setShowHelp(false)} />}
      </>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-[var(--bg)]">
      <Header />
      <AppLayout
        sessionsPanel={<SessionPanel />}
        tracePanel={<TracePanel />}
        timelineStrip={<TimelineStrip />}
        detailPanel={<DetailPanel />}
      />
      <ReviewBar />
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
