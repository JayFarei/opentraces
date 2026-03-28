import { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { flattenTree } from "../../lib/tree";
import { StepNode } from "./StepNode";
import type { TreeNode } from "../../types/trace";

interface TraceTreeProps {
  tree: TreeNode[];
  traceStartMs: number | null;
}

export function TraceTree({ tree, traceStartMs }: TraceTreeProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const flatNodes = useMemo(() => flattenTree(tree), [tree]);

  const virtualizer = useVirtualizer({
    count: flatNodes.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 28,
    overscan: 20,
  });

  if (flatNodes.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-[var(--text-muted)] text-[11px] font-[family-name:var(--font-mono)]">
        no steps in trace
      </div>
    );
  }

  return (
    <div ref={parentRef} className="h-full overflow-y-auto">
      <div
        style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const node = flatNodes[virtualRow.index]!;
          return (
            <div
              key={node.id}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              <StepNode node={node} traceStartMs={traceStartMs} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
