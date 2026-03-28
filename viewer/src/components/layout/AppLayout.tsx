import { useState, useRef, useCallback } from "react";
import { SessionSidebar } from "../sessions/SessionSidebar";
import { SessionHeader } from "./SessionHeader";
import { TraceView } from "../trace/TraceView";
import { DetailPanel } from "../detail/DetailPanel";

export function AppLayout() {
  const [sidebarWidth, setSidebarWidth] = useState(240);
  const [detailHeight, setDetailHeight] = useState(220);
  const mainRef = useRef<HTMLDivElement>(null);

  const handleSidebarDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      setSidebarWidth(Math.max(180, Math.min(400, startWidth + ev.clientX - startX)));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sidebarWidth]);

  const handleDetailDrag = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = detailHeight;
    const onMove = (ev: MouseEvent) => {
      const containerHeight = mainRef.current?.getBoundingClientRect().height ?? 600;
      const maxHeight = containerHeight - 100;
      setDetailHeight(Math.max(80, Math.min(maxHeight, startHeight - (ev.clientY - startY))));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [detailHeight]);

  return (
    <div className="flex-1 overflow-hidden flex">
      {/* Session sidebar */}
      <div
        className="h-full flex-none overflow-hidden border-r border-[var(--border)]"
        style={{ width: `${String(sidebarWidth)}px` }}
      >
        <SessionSidebar />
      </div>

      {/* Sidebar resize handle */}
      <div
        className="w-[5px] flex-none cursor-col-resize bg-[var(--border)] hover:bg-[var(--accent)] transition-colors duration-100"
        onMouseDown={handleSidebarDrag}
      />

      {/* Main content: header + trace + detail */}
      <div ref={mainRef} className="flex-1 flex flex-col overflow-hidden min-w-0">
        <SessionHeader />

        {/* Trace view: takes remaining space minus detail */}
        <div className="flex-1 overflow-hidden min-h-0">
          <TraceView />
        </div>

        {/* Detail resize handle */}
        <div
          className="h-[5px] flex-none cursor-row-resize bg-[var(--border)] hover:bg-[var(--accent)] transition-colors duration-100"
          onMouseDown={handleDetailDrag}
        />

        {/* Detail panel: fixed height from bottom */}
        <div
          className="flex-none overflow-hidden"
          style={{ height: `${String(detailHeight)}px` }}
        >
          <DetailPanel />
        </div>
      </div>
    </div>
  );
}
