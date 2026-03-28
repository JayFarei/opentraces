import { useState } from "react";
import { SelectionProvider, useSelection } from "./contexts/SelectionContext";
import { ViewPreferencesProvider } from "./contexts/ViewPreferencesContext";
import { Header } from "./components/layout/Header";
import { AppLayout } from "./components/layout/AppLayout";
import { ReviewBar } from "./components/layout/ReviewBar";
import { KeyboardHelp } from "./components/layout/KeyboardHelp";
import { SessionPanel } from "./components/sessions/SessionPanel";
import { TracePanel } from "./components/trace/TracePanel";
import { ContextFlow } from "./components/trace/ContextFlow";
import { DetailPanel } from "./components/detail/DetailPanel";
import { Onboarding } from "./components/Onboarding";
import { useSessionList } from "./hooks/useSessionList";
import { useTraceData } from "./hooks/useTraceData";
import { useKeyboardNav } from "./hooks/useKeyboardNav";

/** The context flow panel needs the tree from the selected session. */
function ContextPanel() {
  const { selectedSessionId } = useSelection();
  const { tree } = useTraceData(selectedSessionId);
  return <ContextFlow tree={tree} />;
}

function AppInner() {
  const { data: sessions, isLoading } = useSessionList();
  const { showHelp, setShowHelp } = useKeyboardNav();
  const [sampleMode, setSampleMode] = useState(false);

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
        contextPanel={<ContextPanel />}
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
