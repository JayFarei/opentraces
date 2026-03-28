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
  contextPanel: ReactNode;
  detailPanel: ReactNode;
}

export function AppLayout({ sessionsPanel, tracePanel, contextPanel, detailPanel }: AppLayoutProps) {
  return (
    <div className="flex-1 overflow-hidden">
      <Group orientation="horizontal" id="opentraces-main">
        <Panel defaultSize={20} minSize={15} id="sessions">
          {sessionsPanel}
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={30} minSize={20} id="trace">
          {tracePanel}
        </Panel>
        <ResizeHandle />
        <Panel defaultSize={50} minSize={30} id="right">
          <Group orientation="vertical" id="opentraces-right">
            <Panel defaultSize={30} minSize={15} id="context">
              {contextPanel}
            </Panel>
            <ResizeHandle direction="vertical" />
            <Panel defaultSize={70} id="detail">
              {detailPanel}
            </Panel>
          </Group>
        </Panel>
      </Group>
    </div>
  );
}
