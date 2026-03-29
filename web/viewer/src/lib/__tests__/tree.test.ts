import { describe, it, expect } from "vitest";
import { buildTree, flattenTree, findNode } from "../tree";
import type { Step, SecurityFlag } from "../../types/trace";

/** Helper to create a minimal Step */
function makeStep(overrides: Partial<Step> & { step_index: number }): Step {
  return {
    role: "agent",
    content: null,
    reasoning_content: null,
    model: null,
    system_prompt_hash: null,
    agent_role: null,
    parent_step: null,
    call_type: null,
    subagent_trajectory_ref: null,
    tools_available: [],
    tool_calls: [],
    observations: [],
    snippets: [],
    token_usage: {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      prefix_reuse_tokens: 0,
    },
    timestamp: null,
    ...overrides,
  };
}

describe("buildTree", () => {
  it("returns empty array for empty steps", () => {
    expect(buildTree([])).toEqual([]);
  });

  it("creates a single root node for a user step", () => {
    const steps = [makeStep({ step_index: 0, role: "user", content: "Hello" })];
    const tree = buildTree(steps);
    expect(tree).toHaveLength(1);
    expect(tree[0]!.type).toBe("user");
    expect(tree[0]!.label).toBe("Hello");
    expect(tree[0]!.children).toHaveLength(0);
  });

  it("expands tool_calls as children of the step", () => {
    const steps = [
      makeStep({
        step_index: 0,
        role: "agent",
        content: "Thinking...",
        tool_calls: [
          { tool_call_id: "tc1", tool_name: "Read", input: { path: "/a" }, duration_ms: null },
          { tool_call_id: "tc2", tool_name: "Write", input: { path: "/b" }, duration_ms: null },
          { tool_call_id: "tc3", tool_name: "Bash", input: { cmd: "ls" }, duration_ms: null },
        ],
        observations: [
          { source_call_id: "tc1", content: "file content", output_summary: null, error: null },
        ],
      }),
    ];
    const tree = buildTree(steps);
    expect(tree).toHaveLength(1);
    expect(tree[0]!.children).toHaveLength(3);
    expect(tree[0]!.children[0]!.type).toBe("tool");
    // Tool labels now show tool_name: primary_arg_value (not param name)
    expect(tree[0]!.children[0]!.label).toBe("Read");  // no file_path key match for "path"
    expect(tree[0]!.children[0]!.observation?.content).toBe("file content");
    expect(tree[0]!.children[1]!.label).toBe("Write"); // no file_path key match for "path"
    expect(tree[0]!.children[2]!.label).toBe("Bash");  // no "command" key match for "cmd"
  });

  it("builds nested tree from parent_step chain (subagent)", () => {
    const steps = [
      makeStep({ step_index: 0, role: "user", content: "Root" }),
      makeStep({ step_index: 1, role: "agent", content: "Agent", parent_step: 0 }),
      makeStep({
        step_index: 2,
        role: "agent",
        content: "SubAgent",
        parent_step: 1,
        call_type: "subagent",
      }),
    ];
    const tree = buildTree(steps);
    expect(tree).toHaveLength(1);
    expect(tree[0]!.label).toBe("Root");
    expect(tree[0]!.children).toHaveLength(1);
    expect(tree[0]!.children[0]!.label).toBe("Agent");
    expect(tree[0]!.children[0]!.children).toHaveLength(1);
    expect(tree[0]!.children[0]!.children[0]!.type).toBe("subagent");
    expect(tree[0]!.children[0]!.children[0]!.label).toBe("SubAgent");
  });

  it("promotes orphan steps to root level", () => {
    const steps = [
      makeStep({ step_index: 0, role: "user", content: "Root" }),
      makeStep({ step_index: 1, role: "agent", content: "Orphan", parent_step: 999 }),
    ];
    const tree = buildTree(steps);
    expect(tree).toHaveLength(2);
    expect(tree[0]!.label).toBe("Root");
    expect(tree[1]!.label).toBe("Orphan");
  });

  it("detects cycles and promotes both steps to root", () => {
    const steps = [
      makeStep({ step_index: 0, role: "agent", content: "A", parent_step: 1 }),
      makeStep({ step_index: 1, role: "agent", content: "B", parent_step: 0 }),
    ];
    const tree = buildTree(steps);
    // Both should be root-level since they form a cycle
    expect(tree).toHaveLength(2);
  });

  it("truncates labels at ~80 characters", () => {
    const longContent = "a".repeat(200);
    const steps = [makeStep({ step_index: 0, content: longContent })];
    const tree = buildTree(steps);
    expect(tree[0]!.label.length).toBeLessThanOrEqual(80);
    expect(tree[0]!.label.endsWith("\u2026")).toBe(true);
  });

  it("uses tool_name as label for tool nodes", () => {
    // Step with content keeps tool calls as children
    const steps = [
      makeStep({
        step_index: 0,
        content: "Making an edit",
        tool_calls: [
          { tool_call_id: "tc1", tool_name: "Edit", input: {}, duration_ms: null },
        ],
      }),
    ];
    const tree = buildTree(steps);
    expect(tree[0]!.children[0]!.label).toBe("Edit");
  });

  it("merges contentless step with its tool calls", () => {
    // Step with no content and only tool calls promotes tools to root
    const steps = [
      makeStep({
        step_index: 0,
        content: null,
        tool_calls: [
          { tool_call_id: "tc1", tool_name: "Read", input: {}, duration_ms: null },
          { tool_call_id: "tc2", tool_name: "Grep", input: {}, duration_ms: null },
        ],
      }),
    ];
    const tree = buildTree(steps);
    // Tool calls promoted to root level, no wrapper step node
    expect(tree).toHaveLength(2);
    expect(tree[0]!.type).toBe("tool");
    expect(tree[0]!.label).toBe("Read");
    expect(tree[1]!.label).toBe("Grep");
  });

  it("propagates security flags to hasFlag", () => {
    const steps = [
      makeStep({ step_index: 0, content: "Clean" }),
      makeStep({ step_index: 1, content: "Flagged" }),
    ];
    const flags: SecurityFlag[] = [
      { step_index: 1, field: "content", reason: "secret detected" },
    ];
    const tree = buildTree(steps, flags);
    expect(tree[0]!.hasFlag).toBe(false);
    expect(tree[1]!.hasFlag).toBe(true);
  });

  it("handles system steps", () => {
    const steps = [makeStep({ step_index: 0, role: "system" })];
    const tree = buildTree(steps);
    expect(tree[0]!.type).toBe("system");
    expect(tree[0]!.label).toBe("system prompt");
  });
});

