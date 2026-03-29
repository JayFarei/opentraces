import { createContext, useContext, useState, useCallback } from "react";
import type { ReactNode } from "react";

interface SelectionState {
  selectedSessionId: string | null;
  selectedNodeId: string | null;
  setSelectedSessionId: (id: string | null) => void;
  setSelectedNodeId: (id: string | null) => void;
}

const SelectionContext = createContext<SelectionState | null>(null);

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selectedSessionId, setSessionId] = useState<string | null>(null);
  const [selectedNodeId, setNodeId] = useState<string | null>(null);

  const setSelectedSessionId = useCallback((id: string | null) => {
    setSessionId(id);
    setNodeId(null);
  }, []);

  const setSelectedNodeId = useCallback((id: string | null) => {
    setNodeId(id);
  }, []);

  return (
    <SelectionContext.Provider
      value={{
        selectedSessionId,
        selectedNodeId,
        setSelectedSessionId,
        setSelectedNodeId,
      }}
    >
      {children}
    </SelectionContext.Provider>
  );
}

export function useSelection(): SelectionState {
  const ctx = useContext(SelectionContext);
  if (!ctx) throw new Error("useSelection must be used within SelectionProvider");
  return ctx;
}
