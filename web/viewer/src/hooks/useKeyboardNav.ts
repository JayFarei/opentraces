import { useEffect, useState, useCallback } from "react";
import { useSelection } from "../contexts/SelectionContext";
import { useSessionList } from "./useSessionList";
import { useTraceData } from "./useTraceData";
export type FocusedPanel = "sessions" | "tree" | "detail";

export function useKeyboardNav() {
  const { selectedSessionId, setSelectedSessionId, selectedNodeId, setSelectedNodeId } =
    useSelection();
  const { data: sessions } = useSessionList();
  const { tree } = useTraceData(selectedSessionId);

  const [focusedPanel, setFocusedPanel] = useState<FocusedPanel>("sessions");
  const [showHelp, setShowHelp] = useState(false);
  const [showCommitDialog, setShowCommitDialog] = useState(false);

  const cyclePanelForward = useCallback(() => {
    setFocusedPanel((p) => {
      if (p === "sessions") return "tree";
      if (p === "tree") return "detail";
      return "sessions";
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
          if (focusedPanel === "sessions" && sessions && sessions.length > 0) {
            const idx = sessions.findIndex((s) => s.trace_id === selectedSessionId);
            const next = idx < sessions.length - 1 ? idx + 1 : 0;
            const target = sessions[next];
            if (target) setSelectedSessionId(target.trace_id);
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
          if (focusedPanel === "sessions" && sessions && sessions.length > 0) {
            const idx = sessions.findIndex((s) => s.trace_id === selectedSessionId);
            const prev = idx > 0 ? idx - 1 : sessions.length - 1;
            const target = sessions[prev];
            if (target) setSelectedSessionId(target.trace_id);
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
          if (focusedPanel === "sessions" && selectedSessionId) {
            setFocusedPanel("tree");
          }
          break;
        }

        case "c": {
          // Open commit dialog
          e.preventDefault();
          setShowCommitDialog(true);
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
          } else if (showCommitDialog) {
            setShowCommitDialog(false);
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
    sessions,
    selectedSessionId,
    selectedNodeId,
    tree,
    showHelp,
    showCommitDialog,
    setSelectedSessionId,
    setSelectedNodeId,
    cyclePanelForward,
  ]);

  return { focusedPanel, showHelp, setShowHelp, showCommitDialog, setShowCommitDialog };
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
