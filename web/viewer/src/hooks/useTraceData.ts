import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTrace } from "../lib/api";
import { buildTree } from "../lib/tree";
import type { TraceRecord, TreeNode } from "../types/trace";

export function useTraceData(traceId: string | null) {
  const query = useQuery<TraceRecord>({
    queryKey: ["trace", traceId],
    queryFn: () => {
      if (!traceId) throw new Error("No trace ID");
      return fetchTrace(traceId);
    },
    enabled: traceId !== null,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const tree: TreeNode[] = useMemo(() => {
    if (!query.data) return [];
    return buildTree(query.data.steps);
  }, [query.data]);

  return { ...query, tree };
}
