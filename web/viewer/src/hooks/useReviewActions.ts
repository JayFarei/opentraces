import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  rejectSession,
  redactStep,
  commitSessions,
  pushCommit,
} from "../lib/api";

export function useReviewActions() {
  const qc = useQueryClient();

  const invalidateSessions = () => {
    void qc.invalidateQueries({ queryKey: ["sessions"] });
  };

  const invalidateTrace = (traceId: string) => {
    void qc.invalidateQueries({ queryKey: ["trace", traceId] });
  };

  const commit = useMutation({
    mutationFn: ({ sessionIds, message }: { sessionIds: string[]; message: string }) =>
      commitSessions(sessionIds, message),
    onSuccess: () => invalidateSessions(),
  });

  const reject = useMutation({
    mutationFn: (traceId: string) => rejectSession(traceId),
    onSuccess: () => invalidateSessions(),
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
    onSuccess: () => invalidateSessions(),
    onError: (err: Error) => {
      console.error("[push] failed:", err.message);
    },
  });

  return { commit, reject, redact, push };
}
