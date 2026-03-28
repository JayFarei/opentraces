import { useState } from "react";
import { Panel, Group, Separator } from "react-resizable-panels";
import { SessionSidebar } from "../sessions/SessionSidebar";
import { SessionHeader } from "./SessionHeader";
import { TraceView } from "../trace/TraceView";
import { DetailPanel } from "../detail/DetailPanel";

export function AppLayout() {
  const [sidebarWidth, setSidebarWidth] = useState(240);

  return (
    <div className="flex-1 overflow-hidden flex">
      {/* Session sidebar: fixed width with drag handle */}
      <div
        className="h-full flex-none overflow-hidden border-r border-[var(--border)]"
        style={{ width: `${String(sidebarWidth)}px` }}
      >
        <SessionSidebar />
      </div>

      {/* Resize handle with visual affordance */}
      <div
        className="w-[5px] flex-none cursor-col-resize bg-[var(--border)] hover:bg-[var(--accent)] transition-colors duration-100"
        onMouseDown={(e) => {
          e.preventDefault();
          const startX = e.clientX;
          const startWidth = sidebarWidth;
          const onMove = (ev: MouseEvent) => {
            const newWidth = Math.max(180, Math.min(400, startWidth + ev.clientX - startX));
            setSidebarWidth(newWidth);
          };
          const onUp = () => {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
          };
          document.addEventListener("mousemove", onMove);
          document.addEventListener("mouseup", onUp);
        }}
      />

      {/* Main content: trace hero + detail */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Session stats header */}
        <SessionHeader />

        {/* Trace + Detail vertical split */}
        <div className="flex-1 overflow-hidden">
          <Group orientation="vertical" id="opentraces-vertical" className="h-full">
            <Panel defaultSize={70} minSize={30} id="trace-view">
              <TraceView />
            </Panel>
            <Separator className="h-[3px] bg-transparent hover:bg-[var(--accent)] transition-colors duration-100 flex-none" />
            <Panel defaultSize={30} minSize={10} id="detail-panel">
              <DetailPanel />
            </Panel>
          </Group>
        </div>
      </div>
    </div>
  );
}
