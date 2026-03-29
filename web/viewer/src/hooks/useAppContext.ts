import { useQuery } from "@tanstack/react-query";
import { fetchAppContext } from "../lib/api";

export function useAppContext() {
  return useQuery({
    queryKey: ["app-context"],
    queryFn: fetchAppContext,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