describe("flattenTree", () => {
  it("preserves DFS order", () => {
    const steps = [
      makeStep({ step_index: 0, role: "user", content: "Root" }),
      makeStep({ step_index: 1, role: "agent", content: "Child1", parent_step: 0 }),
      makeStep({ step_index: 2, role: "agent", content: "Child2", parent_step: 0 }),
      makeStep({ step_index: 3, role: "agent", content: "Grandchild", parent_step: 1 }),
    ];
    const tree = buildTree(steps);
    const flat = flattenTree(tree);
    const labels = flat.map((n) => n.label);
    expect(labels).toEqual(["Root", "Child1", "Grandchild", "Child2"]);
  });

  it("returns empty for empty roots", () => {
    expect(flattenTree([])).toEqual([]);
  });
});

describe("findNode", () => {
  it("returns correct node by id", () => {
    const steps = [
      makeStep({ step_index: 0, content: "Root" }),
      makeStep({ step_index: 1, content: "Child", parent_step: 0 }),
    ];
    const tree = buildTree(steps);
    const node = findNode(tree, "step-1");
    expect(node).not.toBeNull();
    expect(node!.label).toBe("Child");
  });

  it("finds tool nodes", () => {
    const steps = [
      makeStep({
        step_index: 0,
        tool_calls: [
          { tool_call_id: "tc1", tool_name: "Grep", input: { pattern: "foo" }, duration_ms: null },
        ],
      }),
    ];
    const tree = buildTree(steps);
    const node = findNode(tree, "tool-0-0");
    expect(node).not.toBeNull();
    expect(node!.type).toBe("tool");
  });

  it("returns null for nonexistent id", () => {
    const tree = buildTree([makeStep({ step_index: 0 })]);
    expect(findNode(tree, "nope")).toBeNull();
  });
});
