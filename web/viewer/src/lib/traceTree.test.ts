import { describe, expect, test } from "vitest";
import { flattenTree, type FlatTreeNode } from "./traceTree";
import type { TraceTreeNode } from "./api";

function ids(rows: FlatTreeNode[]) {
  return rows.map((row) => row.node.id);
}

const tree: TraceTreeNode[] = [{
  id: "s1",
  parent_id: null,
  kind: "step",
  step_index: 1,
  timestamp: "2026-04-16T10:00:00Z",
  preview: "user: start",
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
      timestamp: "2026-04-16T10:01:00Z",
      preview: "agent: branch",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: "agent",
      children: [
        {
          id: "s2-subagent",
          parent_id: "s2",
          kind: "subagent_ref",
          step_index: 2,
          timestamp: "2026-04-16T10:01:00Z",
          preview: "subagent alpha",
          label: null,
          on_active_path: false,
          entity_ref: { trace_id: "alpha" },
          role: null,
          children: [],
        },
      ],
    },
    {
      id: "s3-tc0",
      parent_id: "s1",
      kind: "tool_call",
      step_index: 3,
      timestamp: "2026-04-16T10:02:00Z",
      preview: "bash pytest",
      label: null,
      on_active_path: false,
      entity_ref: null,
      role: null,
      children: [],
    },
    {
      id: "s4",
      parent_id: "s1",
      kind: "step",
      step_index: 4,
      timestamp: "2026-04-16T10:03:00Z",
      preview: "agent: decision",
      label: "decision",
      on_active_path: false,
      entity_ref: null,
      role: "agent",
      children: [],
    },
  ],
}];

describe("flattenTree", () => {
  test("promotes the active path and keeps labels searchable", () => {
    expect(ids(flattenTree(tree, "s4", new Set(), "default", ""))).toEqual(["s1", "s4", "s2", "s2-subagent", "s3-tc0"]);
    expect(ids(flattenTree(tree, "s4", new Set(), "labeled-only", ""))).toEqual(["s4"]);
    expect(ids(flattenTree(tree, "s4", new Set(), "default", "decision"))).toEqual(["s4"]);
  });

  test("hides tool rows in no-tools mode and folded descendants", () => {
    expect(ids(flattenTree(tree, "s4", new Set(), "no-tools", ""))).toEqual(["s1", "s4", "s2", "s2-subagent"]);
    expect(ids(flattenTree(tree, "s4", new Set(["s2"]), "default", ""))).toEqual(["s1", "s4", "s2", "s3-tc0"]);
  });

  test("can keep the default list to steps-only until a step is expanded", () => {
    expect(ids(flattenTree(tree, "s4", new Set(), "default", "", {
      detailStepId: null,
      promoteActiveSiblings: false,
    }))).toEqual(["s1", "s2", "s4"]);
    expect(ids(flattenTree(tree, "s4", new Set(), "default", "", {
      detailStepId: "s1",
      promoteActiveSiblings: false,
    }))).toEqual(["s1", "s3-tc0", "s2", "s4"]);
  });
});
