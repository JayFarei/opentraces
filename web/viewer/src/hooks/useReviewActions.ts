import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  rejectTrace,
  redactStep,
  commitTraces,
  pushCommit,
} from "../lib/api";

export function useReviewActions() {
  const qc = useQueryClient();

  const invalidateTraces = () => {
    void qc.invalidateQueries({ queryKey: ["traces"] });
  };

  const invalidateTrace = (traceId: string) => {
    void qc.invalidateQueries({ queryKey: ["trace", traceId] });
  };

  const commit = useMutation({
    mutationFn: ({ traceIds, message }: { traceIds: string[]; message: string }) =>
      commitTraces(traceIds, message),
    onSuccess: () => invalidateTraces(),
  });

  const reject = useMutation({
    mutationFn: (traceId: string) => rejectTrace(traceId),
    onSuccess: () => invalidateTraces(),
  });

  const redact = useMutation({
    mutationFn: ({ traceId, stepIndex }: { traceId: string; stepIndex: number }) =>
      redactStep(traceId, stepIndex),
    onSuccess: (_data, variables) => {
      invalidateTrace(variables.traceId);
    },
  });

  const push = useMutation({
    mutationFn: (commitId?: string) => pushCommit(commitId),
    onSuccess: () => invalidateTraces(),
    onError: (err: Error) => {
      console.error("[push] failed:", err.message);
    },
  });

  return { commit, reject, redact, push };
}
