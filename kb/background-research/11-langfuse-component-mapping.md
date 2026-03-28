# Langfuse Component Mapping for OpenTraces React Dashboard

> Research date: 2026-03-28
> Purpose: Field-by-field mapping from OpenTraces schema to portable Langfuse components
> Usage: Reference this document when building the React dashboard to know exactly what to port, adapt, or build fresh

---

## Part 1: Complete OpenTraces Schema with Component Mapping

### TraceRecord (top-level) → Langfuse `TracePage` + `TracePreview`

| Field | Type | Langfuse Component | Source Path | Action |
|-------|------|--------------------|-------------|--------|
| `schema_version` | `str` | — | — | No UI needed |
| `trace_id` | `str` | `CopyIdsPopover` | `web/src/components/trace2/components/_shared/CopyIdsPopover.tsx` | **Port directly** — click-to-copy ID popover |
| `session_id` | `str` | Session badge with link | `TracePreview.tsx:273-282` — `<Badge>Session: {trace.sessionId}</Badge>` with `<Link>` | **Port directly** |
| `content_hash` | `str | None` | — | — | No UI needed (dedup internal) |
| `timestamp_start` | `str | None` | `LocalIsoDate` | `web/src/components/LocalIsoDate.tsx` | **Port directly** — formats ISO dates with millisecond accuracy |
| `timestamp_end` | `str | None` | Duration badge | `TracePreview.tsx:313-316` — `formatIntervalSeconds(trace.latency)` | **Port** — compute `end - start` for latency |
| `task` | `Task` | **BUILD FRESH** — Task header card | — | Langfuse has no task concept. Build a header card showing task description, source, repo |
| `agent` | `Agent` | `ItemBadge` + metadata badges | `web/src/components/ItemBadge.tsx` | **Adapt** — use for agent type badge (claude-code, cursor, etc.) |
| `environment` | `Environment` | Environment badge | `TracePreview.tsx:308-309` — `<Badge variant="tertiary">Env: {trace.environment}</Badge>` | **Port directly** |
| `system_prompts` | `dict[str, str]` | `IOPreview` (expandable JSON) | `web/src/components/trace2/components/IOPreview/IOPreview.tsx` | **Adapt** — render as collapsible system prompt viewer |
| `tool_definitions` | `list[dict]` | — | — | Optional: collapsible "Available Tools" panel |
| `steps` | `list[Step]` | **Core: Tree + Timeline + Detail** | See Step mapping below | **Port + Adapt** (biggest work) |
| `outcome` | `Outcome` | **BUILD FRESH** — Outcome card | — | Langfuse has no outcome concept. Build committed/success status card |
| `dependencies` | `list[str]` | Badge list | Reuse `TagList` pattern from `web/src/features/tag/components/TagList.tsx` | **Port** |
| `metrics` | `Metrics` | `AggUsageBadge` + `BreakdownTooltip` | `web/src/components/token-usage-badge.tsx`, `web/src/components/trace2/components/_shared/BreakdownToolTip.tsx` | **Port directly** |
| `security` | `SecurityMetadata` | **BUILD FRESH** — Security tier badge | — | Build tier 1/2/3 badge with flag/redaction counts |
| `attribution` | `Attribution | None` | **BUILD FRESH** — File attribution view | — | Langfuse has nothing like this. Build a file tree with highlighted line ranges |
| `metadata` | `dict[str, Any]` | `PrettyJsonView` | `web/src/components/ui/PrettyJsonView.tsx` | **Port directly** |

### Task → Build Fresh

| Field | Type | UI Element |
|-------|------|------------|
| `description` | `str | None` | Main heading text (like Langfuse trace name but richer) |
| `source` | `str | None` | Badge: "user_prompt", "cli_arg", "skill" |
| `repository` | `str | None` | Badge with link: `owner/repo` |
| `base_commit` | `str | None` | Monospace short-hash badge |

**Design**: A card at the top of the trace detail page, like Langfuse's `TracePreview` header but with task-specific fields instead of just a name.

### Agent → Adapt from ItemBadge

