import { describe, expect, test } from "vitest";
import type { TraceTreeNode } from "../../lib/api";
import { resolveTraceTreeSelection } from "./TracePreview";

const tree: TraceTreeNode[] = [{
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
          step_index: null,
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

describe("TracePreview selection resolution", () => {
  test("non-step tree nodes resolve to the owning step and conversation anchor", () => {
    expect(resolveTraceTreeSelection(tree, "s2-tc0", null)).toEqual({
      activeStepIndex: 2,
      scrollTargetNodeId: "s2-tc0",
    });
  });

  test("compaction nodes fall back to the owning step anchor", () => {
    const compactionTree: TraceTreeNode[] = [{
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
          id: "s1-compaction",
          parent_id: "s1",
          kind: "compaction",
          step_index: null,
          timestamp: null,
          preview: "compaction",
          label: null,
          on_active_path: false,
          entity_ref: null,
          role: null,
          children: [],
        },
      ],
    }];

    expect(resolveTraceTreeSelection(compactionTree, "s1-compaction", null)).toEqual({
      activeStepIndex: 1,
      scrollTargetNodeId: "s1",
    });
  });
});
