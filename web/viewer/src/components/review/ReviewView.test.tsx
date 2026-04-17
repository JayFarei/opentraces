/* @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { TraceListItem, TraceRecord } from "../../lib/api";
import { DARK } from "../../tokens";
import { ReviewView } from "./ReviewView";

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

const traces: TraceListItem[] = [
  {
    trace_id: "trace-gen-002",
    task: "Later generation trace",
    agent: "claude-code",
    model: "anthropic/claude-opus-4-6",
    steps: 2,
    tool_calls: 0,
    timestamp: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    status: "parsed",
    _stage: "inbox",
    security_flags: 0,
    generation_index: 2,
    project: "demo",
  },
];

const detail: TraceRecord = {
  trace_id: "trace-gen-002",
  generation_index: 2,
  task: { description: "Later generation trace" },
  agent: { name: "claude-code", model: "anthropic/claude-opus-4-6" },
  steps: [
    {
      step_index: 1,
      role: "user",
      content: "hello",
      timestamp: "2026-04-16T10:00:00Z",
    },
  ],
  timestamp_start: "2026-04-16T10:00:00Z",
  metrics: {
    total_steps: 1,
    total_input_tokens: 0,
    total_output_tokens: 0,
    estimated_cost_usd: 0,
  },
};

describe("ReviewView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("renders generation badges and exposes the trace legend in help", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/traces")) return ok(traces);
      if (url.endsWith("/api/context")) {
        return ok({
          project_name: "demo",
          remote: "owner/dataset",
          review_policy: "review",
          push_policy: "manual",
          authenticated: false,
          username: null,
        });
      }
      if (url.endsWith("/api/traces/trace-gen-002")) return ok(detail);
      if (url.endsWith("/api/traces/trace-gen-002/tree")) return ok({ tree: [] });
      throw new Error(`unexpected fetch ${url}`);
    }));

    const view = renderWithQueryClient(
      <ReviewView
        t={DARK}
        wide
        selectedId="trace-gen-002"
        setSelectedId={() => {}}
        openPush={() => {}}
        openInfo={() => {}}
      />,
    );

    await waitFor(() => expect(view.getByText("Later generation trace")).toBeInTheDocument());
    expect(view.getByText("◐")).toBeInTheDocument();
    expect(view.getByText("↑2")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "?" });
    await waitFor(() => expect(view.getByText("Review help")).toBeInTheDocument());
    expect(view.getByText(/Session generation\./)).toBeInTheDocument();
    expect(view.getByText(/↑2\+/)).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "?" });
    await waitFor(() => expect(view.queryByText("Review help")).not.toBeInTheDocument());
  });

  test("refresh invalidates the selected trace preview, tree, and inverse blame queries", async () => {
    const counts = new Map<string, number>();
    const inverseBlame = {
      trace: {
        id: "trace-gen-002",
        trace_id: "trace-gen-002",
        name: "Later generation trace",
        lines: 3,
        model: "anthropic/claude-opus-4-6",
      },
      commits: [
        {
          id: "commit-1",
          sha: "sha-1",
          msg: "feat: add trace",
          linesInCommit: 2,
          totalCommitLines: 4,
          pct: "50%",
          source: "attribution",
          tier: "tool_emitted",
        },
      ],
      files: [{ path: "app.py", lines: 3 }],
    };

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      counts.set(url, (counts.get(url) ?? 0) + 1);
      if (url.endsWith("/api/traces")) return ok(traces);
      if (url.endsWith("/api/context")) {
        return ok({
          project_name: "demo",
          remote: "owner/dataset",
          review_policy: "review",
          push_policy: "manual",
          authenticated: false,
          username: null,
        });
      }
      if (url.endsWith("/api/trace/trace-gen-002/detail")) return ok(detail);
      if (url.endsWith("/api/traces/trace-gen-002/tree")) return ok({ tree: [] });
      if (url.endsWith("/api/trace/trace-gen-002/commits")) return ok(inverseBlame);
      if (url.endsWith("/api/refresh")) return ok({ status: "ok", new: 0, updated: 0, skipped: 0, errors: 0, total: 0 });
      throw new Error(`unexpected fetch ${url}`);
    }));

    const view = renderWithQueryClient(
      <ReviewView
        t={DARK}
        wide
        selectedId="trace-gen-002"
        setSelectedId={() => {}}
        openPush={() => {}}
        openInfo={() => {}}
      />,
    );

    await waitFor(() => expect(view.getByText("Later generation trace")).toBeInTheDocument());
    await waitFor(() => expect(view.container.querySelector('[data-testid="trace-preview-conversation-pane"]')).toBeTruthy());
    const blameTab = [...view.container.querySelectorAll("span")].find((node) => node.textContent === "blame");
    expect(blameTab).toBeTruthy();
    if (!blameTab) throw new Error("expected blame tab");
    fireEvent.click(blameTab);
    await waitFor(() => expect(view.getByText("COMMITS WITH LINE-LEVEL ATTRIBUTION")).toBeInTheDocument());

    const refreshButton = view.container.querySelector('[aria-label="Refresh inbox"]');
    expect(refreshButton).toBeTruthy();
    if (!refreshButton) throw new Error("expected refresh button");
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(counts.get("/api/traces")).toBe(2);
      expect(counts.get("/api/context")).toBe(2);
      expect(counts.get("/api/trace/trace-gen-002/detail")).toBe(2);
      expect(counts.get("/api/traces/trace-gen-002/tree")).toBe(2);
      expect(counts.get("/api/trace/trace-gen-002/commits")).toBe(2);
    });
  });
});
