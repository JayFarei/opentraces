import { Panel, Group, Separator } from "react-resizable-panels";
import type { ReactNode } from "react";

function ResizeHandle({ direction = "horizontal" }: { direction?: "horizontal" | "vertical" }) {
  const isHorizontal = direction === "horizontal";
  return (
    <Separator
      className={`group relative ${isHorizontal ? "w-[3px]" : "h-[3px]"} bg-transparent hover:bg-[var(--accent)] transition-colors duration-100`}
    />
  );
}

interface AppLayoutProps {
  sessionsPanel: ReactNode;
  tracePanel: ReactNode;
  timelineStrip: ReactNode;
  detailPanel: ReactNode;
}

export function AppLayout({ sessionsPanel, tracePanel, timelineStrip, detailPanel }: AppLayoutProps) {
  return (
    <div className="flex-1 overflow-hidden">
      <Group orientation="horizontal" id="opentraces-main">
        <Panel defaultSize={20} minSize={15} id="sessions">
          {sessionsPanel}
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={25} minSize={18} id="trace">
          {tracePanel}
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={55} minSize={30} id="right">
          <Group orientation="vertical" id="opentraces-right">
            <Panel defaultSize={25} minSize={15} id="timeline">
              {timelineStrip}
            </Panel>
            <ResizeHandle direction="vertical" />
            <Panel defaultSize={75} id="detail">
              {detailPanel}
            </Panel>
          </Group>
        </Panel>
      </Group>
    </div>
  );
}
