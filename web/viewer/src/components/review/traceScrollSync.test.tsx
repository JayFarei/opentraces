/* @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { TraceStep, TraceTreeNode } from "../../lib/api";
import { DARK } from "../../tokens";
import { ConversationView } from "./ConversationView";
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

  test("the tree auto-follows the currently active row", async () => {
    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId={null}
        activePathNodeId="s2"
        onSelectNode={() => {}}
      />,
    );

    const row = view.container.querySelector('[data-node-id="s2"]') as HTMLDivElement;
    const scrollSpy = vi.fn();
    Object.defineProperty(row, "scrollIntoView", { value: scrollSpy, configurable: true });

    view.rerender(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId="s2"
        activePathNodeId="s2"
        onSelectNode={() => {}}
      />,
    );

    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" }));
  });

  test("selection changes do not reorder the tree when the active leaf stays fixed", () => {
    const branchTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "root",
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
          preview: "active branch",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: "agent",
          children: [],
        },
        {
          id: "s3",
          parent_id: "s1",
          kind: "step",
          step_index: 3,
          timestamp: null,
          preview: "older branch",
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

    const ids = () => [...view.container.querySelectorAll("[data-node-id]")].map((node) => node.getAttribute("data-node-id"));
    expect(ids()).toEqual(["s1", "s2", "s3"]);

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

    expect(ids()).toEqual(["s1", "s2", "s3"]);
  });

  test("step details stay hidden until that step is explicitly selected", () => {
    const detailTree: TraceTreeNode[] = [{
      id: "s1",
      parent_id: null,
      kind: "step",
      step_index: 1,
      timestamp: null,
      preview: "root",
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
          preview: "next",
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

    const ids = () => [...view.container.querySelectorAll("[data-node-id]")].map((node) => node.getAttribute("data-node-id"));
    expect(ids()).toEqual(["s1", "s2"]);

    const rowButtons = view.container.querySelector('[data-node-id="s1"]')?.querySelectorAll("button");
    const mainButton = rowButtons?.[1];
    expect(mainButton).toBeTruthy();
    if (!mainButton) throw new Error("expected the step row to expose a select button");
    fireEvent.click(mainButton);

    expect(ids()).toEqual(["s1", "s1-tc0", "s2"]);
  });

  test("the web tree exposes only simplified filter labels", () => {
    const view = render(
      <TraceTree
        t={DARK}
        traceId="trace-1"
        roots={tree}
        selectedNodeId="s1"
        activePathNodeId="s1"
        onSelectNode={() => {}}
      />,
    );

    const options = [...view.container.querySelectorAll("option")].map((option) => option.textContent?.trim());
    expect(options).toEqual(["everything", "no tools", "prompts only"]);
  });
});
