import { useQuery } from "@tanstack/react-query";
import { fetchTraces } from "../lib/api";
import type { TraceListItem } from "../types/trace";

export function useTraceList() {
  return useQuery<TraceListItem[]>({
    queryKey: ["traces"],
    queryFn: fetchTraces,
    // Poll every 5s when backend is reachable, stop on error
    refetchInterval: (query) => (query.state.error ? false : 5_000),
    retry: false,
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  });
}
