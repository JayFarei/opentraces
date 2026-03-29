import { useMemo, useRef } from "react";
import { scaleLinear } from "d3-scale";
import { useSelection } from "../../contexts/SelectionContext";
import { useTraceData } from "../../hooks/useTraceData";
import { flattenTree } from "../../lib/tree";
import type { TreeNode } from "../../types/trace";

const BAR_HEIGHT = 14;
const ROW_HEIGHT = 18;
const HEADER_HEIGHT = 20;
const LEFT_PADDING = 4;

const TYPE_COLORS: Record<TreeNode["type"], string> = {
  user: "var(--blue)",
  agent: "var(--green)",
  tool: "var(--purple, #A855F7)",
  system: "var(--text-dim)",
  subagent: "var(--cyan)",
};

export function TimelineStrip() {
  const { selectedSessionId, selectedNodeId, setSelectedNodeId } = useSelection();
  const { data: trace, tree } = useTraceData(selectedSessionId);
  const containerRef = useRef<HTMLDivElement>(null);

  const flatNodes = useMemo(() => flattenTree(tree), [tree]);

  const { totalDuration, timeScale, nodeOffsets } = useMemo(() => {
    if (!trace || flatNodes.length === 0) {
      return { totalDuration: 0, timeScale: null, nodeOffsets: new Map<string, { start: number; duration: number }>() };
    }

    const startTime = trace.timestamp_start ? new Date(trace.timestamp_start).getTime() : 0;
    const endTime = trace.timestamp_end ? new Date(trace.timestamp_end).getTime() : 0;
    const total = endTime > startTime ? (endTime - startTime) / 1000 : 0;

    const offsets = new Map<string, { start: number; duration: number }>();
    let sequentialOffset = 0;

    for (const node of flatNodes) {
      if (node.step?.timestamp && startTime > 0) {
        const nodeStart = (new Date(node.step.timestamp).getTime() - startTime) / 1000;
        const dur = node.toolCall?.duration_ms
          ? node.toolCall.duration_ms / 1000
          : 0.5;
        offsets.set(node.id, { start: nodeStart, duration: dur });
      } else if (node.toolCall?.duration_ms) {
        const dur = node.toolCall.duration_ms / 1000;
        offsets.set(node.id, { start: sequentialOffset, duration: dur });
        sequentialOffset += dur;
      } else {
        offsets.set(node.id, { start: sequentialOffset, duration: 0.3 });
        sequentialOffset += 0.3;
      }
    }

    const effectiveTotal = total > 0 ? total : sequentialOffset;
    const scale = scaleLinear().domain([0, effectiveTotal]).range([0, 100]);

    return { totalDuration: effectiveTotal, timeScale: scale, nodeOffsets: offsets };
  }, [trace, flatNodes]);

  if (!selectedSessionId) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        select a session
      </div>
    );
  }

  if (!timeScale || flatNodes.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        no timeline data
      </div>
    );
  }

  const formatTime = (s: number) => {
    if (s < 60) return `${s.toFixed(0)}s`;
    return `${(s / 60).toFixed(1)}m`;
  };

  return (
    <div className="h-full overflow-auto bg-[var(--bg)]" ref={containerRef}>
      {/* Time scale header */}
      <div
        className="sticky top-0 bg-[var(--bg)] border-b border-[var(--border)] flex items-end px-1"
        style={{ height: HEADER_HEIGHT }}
      >
        <span className="text-[8px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          0s
        </span>
        <span className="ml-auto text-[8px] font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
          {formatTime(totalDuration)}
        </span>
      </div>

      {/* Bars */}
      <div style={{ position: "relative" }}>
        {flatNodes.map((node, idx) => {
          const offset = nodeOffsets.get(node.id);
          if (!offset) return null;
          const left = timeScale(offset.start);
          const width = Math.max(timeScale(offset.duration) - timeScale(0), 0.5);
          const isSelected = selectedNodeId === node.id;
          const color = TYPE_COLORS[node.type];
          const opacity = node.type === "tool" ? 0.7 : 1;

          return (
            <button
              key={node.id}
              onClick={() => setSelectedNodeId(node.id)}
              className="absolute cursor-pointer transition-opacity duration-100"
              style={{
                left: `${left}%`,
                width: `${Math.max(width, 0.5)}%`,
                top: HEADER_HEIGHT + idx * ROW_HEIGHT + (ROW_HEIGHT - BAR_HEIGHT) / 2,
                height: BAR_HEIGHT,
                backgroundColor: color,
                opacity: isSelected ? 1 : opacity * 0.6,
                border: isSelected ? "1px solid var(--text)" : "none",
                marginLeft: LEFT_PADDING,
              }}
              title={node.label}
            >
              {node.hasFlag && (
                <span
                  className="absolute w-1 h-1 bg-[var(--red)]"
                  style={{ top: -1, right: -1 }}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
