"""Tree selector adapter.

Flattening + rendering metadata adapted from badlogic/pi-mono
(MIT, https://github.com/badlogic/pi-mono). See kb/br/39 for the
research brief and kb/plans/048 for the porting plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


FilterMode = Literal["default", "no-tools", "user-only", "labeled-only", "all"]
TreeKind = Literal["step", "tool_call", "observation", "subagent_ref", "compaction"]


@dataclass
class Gutter:
    position: int
    show: bool


@dataclass
class TreeNode:
    id: str
    parent_id: str | None
    kind: TreeKind
    step_index: int | None
    timestamp: str | None
    preview: str
    label: str | None
    on_active_path: bool
    entity_ref: dict | None
    role: str | None = None
    children: list["TreeNode"] = field(default_factory=list)


@dataclass
class FlatNode:
    node: TreeNode
    indent: int
    is_last: bool
    show_connector: bool
    gutters: list[Gutter]
    foldable: bool
    folded: bool
    on_active_path: bool
    is_virtual_root_child: bool = False


def build_tree(record, step_labels: dict[str, str] | None = None) -> list[TreeNode]:
    labels = step_labels or {}
    raw_steps = record.steps if hasattr(record, "steps") else record.get("steps", [])
    steps = sorted(list(raw_steps), key=lambda step: step.step_index if hasattr(step, "step_index") else step.get("step_index", 0))
    nodes: dict[str, TreeNode] = {}
    previous_main_step_id: str | None = None

    for step in steps:
        step_index = step.step_index if hasattr(step, "step_index") else step.get("step_index")
        parent_step = step.parent_step if hasattr(step, "parent_step") else step.get("parent_step")
        step_id = f"s{step_index}"
        parent_id = f"s{parent_step}" if parent_step is not None else previous_main_step_id
        node = TreeNode(
            id=step_id,
            parent_id=parent_id,
            kind="step",
            step_index=step_index,
            timestamp=step.timestamp if hasattr(step, "timestamp") else step.get("timestamp"),
            preview=_step_preview(step),
            label=labels.get(step_id),
            on_active_path=False,
            entity_ref=None,
            role=step.role if hasattr(step, "role") else step.get("role"),
        )
        nodes[step_id] = node

        tool_call_parent: dict[str, str] = {}
        tool_calls = step.tool_calls if hasattr(step, "tool_calls") else step.get("tool_calls", [])
        for index, tool_call in enumerate(tool_calls):
            tool_id = f"{step_id}-tc{index}"
            tool_call_id = tool_call.tool_call_id if hasattr(tool_call, "tool_call_id") else tool_call.get("tool_call_id")
            tool_call_parent[tool_call_id] = tool_id
            nodes[tool_id] = TreeNode(
                id=tool_id,
                parent_id=step_id,
                kind="tool_call",
                step_index=step_index,
                timestamp=node.timestamp,
                preview=_tool_preview(tool_call),
                label=None,
                on_active_path=False,
                entity_ref=None,
            )

        observations = step.observations if hasattr(step, "observations") else step.get("observations", [])
        for index, observation in enumerate(observations):
            source_call_id = observation.source_call_id if hasattr(observation, "source_call_id") else observation.get("source_call_id")
            obs_parent = tool_call_parent.get(source_call_id, step_id)
            nodes[f"{step_id}-ob{index}"] = TreeNode(
                id=f"{step_id}-ob{index}",
                parent_id=obs_parent,
                kind="observation",
                step_index=step_index,
                timestamp=node.timestamp,
                preview=_observation_preview(observation),
                label=None,
                on_active_path=False,
                entity_ref=None,
            )

        subagent_ref = step.subagent_trajectory_ref if hasattr(step, "subagent_trajectory_ref") else step.get("subagent_trajectory_ref")
        if subagent_ref:
            nodes[f"{step_id}-subagent"] = TreeNode(
                id=f"{step_id}-subagent",
                parent_id=step_id,
                kind="subagent_ref",
                step_index=step_index,
                timestamp=node.timestamp,
                preview=f"subagent {subagent_ref[:8]}",
                label=None,
                on_active_path=False,
                entity_ref={"trace_id": subagent_ref},
            )

        call_type = step.call_type if hasattr(step, "call_type") else step.get("call_type")
        if call_type != "subagent":
            previous_main_step_id = step_id

    roots: list[TreeNode] = []
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=_sort_key)
    roots.sort(key=_sort_key)
    return roots


def flatten_tree(
    tree: list[TreeNode],
    leaf_id: str | None,
    folded: set[str],
    filter_mode: FilterMode,
    search: str,
) -> list[FlatNode]:
    order_map = _original_order(tree)
    parent_map = _parent_map(tree)
    active_path = _active_path_ids(leaf_id, parent_map)
    visible_roots, visible_children, visible_parent = _build_visible_tree(
        tree,
        folded=folded,
        filter_mode=filter_mode,
        search=search,
        active_path=active_path,
        order_map=order_map,
    )
    contains_active = _contains_active(visible_roots, visible_children, active_path)
    ordered_roots = _ordered_nodes(visible_roots, contains_active, order_map)
    flat = _flatten_visible_tree(
        ordered_roots,
        visible_children=visible_children,
        active_path=active_path,
        contains_active=contains_active,
        folded=folded,
        order_map=order_map,
    )
    child_counts = {node_id: len(children) for node_id, children in visible_children.items()}
    for item in flat:
        item.foldable = bool(child_counts.get(item.node.id))
        item.folded = item.node.id in folded
        item.on_active_path = item.node.id in active_path
        item.node.on_active_path = item.on_active_path
    return flat


def format_relative_timestamp(iso_ts: str | None, now=None) -> str:
    if not iso_ts:
        return ""
    return iso_ts


def _step_preview(step) -> str:
    role = step.role if hasattr(step, "role") else step.get("role")
    role = role or "step"
    content = step.content if hasattr(step, "content") else step.get("content")
    content = (content or "").strip()
    if content:
        return f"{role}: {' '.join(content.split())[:120]}"
    tool_calls = step.tool_calls if hasattr(step, "tool_calls") else step.get("tool_calls", [])
    if tool_calls:
        return f"{role}: {len(tool_calls)} tool call(s)"
    return f"{role}: (no content)"


def _tool_preview(tool_call) -> str:
    tool_name = tool_call.tool_name if hasattr(tool_call, "tool_name") else tool_call.get("tool_name")
    tool_input = tool_call.input if hasattr(tool_call, "input") else tool_call.get("input", {})
    return f"{tool_name}: {_first_value(tool_input)}"


def _observation_preview(observation) -> str:
    output_summary = observation.output_summary if hasattr(observation, "output_summary") else observation.get("output_summary")
    content = observation.content if hasattr(observation, "content") else observation.get("content")
    error = observation.error if hasattr(observation, "error") else observation.get("error")
    content = (output_summary or content or error or "").strip()
    compact = " ".join(content.split())
    return compact[:120] if compact else "observation"


def _first_value(payload: dict) -> str:
    for value in payload.values():
        text = " ".join(str(value).split())
        if text:
            return text[:80]
    return ""


def _sort_key(node: TreeNode) -> tuple[str, int]:
    return (node.timestamp or "", node.step_index or 0)


def _original_order(tree: list[TreeNode]) -> dict[str, int]:
    order: dict[str, int] = {}
    stack = list(reversed(tree))
    index = 0
    while stack:
        node = stack.pop()
        order[node.id] = index
        index += 1
        for child in reversed(node.children):
            stack.append(child)
    return order


def _parent_map(tree: list[TreeNode]) -> dict[str, str | None]:
    parent_map: dict[str, str | None] = {}
    stack = list(tree)
    while stack:
        node = stack.pop()
        parent_map[node.id] = node.parent_id
        stack.extend(node.children)
    return parent_map


def _active_path_ids(leaf_id: str | None, parent_map: dict[str, str | None]) -> set[str]:
    active: set[str] = set()
    current = leaf_id
    while current:
        active.add(current)
        current = parent_map.get(current)
    return active


def _passes_filter(node: TreeNode, filter_mode: FilterMode, search: str) -> bool:
    text = f"{node.preview} {node.label or ''}".lower()
    tokens = [token for token in search.lower().split() if token]
    if tokens and not all(token in text for token in tokens):
        return False
    if filter_mode == "all":
        return True
    if filter_mode == "no-tools":
        return node.kind not in {"tool_call", "observation"}
    if filter_mode == "user-only":
        return node.kind == "step" and node.role == "user"
    if filter_mode == "labeled-only":
        return bool(node.label)
    return True


def _build_visible_tree(
    tree: list[TreeNode],
    *,
    folded: set[str],
    filter_mode: FilterMode,
    search: str,
    active_path: set[str],
    order_map: dict[str, int],
) -> tuple[list[TreeNode], dict[str | None, list[TreeNode]], dict[str, str | None]]:
    visible_children: dict[str | None, list[TreeNode]] = {None: []}
    visible_parent: dict[str, str | None] = {}

    def walk(node: TreeNode, visible_parent_id: str | None) -> None:
        current_visible = _passes_filter(node, filter_mode, search)
        next_parent = visible_parent_id
        if current_visible:
            visible_children.setdefault(visible_parent_id, []).append(node)
            visible_children.setdefault(node.id, [])
            visible_parent[node.id] = visible_parent_id
            next_parent = node.id
        if current_visible and node.id in folded:
            return
        for child in node.children:
            walk(child, next_parent)

    for root in tree:
        walk(root, None)

    for parent_id, children in list(visible_children.items()):
        visible_children[parent_id] = _ordered_nodes(children, _contains_active(children, visible_children, active_path), order_map)

    return visible_children[None], visible_children, visible_parent


def _contains_active(
    roots: list[TreeNode],
    children_map: dict[str | None, list[TreeNode]],
    active_path: set[str],
) -> dict[str, bool]:
    contains: dict[str, bool] = {}
    all_nodes: list[TreeNode] = []
    stack = list(roots)
    while stack:
        node = stack.pop()
        all_nodes.append(node)
        stack.extend(children_map.get(node.id, []))
    for node in reversed(all_nodes):
        contains[node.id] = node.id in active_path or any(
            contains.get(child.id, False) for child in children_map.get(node.id, [])
        )
    return contains


def _ordered_nodes(
    nodes: list[TreeNode],
    contains_active: dict[str, bool],
    order_map: dict[str, int],
) -> list[TreeNode]:
    return sorted(
        nodes,
        key=lambda node: (0 if contains_active.get(node.id, False) else 1, order_map.get(node.id, 0)),
    )


def _flatten_visible_tree(
    roots: list[TreeNode],
    *,
    visible_children: dict[str | None, list[TreeNode]],
    active_path: set[str],
    contains_active: dict[str, bool],
    folded: set[str],
    order_map: dict[str, int],
) -> list[FlatNode]:
    result: list[FlatNode] = []
    multiple_roots = len(roots) > 1
    stack: list[tuple[TreeNode, int, bool, bool, bool, list[Gutter], bool]] = []
    for index in range(len(roots) - 1, -1, -1):
        is_last = index == len(roots) - 1
        stack.append((roots[index], 1 if multiple_roots else 0, multiple_roots, multiple_roots, is_last, [], multiple_roots))

    while stack:
        node, indent, just_branched, show_connector, is_last, gutters, is_virtual_root_child = stack.pop()
        result.append(
            FlatNode(
                node=node,
                indent=indent,
                is_last=is_last,
                show_connector=show_connector,
                gutters=gutters,
                foldable=False,
                folded=node.id in folded,
                on_active_path=node.id in active_path,
                is_virtual_root_child=is_virtual_root_child,
            )
        )

        children = visible_children.get(node.id, [])
        multiple_children = len(children) > 1
        ordered_children = _ordered_nodes(children, contains_active, order_map)

        if multiple_children:
            child_indent = indent + 1
        elif just_branched and indent > 0:
            child_indent = indent + 1
        else:
            child_indent = indent

        connector_displayed = show_connector and not is_virtual_root_child
        current_display_indent = max(0, indent - 1) if multiple_roots else indent
        connector_position = max(0, current_display_indent - 1)
        child_gutters = (
            [*gutters, Gutter(position=connector_position, show=not is_last)]
            if connector_displayed
            else gutters
        )

        for index in range(len(ordered_children) - 1, -1, -1):
            child_is_last = index == len(ordered_children) - 1
            stack.append(
                (
                    ordered_children[index],
                    child_indent,
                    multiple_children,
                    multiple_children,
                    child_is_last,
                    child_gutters,
                    False,
                )
            )

    return result
