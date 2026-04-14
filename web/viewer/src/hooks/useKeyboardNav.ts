import { useEffect, useState, useCallback } from "react";
import { useSelection } from "../contexts/SelectionContext";
import { useTraceList } from "./useTraceList";
import { useTraceData } from "./useTraceData";
export type FocusedPanel = "traces" | "tree" | "detail";

export function useKeyboardNav() {
  const { selectedTraceId, setSelectedTraceId, selectedNodeId, setSelectedNodeId } =
    useSelection();
  const { data: traces } = useTraceList();
  const { tree } = useTraceData(selectedTraceId);

  const [focusedPanel, setFocusedPanel] = useState<FocusedPanel>("traces");
  const [showHelp, setShowHelp] = useState(false);
  const [showAddDialog, setShowAddDialog] = useState(false);

  const cyclePanelForward = useCallback(() => {
    setFocusedPanel((p) => {
      if (p === "traces") return "tree";
      if (p === "tree") return "detail";
      return "traces";
    });
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't handle if user is typing in an input/textarea
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      switch (e.key) {
        case "j": {
          // Navigate down in current panel
          e.preventDefault();
          if (focusedPanel === "traces" && traces && traces.length > 0) {
            const idx = traces.findIndex((s) => s.trace_id === selectedTraceId);
            const next = idx < traces.length - 1 ? idx + 1 : 0;
            const target = traces[next];
            if (target) setSelectedTraceId(target.trace_id);
          } else if (focusedPanel === "tree" && tree.length > 0) {
            const flat = flattenTree(tree);
            const idx = flat.findIndex((n) => n.id === selectedNodeId);
            const next = idx < flat.length - 1 ? idx + 1 : 0;
            const target = flat[next];
            if (target) setSelectedNodeId(target.id);
          }
          break;
        }

        case "k": {
          // Navigate up in current panel
          e.preventDefault();
          if (focusedPanel === "traces" && traces && traces.length > 0) {
            const idx = traces.findIndex((s) => s.trace_id === selectedTraceId);
            const prev = idx > 0 ? idx - 1 : traces.length - 1;
            const target = traces[prev];
            if (target) setSelectedTraceId(target.trace_id);
          } else if (focusedPanel === "tree" && tree.length > 0) {
            const flat = flattenTree(tree);
            const idx = flat.findIndex((n) => n.id === selectedNodeId);
            const prev = idx > 0 ? idx - 1 : flat.length - 1;
            const target = flat[prev];
            if (target) setSelectedNodeId(target.id);
          }
          break;
        }

        case "Enter": {
          // Select/expand current item
          if (focusedPanel === "traces" && selectedTraceId) {
            setFocusedPanel("tree");
          }
          break;
        }

        case "c": {
          // Open add dialog
          e.preventDefault();
          setShowAddDialog(true);
          break;
        }

        case "?": {
          // Toggle help overlay
          e.preventDefault();
          setShowHelp((v) => !v);
          break;
        }

        case "Escape": {
          // Dismiss overlays
          if (showHelp) {
            setShowHelp(false);
          } else if (showAddDialog) {
            setShowAddDialog(false);
          }
          break;
        }

        case "Tab": {
          // Move focus between panels
          e.preventDefault();
          cyclePanelForward();
          break;
        }
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [
    focusedPanel,
    traces,
    selectedTraceId,
    selectedNodeId,
    tree,
    showHelp,
    showAddDialog,
    setSelectedTraceId,
    setSelectedNodeId,
    cyclePanelForward,
  ]);

  return { focusedPanel, showHelp, setShowHelp, showAddDialog, setShowAddDialog };
}

/** Flatten a tree structure into a flat list for j/k navigation. */
function flattenTree(
  nodes: Array<{ id: string; children: Array<{ id: string; children: unknown[] }> }>,
): Array<{ id: string }> {
  const result: Array<{ id: string }> = [];
  for (const node of nodes) {
    result.push({ id: node.id });
    if (node.children && node.children.length > 0) {
      result.push(
        ...flattenTree(node.children as Array<{ id: string; children: Array<{ id: string; children: unknown[] }> }>),
      );
    }
  }
  return result;
}
