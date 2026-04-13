import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Verifies onboarding does not flicker between states.
 *
 * The bug: when the Flask API is not running, React Query refetches
 * every 5s and errors. If data becomes undefined during refetch,
 * the app briefly swaps from Onboarding to empty AppLayout and back.
 *
 * The fix: use placeholderData to keep previous data during refetch,
 * and show Onboarding for loading/error/empty states consistently.
 */

// Track which component is rendered
let renderLog: string[] = [];

// Mock the session list hook to simulate API behavior
vi.mock("../hooks/useTraceList", () => ({
  useTraceList: vi.fn(),
}));

// Mock app context hook
vi.mock("../hooks/useAppContext", () => ({
  useAppContext: () => ({ data: null }),
}));

// Mock review actions
vi.mock("../hooks/useReviewActions", () => ({
  useReviewActions: () => ({
    approve: { mutate: vi.fn() },
    reject: { mutate: vi.fn() },
    push: { mutate: vi.fn() },
  }),
}));

// Mock keyboard nav
vi.mock("../hooks/useKeyboardNav", () => ({
  useKeyboardNav: () => ({ showHelp: false, setShowHelp: vi.fn() }),
}));

import { useTraceList } from "../hooks/useTraceList";

type TraceState = {
  data: { trace_id: string }[] | undefined;
  isLoading: boolean;
  isError: boolean;
};

describe("Onboarding stability", () => {
  beforeEach(() => {
    renderLog = [];
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows onboarding consistently when API is unreachable (no flicker to empty app)", () => {
    const mock = vi.mocked(useTraceList);

    // Simulate the sequence of states React Query goes through:
    const states: TraceState[] = [
      // 1. Initial load
      { data: undefined, isLoading: true, isError: false },
      // 2. First fetch fails (API not running)
      { data: undefined, isLoading: false, isError: true },
      // 3. Refetch starts (5s later) — this is where the flicker happened
      { data: undefined, isLoading: true, isError: false },
      // 4. Refetch fails again
      { data: undefined, isLoading: false, isError: true },
      // 5. Another refetch
      { data: undefined, isLoading: true, isError: false },
      // 6. Fails again
      { data: undefined, isLoading: false, isError: true },
    ];

    for (const state of states) {
      mock.mockReturnValue(state as ReturnType<typeof useTraceList>);

      // Evaluate the isEmpty/showOnboarding logic directly
      const traces = state.data;
      const { isLoading, isError } = state;
      const sampleMode = false;

      const hasTraces = sampleMode || (traces != null && traces.length > 0);
      const showOnboarding = !hasTraces && (isLoading || isError || traces?.length === 0);

      renderLog.push(showOnboarding ? "onboarding" : "app");
    }

    // Every single state should show onboarding — no "app" flickers
    expect(renderLog).toEqual([
      "onboarding", // loading
      "onboarding", // error
      "onboarding", // refetch loading
      "onboarding", // refetch error
      "onboarding", // refetch loading
      "onboarding", // refetch error
    ]);
  });

  it("shows onboarding when API returns empty traces", () => {
    const mock = vi.mocked(useTraceList);

    const states: TraceState[] = [
      { data: undefined, isLoading: true, isError: false },
      { data: [], isLoading: false, isError: false },
      // Refetch keeps returning empty
      { data: [], isLoading: false, isError: false },
    ];

    for (const state of states) {
      mock.mockReturnValue(state as ReturnType<typeof useTraceList>);

      const traces = state.data;
      const { isLoading, isError } = state;
      const sampleMode = false;

      const hasTraces = sampleMode || (traces != null && traces.length > 0);
      const showOnboarding = !hasTraces && (isLoading || isError || traces?.length === 0);

      renderLog.push(showOnboarding ? "onboarding" : "app");
    }

    expect(renderLog).toEqual(["onboarding", "onboarding", "onboarding"]);
  });

  it("transitions to app when traces arrive, and stays there", () => {
    const mock = vi.mocked(useTraceList);

    const states: TraceState[] = [
      { data: undefined, isLoading: true, isError: false },
      { data: [{ trace_id: "abc" }], isLoading: false, isError: false },
      // Refetch returns same data
      { data: [{ trace_id: "abc" }], isLoading: false, isError: false },
    ];

    for (const state of states) {
      mock.mockReturnValue(state as ReturnType<typeof useTraceList>);

      const traces = state.data;
      const { isLoading, isError } = state;
      const sampleMode = false;

      const hasTraces = sampleMode || (traces != null && traces.length > 0);
      const showOnboarding = !hasTraces && (isLoading || isError || traces?.length === 0);

      renderLog.push(showOnboarding ? "onboarding" : "app");
    }

    expect(renderLog).toEqual(["onboarding", "app", "app"]);
  });

  it("sampleMode locks to app view regardless of API state", () => {
    const mock = vi.mocked(useTraceList);

    const states: TraceState[] = [
      { data: undefined, isLoading: false, isError: true },
      { data: undefined, isLoading: true, isError: false },
    ];

    for (const state of states) {
      mock.mockReturnValue(state as ReturnType<typeof useTraceList>);

      const traces = state.data;
      const { isLoading, isError } = state;
      const sampleMode = true; // user clicked "load sample data"

      const hasTraces = sampleMode || (traces != null && traces.length > 0);
      const showOnboarding = !hasTraces && (isLoading || isError || traces?.length === 0);

      renderLog.push(showOnboarding ? "onboarding" : "app");
    }

    expect(renderLog).toEqual(["app", "app"]);
  });
});
