import { useQuery } from "@tanstack/react-query";
import { fetchSessions } from "../lib/api";
import type { SessionListItem } from "../types/trace";

export function useSessionList() {
  return useQuery<SessionListItem[]>({
    queryKey: ["sessions"],
    queryFn: fetchSessions,
    // Poll every 5s when backend is reachable, stop on error
    refetchInterval: (query) => (query.state.error ? false : 5_000),
    retry: false,
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  });
}
