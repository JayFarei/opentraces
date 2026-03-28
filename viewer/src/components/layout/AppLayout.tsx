import { Panel, Group, Separator } from "react-resizable-panels";
import { SessionSidebar } from "../sessions/SessionSidebar";
import { SessionHeader } from "./SessionHeader";
import { TraceView } from "../trace/TraceView";
import { DetailPanel } from "../detail/DetailPanel";

function ResizeHandle({ direction = "horizontal" }: { direction?: "horizontal" | "vertical" }) {
  const isHorizontal = direction === "horizontal";
  return (
    <Separator
      className={`group relative ${isHorizontal ? "w-[3px]" : "h-[3px]"} bg-transparent hover:bg-[var(--accent)] transition-colors duration-100`}
    />
  );
}

export function AppLayout() {
  return (
    <div className="flex-1 overflow-hidden">
      <Group orientation="horizontal" id="opentraces-main">
        {/* Session sidebar: 15%, narrow */}
        <Panel defaultSize={15} minSize={10} maxSize={25} id="sessions">
          <SessionSidebar />
        </Panel>
        <ResizeHandle />

        {/* Main content: trace hero + detail */}
        <Panel defaultSize={85} minSize={60} id="main-content">
          <div className="h-full flex flex-col overflow-hidden">
            {/* Session stats header */}
            <SessionHeader />

            {/* Trace + Detail vertical split */}
            <div className="flex-1 overflow-hidden">
              <Group orientation="vertical" id="opentraces-vertical">
                {/* Trace view: the hero */}
                <Panel defaultSize={70} minSize={30} id="trace-view">
                  <TraceView />
                </Panel>

                <ResizeHandle direction="vertical" />

                {/* Detail panel: below trace, always present */}
                <Panel defaultSize={30} minSize={10} id="detail-panel">
                  <DetailPanel />
                </Panel>
              </Group>
            </div>
          </div>
        </Panel>
      </Group>
    </div>
  );
}
