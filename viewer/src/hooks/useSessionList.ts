import { useQuery } from "@tanstack/react-query";
import { fetchSessions } from "../lib/api";
import type { SessionListItem } from "../types/trace";

export function useSessionList() {
  return useQuery<SessionListItem[]>({
    queryKey: ["sessions"],
    queryFn: fetchSessions,
    refetchInterval: 5_000,
  });
}
