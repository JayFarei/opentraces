/* @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { BlamePayload, GraphCommit } from "../../lib/api";
import { DARK } from "../../tokens";
import { GraphView } from "./GraphView";

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

const commits: GraphCommit[] = [
  {
    id: "c1a2b3d4",
    sha: "sha-1",
    msg: "Improve viewer trace map navigation",
    timestamp: "2026-04-16T10:00:00Z",
    pct: 12,
    coverage_role: "red",
    traces: [
      {
        id: "trace-one",
        trace_id: "trace-1",
        canonical: true,
        lines: 176,
        short_name: "web-viewer-date",
        changes: "+ Added PROGRAMMATIC_SCROLL_IDLE_MS, sortedStepNodes · ~ Modified pickActiveConversationStep, ConversationView",
        fns: undefined,
        info: undefined,
      },
    ],
  },
  {
    id: "d4e5f6a7",
    sha: "sha-2",
    msg: "Tighten diff semantics",
    timestamp: "2026-04-16T11:00:00Z",
    pct: 100,
    coverage_role: "green",
    traces: [
      {
        id: "trace-two",
        trace_id: "trace-2",
        canonical: true,
        lines: 37,
        short_name: "looks-great-commit",
        changes: "- Deleted oldFormatter",
        fns: "[37 lines · diffuse]",
        info: undefined,
      },
    ],
  },
];

const blameBySha: Record<string, BlamePayload> = {
  "sha-1": {
    sha: "sha-1",
    id: "c1a2b3d4",
    msg: "Improve viewer trace map navigation",
    pct: 12,
    coverage: "12%",
    attributed: "14/115 lines",
    fileCount: 1,
    sessions: [
      {
        id: "trace-one",
        trace_id: "trace-1",
        name: "web-viewer-date",
        lines: 176,
        pct: "12%",
        model: "claude-opus-4-6",
        entities: "+ Added PROGRAMMATIC_SCROLL_IDLE_MS, sortedStepNodes · ~ Modified pickActiveConversationStep, ConversationView",
        canonical: true,
      },
    ],
    files: [{ path: "web/viewer/src/components/review/ConversationView.tsx", total: 90, attr: 14 }],
  },
  "sha-2": {
    sha: "sha-2",
    id: "d4e5f6a7",
    msg: "",
    pct: 100,
    coverage: "100%",
    attributed: "165/16 lines",
    fileCount: 1,
    sessions: [
      {
        id: "trace-two",
        trace_id: "trace-2",
        name: "looks-great-commit",
        lines: 37,
        pct: "100%",
        model: "claude-opus-4-6",
        entities: "- Deleted oldFormatter",
        canonical: true,
      },
    ],
    files: [{ path: "web/viewer/src/lib/conversation.ts", total: 165, attr: 165 }],
  },
};

describe("GraphView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("keeps the commit list compact and falls back to the selected commit subject in blame", async () => {
    const gotoTrace = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/graph?")) return ok({ commits });
      if (url.endsWith("/api/blame/sha-1")) return ok(blameBySha["sha-1"]);
      if (url.endsWith("/api/blame/sha-2")) return ok(blameBySha["sha-2"]);
      throw new Error(`unexpected fetch ${url}`);
    }));

    const view = renderWithQueryClient(
      <GraphView t={DARK} wide mode="dark" gotoTrace={gotoTrace} />,
    );

    await waitFor(() => expect(view.container.querySelector('[data-testid="graph-commit-sha-1"]')).toBeTruthy());

    const firstCommit = view.container.querySelector('[data-testid="graph-commit-sha-1"]') as HTMLElement;
    const secondCommit = view.container.querySelector('[data-testid="graph-commit-sha-2"]') as HTMLElement;
    const addedCount = firstCommit.querySelector('[data-diff-count-kind="added"]') as HTMLElement;
    const modifiedCount = firstCommit.querySelector('[data-diff-count-kind="modified"]') as HTMLElement;
    const deletedCount = secondCommit.querySelector('[data-diff-count-kind="deleted"]') as HTMLElement;
    const stackedSummary = view.container.querySelector('[data-diff-layout="stacked"]') as HTMLElement;

    expect(firstCommit).toHaveAttribute("aria-pressed", "true");
    expect(firstCommit).not.toHaveTextContent("PROGRAMMATIC_SCROLL_IDLE_MS");
    expect(addedCount).toHaveAttribute("aria-label", "2 added changes");
    expect(modifiedCount).toHaveAttribute("aria-label", "2 modified changes");
    expect(deletedCount).toHaveAttribute("aria-label", "1 deleted change");
    expect(stackedSummary).toBeTruthy();

    fireEvent.click(deletedCount);

    await waitFor(() => expect(secondCommit).toHaveAttribute("aria-pressed", "true"));
    await waitFor(() => expect(view.getByTestId("graph-blame-commit-msg")).toHaveTextContent("Tighten diff semantics"));
    expect(gotoTrace).not.toHaveBeenCalled();

    const traceLink = secondCommit.querySelector('[data-testid="graph-trace-link-trace-2"]') as HTMLButtonElement;
    fireEvent.click(traceLink);
    expect(gotoTrace).toHaveBeenCalledWith("trace-2");
  });
});
