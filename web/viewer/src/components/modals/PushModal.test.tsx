/* @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { TraceListItem } from "../../lib/api";
import { DARK } from "../../tokens";
import { PushModal } from "./PushModal";

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function ok(body: unknown): Response {
  return {
    ok: true,
    json: async () => body,
  } as Response;
}

describe("PushModal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("keyboard shortcuts no-op when there are zero staged traces", async () => {
    const traces: TraceListItem[] = [
      {
        trace_id: "trace-1",
        task: "Inbox trace",
        agent: "claude-code",
        model: "claude-opus",
        steps: 3,
        tool_calls: 1,
        timestamp: new Date().toISOString(),
        status: "parsed",
        _stage: "inbox",
        security_flags: 0,
        generation_index: 0,
        project: "demo",
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/traces")) return ok(traces);
      if (url.endsWith("/api/push")) throw new Error("push should not be called");
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const onClose = vi.fn();
    const view = renderWithQueryClient(<PushModal t={DARK} onClose={onClose} remote="origin/main" />);

    await waitFor(() => expect(view.getByText("Nothing staged.")).toBeInTheDocument());

    fireEvent.keyDown(window, { key: "l" });
    fireEvent.keyDown(window, { key: "s" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(view.getByText("Nothing staged.")).toBeInTheDocument();
    expect(view.queryByText(/Running opentraces/)).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