| Field | Type | UI Element |
|-------|------|------------|
| `name` | `str` | Color-coded badge: claude-code=green, cursor=blue, codex=purple |
| `version` | `str | None` | Version badge (like Langfuse's `Release: {trace.release}`) |
| `model` | `str | None` | Model badge (like Langfuse's model display in `ObservationDetailViewHeader`) |

**Port from**: `web/src/components/ItemBadge.tsx` — extend the type map to include agent types.

### Environment → Port from TracePreview badges

| Field | Type | Langfuse Source |
|-------|------|----------------|
| `os` | `str | None` | New badge: `<Badge variant="tertiary">OS: darwin</Badge>` |
| `shell` | `str | None` | New badge |
| `vcs.type` | `"git" | "none"` | Conditional: show git badges only if type="git" |
| `vcs.branch` | `str | None` | Badge: `<Badge>Branch: main</Badge>` |
| `vcs.base_commit` | `str | None` | Monospace badge, first 7 chars |
| `vcs.diff` | `str | None` | Collapsible diff viewer (like Langfuse's `TraceLogView` code blocks) |
| `language_ecosystem` | `list[str]` | Tag list (port from `TagList`) |

### Step → Core Mapping (Port + Adapt from trace2/)

This is the most important mapping. Each `Step` maps to a tree node in the navigation panel.

| Field | Type | Langfuse Equivalent | Component Source | Action |
|-------|------|---------------------|------------------|--------|
| `step_index` | `int` | Observation ordering | Built into tree-building algorithm | Used for sort order |
| `role` | `"system" | "user" | "agent"` | `ObservationType` | `ItemBadge.tsx` | **Adapt** — map roles to type badges (user=blue, agent=green, system=gray) |
| `content` | `str | None` | `IOPreview` input/output | `web/src/components/trace2/components/IOPreview/IOPreview.tsx` | **Port directly** — render as formatted text/markdown |
| `reasoning_content` | `str | None` | — | — | **BUILD FRESH** — collapsible "Thinking" section with italic styling (your Gradio CSS `.trace-reasoning` is a good starting point) |
| `model` | `str | None` | Model badge in `ObservationDetailViewHeader` | `web/src/components/trace2/components/ObservationDetailView/ObservationDetailViewHeader.tsx` | **Port** |
| `system_prompt_hash` | `str | None` | — | — | Link to system_prompts map, show as badge if present |
| `agent_role` | `str | None` | Agent role badge | — | **BUILD FRESH** — badge: "main", "explore", "plan" |
| `parent_step` | `int | None` | `parentObservationId` | `web/src/components/trace2/lib/tree-building.ts` | **CRITICAL PORT** — this is how the tree is built. Map `parent_step` → `parentObservationId` in the tree builder |
| `call_type` | `"main" | "subagent" | "warmup"` | — | — | **BUILD FRESH** — visual indicator: subagent steps get indented left border (like your Gradio `.trace-subagent`) |
| `subagent_trajectory_ref` | `str | None` | — | — | Link badge to external trace |
| `tools_available` | `list[str]` | Tool availability display | Langfuse has this in `ObservationDetailView` | Optional: "Tools Available" collapsible |
| `tool_calls` | `list[ToolCall]` | Observation children | See ToolCall mapping | **Port + Adapt** |
| `observations` | `list[Observation]` | Observation results | See Observation mapping | **Port + Adapt** |
| `snippets` | `list[Snippet]` | — | — | **BUILD FRESH** — code snippet viewer with syntax highlighting |
| `token_usage` | `TokenUsage` | `AggUsageBadge` | `web/src/components/token-usage-badge.tsx` | **Port directly** |
| `timestamp` | `str | None` | Timeline position | `timeline-calculations.ts` | Used to position on Gantt chart |

### ToolCall → Adapt from Langfuse's tool observation rendering

| Field | Type | UI Element |
|-------|------|------------|
| `tool_call_id` | `str` | Internal ID for linking to Observation |
| `tool_name` | `str` | Color-coded tool badge: Read=blue, Edit=yellow, Bash=green, Grep=purple, Write=orange, Glob=cyan |
| `input` | `dict[str, Any]` | `PrettyJsonView` — render tool input as formatted JSON/key-value |
| `duration_ms` | `int | None` | Duration badge + timeline bar width |

**Port from**: `web/src/components/trace2/components/SpanContent.tsx` for the inline display, `web/src/components/trace2/components/ObservationDetailView/ObservationDetailView.tsx` for the detail panel.

**Key adaptation**: In Langfuse, tool calls are separate observations in the tree. In OpenTraces, they're nested inside steps. You have two choices:

1. **Flat tree** (simpler): Each step is a tree node. Tool calls shown inline in the detail panel.
2. **Expanded tree** (richer): Each step is a tree node, each tool_call is a child node. This matches Langfuse's model better and enables the timeline view.

**Recommendation**: Option 2. Transform during tree building:
```
Step (role=agent, step_index=5, parent_step=3)
  ├── ToolCall (tool_name=Read, tool_call_id=tc1)
  ├── ToolCall (tool_name=Edit, tool_call_id=tc2)
  └── ToolCall (tool_name=Bash, tool_call_id=tc3)
```

### Observation (tool result) → Adapt from IOPreview

| Field | Type | UI Element |
|-------|------|------------|
| `source_call_id` | `str` | Links back to ToolCall — show result inline with the tool call |
| `content` | `str | None` | Full output in detail panel (use `IOPreview` or code block) |
| `output_summary` | `str | None` | Truncated preview in tree node (like `SpanContent` name truncation) |
| `error` | `str | None` | Error badge (red) — like Langfuse's `level=ERROR` styling |

### TokenUsage → Port directly from AggUsageBadge

| Field | Type | Langfuse Component |
|-------|------|-------------------|
| `input_tokens` | `int` | `AggUsageBadge` — "↓ 5,000" |
| `output_tokens` | `int` | `AggUsageBadge` — "↑ 1,200" |
| `cache_read_tokens` | `int` | **Extend** — Langfuse doesn't show cache tokens. Add "Cache: 3,000" badge |
| `cache_write_tokens` | `int` | **Extend** — "Cache Write: 1,000" badge |
| `prefix_reuse_tokens` | `int` | **Extend** — optional badge |

**Port from**: `web/src/components/token-usage-badge.tsx` (AggUsageBadge) and `web/src/components/trace2/components/_shared/BreakdownToolTip.tsx` (hover tooltip with breakdown).

### Outcome → Build Fresh (no Langfuse equivalent)

| Field | Type | UI Element |
|-------|------|------------|
| `success` | `bool | None` | Large status indicator: green check / red X / gray question mark |
| `signal_source` | `str` | Small label: "deterministic", etc. |
| `signal_confidence` | `"derived" | "inferred" | "annotated"` | Confidence badge with tooltip explaining what it means |
| `description` | `str | None` | Description text below status |
| `patch` | `str | None` | Collapsible diff viewer (use CodeMirror or simple `<pre>` with diff highlighting) |
| `committed` | `bool` | Prominent badge: "Committed" (green) / "Not committed" (gray) |
| `commit_sha` | `str | None` | Monospace badge, first 7 chars, with copy button |

**Design**: A card in the detail panel, positioned after the I/O preview. Use Langfuse's `Badge` component for all badges.

### Metrics → Port from dashboard BigNumber + BreakdownTooltip

| Field | Type | Langfuse Component | Source |
|-------|------|--------------------|--------|
| `total_steps` | `int` | BigNumber card | `web/src/features/widgets/chart-library/BigNumber.tsx` |
| `total_input_tokens` | `int` | BigNumber or AggUsageBadge | `token-usage-badge.tsx` |
| `total_output_tokens` | `int` | Same | Same |
| `total_duration_s` | `float | None` | Duration badge | `formatIntervalSeconds` from `web/src/utils/dates.ts` |
| `cache_hit_rate` | `float | None` | Percentage badge | **BUILD** — simple percentage badge |
| `estimated_cost_usd` | `float | None` | Cost badge with USD formatter | `web/src/utils/numbers.ts` — `usdFormatter` |

### SecurityMetadata → Build Fresh

| Field | Type | UI Element |
|-------|------|------------|
| `tier` | `1 | 2 | 3` | Color-coded tier badge: 1=green "Open", 2=yellow "Guarded", 3=red "Strict" |
| `flags_reviewed` | `int` | Badge: "3 flags reviewed" |
| `redactions_applied` | `int` | Badge: "2 redactions" |
| `classifier_version` | `str | None` | Small metadata text |

### Attribution → Build Fresh (unique to OpenTraces)

| Field | Type | UI Element |
|-------|------|------------|
| `version` | `str` | Metadata |
| `experimental` | `bool` | Warning banner: "Attribution is experimental" |
| `files` | `list[AttributionFile]` | **File tree with highlighted ranges** |

**Design for AttributionFile**:
```
📄 src/main.py
   └── Lines 1-34 (high confidence) — AI contributor, step #34
📄 scripts/engine.py
   └── Lines 1-8 (high confidence) — AI contributor, step #52
```

Use a collapsible tree (port `VirtualizedTree` pattern from `web/src/components/trace2/components/_shared/VirtualizedTree.tsx`) with each file as a node and ranges as children. Link `url: "opentraces://trace/step_N"` to scroll-to-step in the main trace view.

---

## Part 2: Component-by-Component Port Guide

### Components to Port Directly (copy + adjust imports)

| Component | Langfuse Source | What It Does | Modifications Needed |
|-----------|----------------|--------------|---------------------|
| `CopyIdsPopover` | `web/src/components/trace2/components/_shared/CopyIdsPopover.tsx` | Click-to-copy ID with popover | None |
| `LocalIsoDate` | `web/src/components/LocalIsoDate.tsx` | Format ISO dates | None |
| `Badge` | `web/src/components/ui/badge.tsx` | Styled badge | Add OpenTraces variants (security tiers, tool types) |
| `ItemBadge` | `web/src/components/ItemBadge.tsx` | Type-colored badge | Extend type map for: USER, AGENT, SYSTEM, TOOL, SUBAGENT |
| `PrettyJsonView` | `web/src/components/ui/PrettyJsonView.tsx` | Formatted JSON viewer | None |
| `Tabs` / `TabsBar` | `web/src/components/ui/tabs.tsx`, `tabs-bar.tsx` | Tab navigation | None |
| `Button` | `web/src/components/ui/button.tsx` | Button | None |
| `Tooltip` / `TooltipProvider` | `web/src/components/ui/tooltip.tsx` | Tooltips | None |
| `Switch` | `web/src/components/ui/switch.tsx` | Toggle switch | None |
| `AlertDialog` | `web/src/components/ui/alert-dialog.tsx` | Confirmation dialogs | None |
| `formatIntervalSeconds` | `web/src/utils/dates.ts` | Duration formatting | None |
| `usdFormatter` | `web/src/utils/numbers.ts` | Cost formatting | None |

### Components to Port + Adapt (structural changes needed)

| Component | Langfuse Source | Adaptation |
|-----------|----------------|------------|
| **Tree Builder** | `web/src/components/trace2/lib/tree-building.ts` | Change input type: `Step[]` instead of `Observation[]`. Map `parent_step` → parent-child. Map `role` → node type. Expand `tool_calls` as child nodes. |
| **TreeNode type** | `web/src/components/trace2/lib/types.ts` | Add fields: `role`, `call_type`, `agent_role`, `tool_calls`, `observations`. Remove Langfuse-specific: `traceId`, `parentObservationId` |
| **VirtualizedTree** | `web/src/components/trace2/components/_shared/VirtualizedTree.tsx` | Minimal changes — just update TreeNode type references |
| **VirtualizedTreeNodeWrapper** | `web/src/components/trace2/components/_shared/VirtualizedTreeNodeWrapper.tsx` | Update type badges for OpenTraces node types |
| **SpanContent** | `web/src/components/trace2/components/SpanContent.tsx` | Major rewrite: show step role, tool names, content preview, cache tokens, subagent indicator |
| **TraceTimeline** | `web/src/components/trace2/components/TraceTimeline/index.tsx` | Change data source from TreeNode to OpenTraces TreeNode. Adapt `flattenTreeWithTimelineMetrics` for step timestamps |
| **TimelineBar** | `web/src/components/trace2/components/TraceTimeline/TimelineBar.tsx` | Remove first-token-time split (not relevant for code agents). Add tool-name label on bars |
| **IOPreview** | `web/src/components/trace2/components/IOPreview/IOPreview.tsx` | Adapt for: step `content` as input, observation `content` as output, `reasoning_content` as expandable section |
| **AggUsageBadge** | `web/src/components/token-usage-badge.tsx` | Add cache_read_tokens and cache_write_tokens to the breakdown |
| **BreakdownTooltip** | `web/src/components/trace2/components/_shared/BreakdownToolTip.tsx` | Extend data shape for OpenTraces token fields |
| **TraceLayoutDesktop** | `web/src/components/trace2/components/_layout/TraceLayoutDesktop.tsx` | Minimal changes — uses `react-resizable-panels` |
| **TracePanelNavigation** | `web/src/components/trace2/components/_layout/TracePanelNavigation.tsx` | Same pattern: Tree vs Timeline vs Search routing |
| **TracePanelDetail** | `web/src/components/trace2/components/_layout/TracePanelDetail.tsx` | Adapt for OpenTraces detail views (step detail, tool call detail, trace overview) |

### Components to Build Fresh (no Langfuse equivalent)

| Component | Purpose | Design Reference |
|-----------|---------|-----------------|
| **TaskCard** | Show task description, source, repository, base_commit | Langfuse `TracePreview` header pattern but with richer fields |
| **OutcomeCard** | Show success/committed/patch with confidence signals | Use Badge + collapsible diff viewer |
| **SecurityBadge** | Show tier 1/2/3 with flag/redaction counts | Color-coded badge: green/yellow/red |
| **ReasoningBlock** | Collapsible chain-of-thought content | Styled like your Gradio `.trace-reasoning`: italic, left-border, muted opacity |
| **AttributionTree** | File tree with line ranges and confidence | Port VirtualizedTree pattern, custom node renderer for files/ranges |
| **SubagentIndicator** | Visual indicator for subagent steps (indented, colored border) | Your Gradio `.trace-subagent` CSS is the right pattern — left border + indent |
| **ToolCallDetail** | Expanded view of a tool call with input dict + observation result | Combine PrettyJsonView (for input) + code block (for output) |
| **CacheEfficiencyBadge** | Show cache_hit_rate as a visual metric | Circular progress or percentage bar |
| **CommitBadge** | Linked commit SHA with copy button | Monospace badge + CopyIdsPopover pattern |

### Context Providers to Port (architecture pattern)

| Context | Langfuse Source | Adaptation |
|---------|----------------|------------|
| `TraceDataProvider` | `web/src/components/trace2/contexts/TraceDataContext.tsx` | Change type: `TraceRecord` + derived tree. Call adapted `buildTraceUiData()` |
| `SelectionProvider` | `web/src/components/trace2/contexts/SelectionContext.tsx` | Port directly — same pattern works |
| `ViewPreferencesProvider` | `web/src/components/trace2/contexts/ViewPreferencesContext.tsx` | Add: `showReasoning`, `showSubagents`, `showCacheMetrics`. Remove: `showGraph` (maybe) |
| `SearchProvider` | `web/src/components/trace2/contexts/SearchContext.tsx` | Port directly |
| `JsonExpansionProvider` | `web/src/components/trace2/contexts/JsonExpansionContext.tsx` | Port directly |

---

## Part 3: Tree Building Algorithm Adaptation

This is the most critical piece. Langfuse's tree builder (`tree-building.ts`) takes a flat array of `Observation[]` with `parentObservationId` and builds a tree. OpenTraces has `Step[]` with `parent_step` (step index).

### Input Transformation

```typescript
// OpenTraces Step → intermediate format for tree builder
interface OpenTracesTreeInput {
  id: string;           // `step_${step.step_index}`
  parentId: string | null; // `step_${step.parent_step}` or null
  type: "USER" | "AGENT" | "SYSTEM" | "TOOL";
  name: string;         // Derived: first 60 chars of content, or tool_name, or role
  startTime: Date;      // step.timestamp
  endTime: Date | null;  // next step's timestamp or trace.timestamp_end
  role: string;         // step.role
  callType: string;     // step.call_type
  agentRole: string;    // step.agent_role
  toolCalls: ToolCall[];
  observations: Observation[];
  tokenUsage: TokenUsage;
}
```

### Tree Expansion Strategy

For each Step with tool_calls, optionally expand into child nodes:

```
Step 5 (agent, parent=3, subagent)
├── Read /src/main.py (tool_call_id=tc1, duration=120ms)
├── Edit /src/main.py (tool_call_id=tc2, duration=null)
└── Bash: python -m pytest (tool_call_id=tc3, duration=3400ms)
```

This gives you a tree that looks like:
```
Step 1 (user) — "I would like to change where reports..."
Step 2 (agent, main) — "I'll start by reading the relevant files..."
  ├── Read (tc1) — /src/config.py
  ├── Read (tc2) — /src/reports.py
  └── Grep (tc3) — pattern: "save_report"
Step 3 (agent, main) — "Let me make the changes..."
  └── Edit (tc4) — /src/reports.py
Step 4 (user) — "Yes, that looks correct."
Step 5 (agent, subagent, parent=3) — exploring...
  ├── Bash (tc5) — ls /skills/deep-research
  └── Read (tc6) — /skills/deep-research/SKILL.md
```

### Real data from your traces

Your traces have:
- **65 steps** (2 user, 63 agent)
- **14 subagent steps** (with `parent_step` set, `call_type: "subagent"`)
- **38 steps with tool calls** (tools: Read, Edit, Bash, Grep, Glob, Agent)
- **1 warmup step** (call_type: "warmup")

This means the tree will have meaningful hierarchy, not just a flat list:
- Main conversation thread (50 main steps)
- Branching subagent explorations (14 subagent steps forming subtrees)
- Tool call expansion (38 steps × 1-3 tool calls each = ~60-100 tool nodes)

Total tree nodes: ~160-170 for a 65-step trace. Well within VirtualizedTree's capacity.

---

## Part 4: Dashboard Page — Chart Library Mapping

Your Gradio explorer already has these views. Map them to Langfuse chart components:

### Trace List Page (Search & Browse tab)

| Gradio Current | React Target | Langfuse Component | Source |
|---------------|--------------|-------------------|--------|
| `gr.Dataframe` (results table) | `TracesTable` pattern | `@tanstack/react-table` + `DataTable` | `web/src/components/table/use-cases/traces.tsx` |
| Dropdown filters | `PopoverFilterBuilder` | `web/src/features/filters/components/filter-builder.tsx` |
| Trace detail HTML | `TracePage` in peek panel | Slide-out panel pattern from `web/src/components/table/peek/peek-trace-detail.tsx` |

**Trace list columns** (adapt from Langfuse's traces table):

| Column | Source Field | Langfuse Reference |
|--------|-------------|-------------------|
| Trace ID (truncated + copy) | `trace_id` | `CopyIdsPopover` |
| Task | `task.description` | Text cell with truncation |
| Agent | `agent.name` | `ItemBadge` |
| Model | `agent.model` | Text badge |
| Steps | `metrics.total_steps` | Number cell |
| Tokens | `metrics.total_input_tokens + total_output_tokens` | `AggUsageBadge` |
| Cost | `metrics.estimated_cost_usd` | `usdFormatter` |
| Duration | `metrics.total_duration_s` | `formatIntervalSeconds` |
| Outcome | `outcome.committed` | Green/gray badge |
| Security | `security.tier` | Tier badge |
| Date | `timestamp_start` | `LocalIsoDate` |
| Ecosystem | `environment.language_ecosystem` | Tag badges |

**Filters** (adapt from Langfuse's filter config):
- Agent name, model, ecosystem, security tier, outcome (committed/not), date range

### Community Dashboard (Stats tab)

| Gradio Current | React Target | Langfuse Chart | Source |
|---------------|--------------|----------------|--------|
| Metric cards (4-grid) | `BigNumber` widget | `web/src/features/widgets/chart-library/BigNumber.tsx` |
| Agent distribution pie | `PieChart` widget | `web/src/features/widgets/chart-library/PieChart.tsx` |
| Model distribution pie | `PieChart` widget | Same |
| Language ecosystem bars | `VerticalBarChart` widget | `web/src/features/widgets/chart-library/VerticalBarChart.tsx` |
| Recent activity table | HTML table | Standard `@tanstack/react-table` |

### Contributor Dashboard

| Gradio Current | React Target | Langfuse Chart | Source |
|---------------|--------------|----------------|--------|
| Sessions over time | `AreaChartTimeSeries` | `web/src/features/widgets/chart-library/AreaChartTimeSeries.tsx` |
| Model distribution | `PieChart` | Same as above |
| Token usage bars | `VerticalBarChart` | Same as above |
| Tool usage horizontal bars | `HorizontalBarChart` | `web/src/features/widgets/chart-library/HorizontalBarChart.tsx` |

### Dashboard Grid Layout

Port `DashboardGrid` (`web/src/features/widgets/components/DashboardGrid.tsx`) which uses `react-grid-layout` for drag-and-drop resizable widgets.

---

## Part 5: Data Layer Adaptation

### Langfuse: tRPC + React Query → OpenTraces: HF Datasets API + React Query

Langfuse fetches data via tRPC:
```typescript
api.traces.byIdWithObservationsAndScores.useQuery({ traceId, projectId })
```

OpenTraces will fetch from HuggingFace Datasets:
```typescript
// Option A: Fetch JSONL directly from HF Hub
const { data } = useQuery({
  queryKey: ['trace', traceId],
  queryFn: () => fetchTraceFromHF(datasetId, traceId),
})

// Option B: Client-side Parquet parsing (for list views)
const { data } = useQuery({
  queryKey: ['traces', filters],
  queryFn: () => fetchTracesFromParquet(datasetId, filters),
})
```

**Key difference**: No server-side query engine. All filtering/aggregation happens client-side or via HF Dataset Viewer API. This means:
- Keep React Query for caching/state management (port from Langfuse)
- Replace tRPC calls with HF API fetch functions
- Dashboard aggregations computed client-side (no ClickHouse)

### File Structure (recommended)

```
src/
  components/
    ui/           # Port shadcn components (badge, button, tabs, tooltip, etc.)
    trace/        # Port from trace2/
      lib/
        types.ts          # Adapted TreeNode + TraceSearchListItem
        tree-building.ts  # Adapted for Step[] input
        helpers.ts        # Port heatmap color, formatters
      contexts/
        TraceDataContext.tsx
        SelectionContext.tsx
        ViewPreferencesContext.tsx
        SearchContext.tsx
      components/
        TraceTree.tsx
        TraceTimeline/
        SpanContent.tsx
        IOPreview/
        TaskCard.tsx          # NEW
        OutcomeCard.tsx       # NEW
        SecurityBadge.tsx     # NEW
        ReasoningBlock.tsx    # NEW
        AttributionTree.tsx   # NEW
        SubagentIndicator.tsx # NEW
        ToolCallDetail.tsx    # NEW
      layout/
        TraceLayoutDesktop.tsx
        TracePanelNavigation.tsx
        TracePanelDetail.tsx
    dashboard/
      charts/       # Port from widgets/chart-library/
        BigNumber.tsx
        PieChart.tsx
        AreaChartTimeSeries.tsx
        VerticalBarChart.tsx
        HorizontalBarChart.tsx
      DashboardGrid.tsx
    table/
      TracesTable.tsx  # Adapted trace list
  hooks/
    useTraceData.ts    # HF API data fetching
    useParsedTrace.ts  # Port Web Worker parsing
  lib/
    hf-api.ts          # HuggingFace Datasets API client
    formatters.ts      # Port from utils/dates.ts, utils/numbers.ts
```

---

## Part 6: Priority Order for Implementation

### Phase 1 — Core Trace Viewer (1 week)
1. Port shadcn/ui base components (Badge, Button, Tabs, Tooltip)
2. Adapt tree-building algorithm for Step[] input
3. Port TraceTree + VirtualizedTree
4. Build SpanContent for OpenTraces step rendering
5. Port IOPreview for step content display
6. Build TaskCard and OutcomeCard
7. Port TraceLayoutDesktop (resizable panels)
8. Wire up with React Query + HF API

### Phase 2 — Enhanced Visualization (1 week)
1. Port TraceTimeline (Gantt chart)
2. Build ReasoningBlock
3. Build AttributionTree
4. Build SecurityBadge and SubagentIndicator
5. Port TracePanelNavigation (tree/timeline/search toggle)
6. Add ToolCallDetail expanded view
7. Port AggUsageBadge with cache token extensions

### Phase 3 — Dashboard & List (1 week)
1. Build TracesTable with filters
2. Port BigNumber, PieChart, AreaChartTimeSeries, BarCharts
3. Port DashboardGrid
4. Build community stats aggregation (client-side)
5. Build contributor dashboard

---

## Appendix: Key Langfuse Files Quick Reference

For when you're actually building — the exact files to open:

```
# Tree building (most important)
web/src/components/trace2/lib/tree-building.ts
web/src/components/trace2/lib/types.ts
web/src/components/trace2/lib/helpers.ts

# Context providers (architecture)
web/src/components/trace2/contexts/TraceDataContext.tsx
web/src/components/trace2/contexts/SelectionContext.tsx
web/src/components/trace2/contexts/ViewPreferencesContext.tsx
web/src/components/trace2/contexts/SearchContext.tsx

# Layout
web/src/components/trace2/components/_layout/TraceLayoutDesktop.tsx
web/src/components/trace2/components/_layout/TracePanelNavigation.tsx
web/src/components/trace2/components/_layout/TracePanelDetail.tsx
web/src/components/trace2/components/_layout/TracePanelNavigationHeader.tsx

# Tree view
web/src/components/trace2/components/TraceTree.tsx
web/src/components/trace2/components/_shared/VirtualizedTree.tsx
web/src/components/trace2/components/_shared/VirtualizedTreeNodeWrapper.tsx
web/src/components/trace2/components/SpanContent.tsx

# Timeline view
web/src/components/trace2/components/TraceTimeline/index.tsx
web/src/components/trace2/components/TraceTimeline/TimelineBar.tsx
web/src/components/trace2/components/TraceTimeline/TimelineRow.tsx
web/src/components/trace2/components/TraceTimeline/TimelineScale.tsx
web/src/components/trace2/components/TraceTimeline/timeline-calculations.ts
web/src/components/trace2/components/TraceTimeline/timeline-flattening.ts

# Detail views
web/src/components/trace2/TracePreview.tsx
web/src/components/trace2/components/IOPreview/IOPreview.tsx
web/src/components/trace2/components/TraceDetailView/TraceDetailView.tsx
web/src/components/trace2/components/ObservationDetailView/ObservationDetailView.tsx

# UI primitives
web/src/components/ui/badge.tsx
web/src/components/ui/button.tsx
web/src/components/ui/tabs.tsx
web/src/components/ui/tabs-bar.tsx
web/src/components/ui/tooltip.tsx
web/src/components/ui/PrettyJsonView.tsx
web/src/components/ItemBadge.tsx
web/src/components/token-usage-badge.tsx
web/src/components/trace2/components/_shared/BreakdownToolTip.tsx
web/src/components/trace2/components/_shared/CopyIdsPopover.tsx

# Charts
web/src/features/widgets/chart-library/Chart.tsx
web/src/features/widgets/chart-library/BigNumber.tsx
web/src/features/widgets/chart-library/PieChart.tsx
web/src/features/widgets/chart-library/AreaChartTimeSeries.tsx
web/src/features/widgets/chart-library/VerticalBarChart.tsx
web/src/features/widgets/chart-library/HorizontalBarChart.tsx
web/src/features/widgets/chart-library/LineChartTimeSeries.tsx

# Dashboard grid
web/src/features/widgets/components/DashboardGrid.tsx
web/src/features/widgets/components/DashboardWidget.tsx

# Formatters
web/src/utils/dates.ts
web/src/utils/numbers.ts

# Trace list table
web/src/components/table/use-cases/traces.tsx
web/src/features/filters/config/traces-config.ts
```
