import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  stageSession,
  unstageSession,
  approveSession,
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

  const stage = useMutation({
    mutationFn: (traceId: string) => stageSession(traceId),
    onSuccess: () => invalidateSessions(),
  });

  const unstage = useMutation({
    mutationFn: (traceId: string) => unstageSession(traceId),
    onSuccess: () => invalidateSessions(),
  });

  const approve = useMutation({
    mutationFn: (traceId: string) => approveSession(traceId),
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

  const commit = useMutation({
    mutationFn: ({ sessionIds, message }: { sessionIds: string[]; message: string }) =>
      commitSessions(sessionIds, message),
    onSuccess: () => invalidateSessions(),
  });

  const push = useMutation({
    mutationFn: (commitId: string) => pushCommit(commitId),
    onSuccess: () => invalidateSessions(),
  });

  return { stage, unstage, approve, reject, redact, commit, push };
}
