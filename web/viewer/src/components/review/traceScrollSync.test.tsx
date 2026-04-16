/* @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { TraceRecord, TraceStep, TraceTreeNode } from "../../lib/api";
import { DARK } from "../../tokens";
import { ConversationView } from "./ConversationView";
import { TracePreview } from "./TracePreview";
import { TraceTree } from "./TraceTree";

type RectShape = {
  top: number;
  bottom: number;
  height?: number;
  left?: number;
  right?: number;
  width?: number;
};

function makeRect(rect: RectShape): DOMRect {
  const height = rect.height ?? rect.bottom - rect.top;
  const left = rect.left ?? 0;
  const width = rect.width ?? 200;
  const right = rect.right ?? left + width;
  return {
    x: left,
    y: rect.top,
    top: rect.top,
    bottom: rect.bottom,
    left,
    right,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect;
}

function trackRect(node: Element, initial: RectShape) {
  let current = initial;
  vi.spyOn(node, "getBoundingClientRect").mockImplementation(() => makeRect(current));
  return {
    set(next: RectShape) {
      current = next;
    },
  };
}

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

const steps: TraceStep[] = [
  { step_index: 1, role: "user", content: "one" },
  {
    step_index: 2,
    role: "agent",
    content: "two",
    tool_calls: [{ tool_name: "Read", input: { file_path: "app.py" } }],
    observations: [{ tool_name: "Read", content: "done" }],
    subagent_trajectory_ref: "subtrace-alpha",
  },
  { step_index: 3, role: "agent", content: "three" },
];

const tree: TraceTreeNode[] = [
  {
    id: "s1",
    parent_id: null,
    kind: "step",
    step_index: 1,
    timestamp: null,
    preview: "user: one",
    label: null,
    on_active_path: false,
    entity_ref: null,
    role: "user",
    children: [],
  },
  {
    id: "s2",
    parent_id: null,
    kind: "step",
    step_index: 2,
    timestamp: null,
    preview: "agent: two",
    label: null,
    on_active_path: true,
    entity_ref: null,
    role: "agent",
    children: [],
  },
];

describe("trace preview scroll sync", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      value: vi.fn(),
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  test("conversation scrolling reports the step currently in view", async () => {
    const onActiveStepChange = vi.fn();
    const view = render(
      <ConversationView
        t={DARK}
        traceId="trace-1"
        steps={steps}
        activeStepIndex={2}
        selectedNodeId="s2"
        scrollTargetNodeId={null}
        onScrollTargetConsumed={() => {}}
        onActiveStepChange={onActiveStepChange}
      />,
    );

    const scrollContainer = view.getByTestId("conversation-scroll");
    const step1 = view.container.querySelector("#trace-node-s1");
    const step2 = view.container.querySelector("#trace-node-s2");
    const step3 = view.container.querySelector("#trace-node-s3");

    expect(step1).not.toBeNull();
    expect(step2).not.toBeNull();
    expect(step3).not.toBeNull();

    const containerRect = trackRect(scrollContainer, { top: 0, bottom: 360 });
    const step1Rect = trackRect(step1!, { top: -180, bottom: -20 });
    const step2Rect = trackRect(step2!, { top: 20, bottom: 210 });
    const step3Rect = trackRect(step3!, { top: 230, bottom: 420 });

    onActiveStepChange.mockClear();
    fireEvent.scroll(scrollContainer);
    await waitFor(() => expect(onActiveStepChange).toHaveBeenLastCalledWith(2));

    step1Rect.set({ top: -360, bottom: -200 });
    step2Rect.set({ top: -120, bottom: 20 });
    step3Rect.set({ top: 60, bottom: 260 });
    containerRect.set({ top: 0, bottom: 360 });
    fireEvent.scroll(scrollContainer);

    await waitFor(() => expect(onActiveStepChange).toHaveBeenLastCalledWith(3));
  });

  test("explicit node targets scroll the matching conversation block", async () => {
    const onScrollTargetConsumed = vi.fn();
    const view = render(
      <ConversationView
        t={DARK}
        traceId="trace-1"
        steps={steps}
        activeStepIndex={1}
        selectedNodeId="s1"
        scrollTargetNodeId={null}
        onScrollTargetConsumed={onScrollTargetConsumed}
        onActiveStepChange={() => {}}
      />,
    );

    const scrollSpy = vi.fn();
    await waitFor(() => expect(view.container.querySelector('[id="trace-node-s2-tc0"]')).not.toBeNull());
    const toolCall = view.container.querySelector('[id="trace-node-s2-tc0"]');
    Object.defineProperty(toolCall!, "scrollIntoView", {
      value: scrollSpy,
      configurable: true,
    });

    view.rerender(
      <ConversationView
        t={DARK}
        traceId="trace-1"
        steps={steps}
        activeStepIndex={2}
        selectedNodeId="s2-tc0"
        scrollTargetNodeId="s2-tc0"
        onScrollTargetConsumed={onScrollTargetConsumed}
        onActiveStepChange={() => {}}
      />,
    );

    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "center", behavior: "smooth" }));
    expect(onScrollTargetConsumed).toHaveBeenCalledTimes(1);
  });

  test("programmatic navigation ignores intermediate scroll sync", () => {
    vi.useFakeTimers();
    try {
      const onActiveStepChange = vi.fn();
      const view = render(
        <ConversationView
          t={DARK}
          traceId="trace-1"
          steps={steps}
          activeStepIndex={3}
          selectedNodeId="s3"
          scrollTargetNodeId="s3"
          onScrollTargetConsumed={() => {}}
          onActiveStepChange={onActiveStepChange}
        />,
      );

      const scrollContainer = view.container.querySelector('[data-testid="conversation-scroll"]') as HTMLDivElement;
      const stepNodes = view.container.querySelectorAll("[data-step-index]");
      expect(stepNodes).toHaveLength(3);
      const [step1, step2, step3] = [...stepNodes];
      if (!step1 || !step2 || !step3) throw new Error("expected three step rows");

      trackRect(scrollContainer, { top: 0, bottom: 360 });
      trackRect(step1, { top: -360, bottom: -200 });
      trackRect(step2, { top: -120, bottom: 40 });
      trackRect(step3, { top: 80, bottom: 260 });

      fireEvent.scroll(scrollContainer);
      expect(onActiveStepChange).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  test("changing traces resets the conversation pane to the top", () => {
    const view = render(
      <ConversationView
        t={DARK}
        traceId="trace-1"
        steps={steps}
        activeStepIndex={1}
        selectedNodeId="s1"
        scrollTargetNodeId={null}
        onScrollTargetConsumed={() => {}}
        onActiveStepChange={() => {}}
      />,
    );

    const scrollContainer = view.container.querySelector('[data-testid="conversation-scroll"]') as HTMLDivElement;
    scrollContainer.scrollTop = 240;

    view.rerender(
      <ConversationView
        t={DARK}
        traceId="trace-2"
        steps={steps}
        activeStepIndex={1}
        selectedNodeId="s1"
        scrollTargetNodeId={null}
        onScrollTargetConsumed={() => {}}
        onActiveStepChange={() => {}}
      />,
    );

    expect(scrollContainer.scrollTop).toBe(0);
  });

  test("the tree auto-follows the selected prompt row", async () => {
    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId={null}
        activePathNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const row = view.container.querySelector('[data-node-id="s1"]') as HTMLDivElement;
    const scrollSpy = vi.fn();
    Object.defineProperty(row, "scrollIntoView", { value: scrollSpy, configurable: true });

    view.rerender(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId="s1"
        activePathNodeId="s1"
        onSelectNode={() => {}}
      />,
    );

    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" }));
  });

  test("hidden agent selections fall back to the nearest visible prompt ancestor", async () => {
    const nestedTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "user: root",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "user",
      children: [
        {
          id: "s2",
          parent_id: "s1",
          kind: "step",
          step_index: 2,
          timestamp: null,
          preview: "agent: hidden child",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [],
        },
      ],
    }];

    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={nestedTree}
        selectedNodeId={null}
        activePathNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const filter = view.container.querySelector('[data-testid="trace-tree-filter"]') as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: "user-only" } });

    const row = view.container.querySelector('[data-node-id="s1"]') as HTMLDivElement;
    expect(row.style.background).toBe("transparent");

    view.rerender(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={nestedTree}
        selectedNodeId="s2"
        activePathNodeId="s2"
        onSelectNode={() => {}}
      />,
    );

    await waitFor(() => expect((view.container.querySelector('[data-node-id="s1"]') as HTMLDivElement).style.background).not.toBe("transparent"));
  });

  test("selection changes do not reorder the prompt map when the active leaf stays fixed", () => {
    const branchTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "user: root",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "user",
      children: [
        {
          id: "s2",
          parent_id: "s1",
          kind: "step",
          step_index: 2,
          timestamp: null,
          preview: "agent: active branch",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [
            {
              id: "s4",
              parent_id: "s2",
              kind: "step",
              step_index: 4,
              timestamp: null,
              preview: "user: follow-up",
              label: null,
              on_active_path: false,
              entity_ref: null,
              role: "user",
              children: [],
            },
          ],
        },
        {
          id: "s3",
          parent_id: "s1",
          kind: "step",
          step_index: 3,
          timestamp: null,
          preview: "agent: sibling branch",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [],
        },
      ],
    }];

    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={branchTree}
        selectedNodeId="s2"
        activePathNodeId="s2"
        onSelectNode={() => {}}
      />,
    );

    const filter = view.container.querySelector('[data-testid="trace-tree-filter"]') as HTMLSelectElement;
    fireEvent.change(filter, { target: { value: "user-only" } });

    const ids = () => [...view.container.querySelectorAll("[data-node-id]")].map((node) => node.getAttribute("data-node-id"));
    expect(ids()).toEqual(["s1", "s4"]);

    view.rerender(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={branchTree}
        selectedNodeId="s3"
        activePathNodeId="s2"
        onSelectNode={() => {}}
      />,
    );

    expect(ids()).toEqual(["s1", "s4"]);
  });

  test("the web tree exposes all and prompts filters", () => {
    const detailTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "user: root",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "user",
      children: [
        {
          id: "s1-tc0",
          parent_id: "s1",
          kind: "tool_call",
          step_index: 1,
          timestamp: null,
          preview: "tool detail",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: null,
          children: [],
        },
        {
          id: "s2",
          parent_id: "s1",
          kind: "step",
          step_index: 2,
          timestamp: null,
          preview: "agent: next",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [],
        },
      ],
    }];

    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={detailTree}
        selectedNodeId="s1"
        activePathNodeId="s1"
        onSelectNode={() => {}}
      />,
    );

    const filter = view.container.querySelector('[data-testid="trace-tree-filter"]') as HTMLSelectElement;
    const ids = () => [...view.container.querySelectorAll("[data-node-id]")].map((node) => node.getAttribute("data-node-id"));
    const options = [...filter.querySelectorAll("option")].map((option) => option.textContent?.trim());
    expect(options).toEqual(["all", "prompts"]);
    expect(ids()).toEqual(["s1", "s2"]);
    fireEvent.change(filter, { target: { value: "user-only" } });
    expect(ids()).toEqual(["s1"]);
    expect(view.queryByText("resume")).toBeNull();
  });

  test("the compact rail allows selecting a row by clicking anywhere on the line", () => {
    const onSelectNode = vi.fn();
    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId={null}
        activePathNodeId={null}
        layout="compact"
        onSelectNode={onSelectNode}
      />,
    );

    const row = view.container.querySelector('[data-node-id="s1"]') as HTMLDivElement;
    fireEvent.click(row);

    expect(onSelectNode).toHaveBeenCalledWith("s1", 1);
  });

  test("the docked map starts compact and expands into the full text tree", async () => {
    Object.defineProperty(window, "innerWidth", {
      value: 1440,
      configurable: true,
      writable: true,
    });

    const trace: TraceRecord = {
      trace_id: "trace-1",
      task: { description: "test trace" },
      agent: { name: "claude-code", model: "claude-opus" },
      steps: [
        { step_index: 1, role: "user", content: "first prompt" },
        { step_index: 2, role: "agent", content: "first reply" },
        { step_index: 3, role: "user", content: "second prompt" },
      ],
      timestamp_start: "2026-04-16T10:00:00Z",
      metrics: {
        total_steps: 3,
        total_input_tokens: 120,
        total_output_tokens: 40,
        estimated_cost_usd: 0.12,
      },
      security: {
        redactions_applied: 0,
      },
      _security_flags: [],
      _stage: "inbox",
    };
    const promptTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "user: first prompt",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "user",
      children: [
        {
          id: "s2",
          parent_id: "s1",
          kind: "step",
          step_index: 2,
          timestamp: null,
          preview: "agent: first reply",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [
            {
              id: "s2-tc0",
              parent_id: "s2",
              kind: "tool_call",
              step_index: 2,
              timestamp: null,
              preview: "tool: Read",
              label: null,
              on_active_path: false,
              entity_ref: null,
              role: null,
              children: [],
            },
          ],
        },
      ],
    }];

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/trace/trace-1/detail")) return ok(trace);
      if (url.endsWith("/api/traces/trace-1/tree")) return ok({ tree: promptTree });
      throw new Error(`unexpected fetch ${url}`);
    }));

    const view = renderWithQueryClient(<TracePreview t={DARK} traceId="trace-1" />);

    await waitFor(() => expect(view.container.querySelector('[data-testid="trace-tree-compact"]')).toBeTruthy());
    const compactTree = view.container.querySelector('[data-testid="trace-tree-compact"]') as HTMLDivElement;
    expect(compactTree.textContent).toContain("map");
    expect(compactTree.textContent).not.toContain("first prompt");
    expect(view.container.querySelector('input[placeholder="search map"]')).toBeNull();

    fireEvent.click(view.getByTestId("trace-tree-layout-toggle"));

    await waitFor(() => expect(view.container.querySelector('[data-testid="trace-tree-expanded"]')).toBeTruthy());
    expect(view.container.querySelector('input[placeholder="search map"]')).toBeTruthy();
    expect((view.container.querySelector('[data-testid="trace-tree-expanded"]') as HTMLDivElement).textContent).toContain("first prompt");
  });

  test("tool and result blocks carry the parent step id in the preview", () => {
    const view = render(
      <ConversationView
        t={DARK}
        traceId="trace-1"
        steps={steps}
        activeStepIndex={2}
        selectedNodeId="s2-tc0"
        scrollTargetNodeId={null}
        onScrollTargetConsumed={() => {}}
        onActiveStepChange={() => {}}
      />,
    );

    expect(view.container).toHaveTextContent("s2 · Read");
    expect(view.container).toHaveTextContent("◂ s2 · Read");
    expect(view.container).toHaveTextContent("s2 · subagent");
  });

  test("the mobile map replaces the conversation pane and closes after selection", async () => {
    Object.defineProperty(window, "innerWidth", {
      value: 900,
      configurable: true,
      writable: true,
    });

    const trace: TraceRecord = {
      trace_id: "trace-1",
      task: { description: "test trace" },
      agent: { name: "claude-code", model: "claude-opus" },
      steps: [
        { step_index: 1, role: "user", content: "first prompt" },
        { step_index: 2, role: "agent", content: "first reply" },
        { step_index: 3, role: "user", content: "second prompt" },
      ],
      timestamp_start: "2026-04-16T10:00:00Z",
      metrics: {
        total_steps: 3,
        total_input_tokens: 120,
        total_output_tokens: 40,
        estimated_cost_usd: 0.12,
      },
      security: {
        redactions_applied: 0,
      },
      _security_flags: [],
      _stage: "inbox",
    };
    const promptTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "user: first prompt",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "user",
      children: [
        {
          id: "s2",
          parent_id: "s1",
          kind: "step",
          step_index: 2,
          timestamp: null,
          preview: "agent: first reply",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [
            {
              id: "s3",
              parent_id: "s2",
              kind: "step",
              step_index: 3,
              timestamp: null,
              preview: "user: second prompt",
              label: null,
              on_active_path: false,
              entity_ref: null,
              role: "user",
              children: [],
            },
          ],
        },
      ],
    }];

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/trace/trace-1/detail")) return ok(trace);
      if (url.endsWith("/api/traces/trace-1/tree")) return ok({ tree: promptTree });
      throw new Error(`unexpected fetch ${url}`);
    }));

    const view = renderWithQueryClient(<TracePreview t={DARK} traceId="trace-1" />);

    await waitFor(() => expect(view.container.querySelector('[data-testid="conversation-scroll"]')).toBeTruthy());
    expect(view.container.querySelector('[data-testid="trace-preview-conversation-pane"]')).toHaveStyle({ height: "100%" });
    expect(view.container.querySelector('[data-testid="trace-tree-scroll"]')).toBeNull();

    fireEvent.click(view.getByTestId("trace-map-toggle"));

    await waitFor(() => expect(view.container.querySelector('[data-testid="trace-tree-scroll"]')).toBeTruthy());
    expect(view.container.querySelector('[data-testid="conversation-scroll"]')).toBeNull();
    expect(view.getByTestId("trace-map-toggle")).toHaveTextContent("conversation");

    const rowButtons = view.container.querySelector('[data-node-id="s3"]')?.querySelectorAll("button");
    const mainButton = rowButtons?.[rowButtons.length - 1];
    expect(mainButton).toBeTruthy();
    if (!mainButton) throw new Error("expected a prompt row button");
    fireEvent.click(mainButton);

    await waitFor(() => expect(view.container.querySelector('[data-testid="conversation-scroll"]')).toBeTruthy());
    expect(view.container.querySelector('[data-testid="trace-tree-scroll"]')).toBeNull();
    expect(view.getByTestId("trace-map-toggle")).toHaveTextContent("map");
  });
});
