# Langfuse: R&D Scouting Brief

> Research date: 2026-03-28
> Source: https://github.com/langfuse/langfuse
> Category: platform
> Focus: Frontend trace visualization, dashboard architecture, bootstrapping guide for agent trace observability

---

## Overview

Langfuse is an open-source LLM engineering platform (YC W23) providing observability, tracing, evaluation, prompt management, and custom dashboards for AI applications. Built with TypeScript/Next.js, backed by ClickHouse for analytics and Postgres for application state. Acquired by ClickHouse in January 2026. 23.9k GitHub stars, 2.4k forks.

## Problem It Solves

Traditional APM tools (Datadog, New Relic) can tell you if your service is up and fast, but reveal nothing about whether LLM outputs are correct, helpful, or safe. Langfuse provides AI-quality monitoring: structured traces of every LLM call, tool invocation, and retrieval step, with cost/latency/token tracking and evaluation workflows.

## How It Works

### Architecture Overview

**Monorepo structure** (pnpm workspaces + Turborepo 2.8.20):
- `web/` — Next.js 16.2.1 application (Pages Router + App Router hybrid), main UI + tRPC + REST API
- `worker/` — Express.js background job processor (BullMQ consumers, 30+ named queues)
- `packages/shared/` — Shared types, Prisma schema, ClickHouse client/repos, domain logic, queue definitions
- `ee/` — Enterprise Edition features (SSO, billing, audit logs, admin API)
- `packages/config-eslint/`, `packages/config-typescript/` — Shared configs

**Data flow — Trace Ingestion to UI:**
1. SDKs (Python/JS) or OpenTelemetry send trace events via REST API (`POST /api/public/ingestion`)
2. Events are validated and queued via Redis/BullMQ
3. Worker processes write to both Postgres (application state) and ClickHouse (analytics)
4. Frontend fetches trace data via tRPC (`api.traces.byIdWithObservationsAndScores`)
5. Tree is built client-side from flat observation array (`tree-building.ts`)
6. React context providers distribute data to Tree/Timeline/Graph/Detail views

**Database dual-stack:**
- **Postgres 17** (Prisma 6.17.1 ORM, 1628-line schema, 390+ migrations) — Projects, users, API keys, dashboard definitions, prompt management
- **ClickHouse** (via `@clickhouse/client ^1.13.0`, golang-migrate for migrations) — Traces, observations, scores, metrics (high-volume analytical queries). Tables: `traces`, `observations`, `scores`, `dataset_run_items_rmt`, `events_core`, `events_full`. Supports clustered (ReplicatedMergeTree) and non-clustered modes
- **Redis 7** (ioredis) — BullMQ job queues (30+ named queues, sharded by `projectId-eventBodyId`), API key caching, rate limiting

### Key Concepts

- **Trace**: Container representing a single request/operation. Has `id`, `name`, `userId`, `sessionId`, `tags`, `metadata`, `environment`, `release`, `version`
- **Observation**: Individual step within a trace. Typed subtypes: SPAN, GENERATION, EVENT, AGENT, TOOL, CHAIN, RETRIEVER, EVALUATOR, EMBEDDING. Nestable via `parentObservationId`
- **Session**: Optional grouping of related traces (e.g., a chat conversation)
- **Score**: Evaluation result attached to a trace or observation (LLM-as-judge, human annotation, programmatic)
- **V4 Data Model (Beta)**: Observation-centric — context attributes (`user_id`, `session_id`, `metadata`, `tags`) propagated to every observation, eliminating expensive joins. Single immutable observations table

### Core API / Interface

**tRPC Routers** (internal API):
- `traces.ts` — `byIdWithObservationsAndScores`, `hasTracingConfigured`, trace list queries
- `observations.ts` — Individual observation queries
- `scores.ts` — Score CRUD, score configs
- `dashboardWidgets.ts` — Widget data queries
- `sessions.ts` — Session grouping
- `generations/` — Generation-specific queries (directory with sub-routers)

**Public REST API:**
- `POST /api/public/ingestion` — Main trace/observation/score ingestion endpoint
- `GET /api/public/metrics` — Dashboard metrics API (same query engine as UI widgets)

**Auth:**
- NextAuth for user sessions (UI)
- API keys for SDK authentication: Basic Auth (`publicKey:secretKey`) for full project scope, Bearer Auth (`publicKey`) for scores-only scope
- API key verification: SHA-256 fast hash → Redis cache → fallback Postgres bcrypt → migrate to fast hash

**Ingestion Pipeline (6 stages):**
1. SDK sends batch → `POST /api/public/ingestion` (`web/src/pages/api/public/ingestion.ts`)
2. Auth + rate limit check → schema validation (Zod discriminated union on event `type`)
3. Events grouped by `eventBodyId`, uploaded to S3 (`LANGFUSE_S3_EVENT_UPLOAD_BUCKET`)
4. Enqueued to Redis/BullMQ `IngestionQueue` (sharded, 5s delay for out-of-order events)
5. Worker reads from queue → fetches from S3 → model matching + token counting → merges with existing ClickHouse records (read-before-write upsert)
6. `ClickhouseWriter` singleton batches inserts (flush on interval or batch size threshold)

**Ingestion event types**: `trace-create`, `score-create`, `span-create/update`, `generation-create/update`, `event-create`, `agent-create`, `tool-create`, `chain-create`, `retriever-create`, `evaluator-create`, `embedding-create`, `guardrail-create`, `sdk-log`

---

## Frontend Architecture — Trace Visualization Deep Dive

This is the highest-value section for bootstrapping a similar dashboard.

### Component Hierarchy

```
TracePage (web/src/components/trace2/TracePage.tsx)
  └── Trace (web/src/components/trace2/Trace.tsx)
        │   ← 6 context providers wrapping all children:
        │      ViewPreferencesProvider → TraceDataProvider → TraceGraphDataProvider
        │      → SelectionProvider → SearchProvider → JsonExpansionProvider
        │
        └── TraceContent (responsive router)
              ├── DesktopTraceContent
              │     └── TraceLayoutDesktop (resizable panels)
              │           ├── NavigationPanel (left, collapsible)
              │           │     ├── TracePanelNavigationHeader (search bar + view toggle)
              │           │     ├── TracePanelNavigation (view switcher)
              │           │     │     ├── TraceTree (default) — hierarchical tree
              │           │     │     ├── TraceTimeline (Gantt chart) — when view=timeline
              │           │     │     └── TraceSearchList — when search query active
              │           │     └── TraceGraphView (optional, below navigation)
              │           └── DetailPanel (right)
              │                 └── TracePanelDetail (view switcher)
              │                       ├── TraceDetailView — trace-level I/O, metadata, scores
              │                       └── ObservationDetailView — when observation selected
              │
              └── MobileTraceContent (accordion-style vertical panels)
```

### 1. Tree View (`web/src/components/trace2/components/TraceTree.tsx`)

The default navigation view. Uses **virtualized rendering** via `@tanstack/react-virtual`.

**Three-layer composition:**
1. `VirtualizedTree` — handles virtualization, scroll, expand/collapse
2. `VirtualizedTreeNodeWrapper` — renders tree structure (indentation, expand arrows, selection highlight)
3. `SpanContent` — renders the actual content of each node (name, type badge, cost, duration, heatmap colors)

**Tree building** (`web/src/components/trace2/lib/tree-building.ts`):
- Fully **iterative** (no recursion) to avoid stack overflow on 10k+ deep trees
- Algorithm: Sort by startTime → Build dependency graph (Map-based, O(N)) → Topological sort (bottom-up, leaves first) → Cost aggregation (children before parents) → Flatten to searchItems (iterative pre-order traversal)
- Complexity: O(N) time, O(N) space

**TreeNode type** (`web/src/components/trace2/lib/types.ts`):
```typescript
type TreeNode = {
  id: string;
  type: "TRACE" | ObservationType;
  name: string;
  startTime: Date;
  endTime?: Date | null;
  level?: string;
  children: TreeNode[];
  inputUsage?: number | null;
  outputUsage?: number | null;
  totalUsage?: number | null;
  calculatedTotalCost?: number | null;
  totalCost?: Decimal;        // Pre-computed: this node + all descendants
  latency?: number;
  parentObservationId?: string | null;
  startTimeSinceTrace: number; // ms from trace start
  startTimeSinceParentStart: number | null;
  depth: number;               // -1 for trace root, 0 for root observations
  childrenDepth: number;       // Max depth of subtree
};
```

### 2. Timeline View (`web/src/components/trace2/components/TraceTimeline/`)

Gantt chart visualization with virtualized rows. Key files:
- `index.tsx` — Main component, sets up virtualizer, calculates scale
- `timeline-calculations.ts` — Step size calculation, time axis math
- `timeline-flattening.ts` — Flattens tree with pre-computed timeline metrics (startOffset, itemWidth, firstTokenTimeOffset)
- `TimelineBar.tsx` — Individual Gantt bar rendering
- `TimelineRow.tsx` — Row container with indentation
- `TimelineScale.tsx` — Time axis header

**Key features:**
- **Split bars for streaming LLMs**: Shows time-to-first-token as a muted left section, completion as full-color right section
- **Heatmap coloring**: Cost and duration badges colored relative to parent totals
- **Proportional bar widths**: Duration mapped to pixel width via `SCALE_WIDTH` constant
- Virtualized via `@tanstack/react-virtual` with 500-item overscan
- Row height: 42px

### 3. Graph View (`web/src/components/trace2/components/TraceGraphView/TraceGraphView.tsx`)

Node-edge graph visualization showing agent flow through turns and tool calls. Rendered as secondary content below the navigation panel.

### 4. Detail Panel (`web/src/components/trace2/components/`)

When a node is selected:
- **TraceDetailView/** — Shows trace-level data: I/O preview, metadata, tags, cost/latency/usage badges, session/user links, environment badge
- **ObservationDetailView/** — Shows observation-level data: I/O, model parameters, token usage, cost details
- **IOPreview/** — Input/output renderer with three modes: Formatted (ChatML conversation view with markdown), JSON, JSON-Beta. Supports media attachments

**TracePreview** (`web/src/components/trace2/TracePreview.tsx`) has three tabs:
1. **Preview** — I/O display with Formatted/JSON toggle, metadata, tags
2. **Log View** — All observations concatenated (disabled >350 observations, warns >150)
3. **Scores** — Score table for the trace

### 5. Context Providers (State Management)

Six React context providers, each with single responsibility:

| Context | File | Purpose |
|---------|------|---------|
| `ViewPreferencesProvider` | `contexts/ViewPreferencesContext.tsx` | Display prefs: showDuration, showCostTokens, showScores, showComments, colorCodeMetrics, showGraph, minObservationLevel |
| `TraceDataProvider` | `contexts/TraceDataContext.tsx` | Read-only trace data + derived tree structure. Calls `buildTraceUiData()` to compute roots, nodeMap, searchItems |
| `TraceGraphDataProvider` | `contexts/TraceGraphDataContext.tsx` | Graph view data + availability check |
| `SelectionProvider` | `contexts/SelectionContext.tsx` | Selected node ID, collapsed nodes set |
| `SearchProvider` | `contexts/SearchContext.tsx` | Search query state |
| `JsonExpansionProvider` | `contexts/JsonExpansionContext.tsx` | JSON expansion state for I/O preview |

**Key insight**: Data fetching is done OUTSIDE the context providers (in `TracePage`), then passed as props. Contexts only handle derived computation and UI state.

### 6. Trace List Table

`web/src/pages/project/[projectId]/traces.tsx` renders `TracesTable` (from `web/src/components/table/use-cases/traces`).

**Table component**: Uses `@tanstack/react-table` via `DataTable`.

**Columns**: bookmarked (star toggle), timestamp, name, input, output (lazy-loaded via `TracesDynamicCell`), levelCounts (error/warning/debug badges), latency, tokens (with breakdown tooltip), totalCost (with breakdown tooltip), environment, sessionId, userId, tags, version, release, observationCount, scores (dynamic score columns from `useScoreColumns`)

**Filters** (from `web/src/features/filters/config/traces-config.ts`): environment, name, trace ID, user ID, session ID, metadata key-value, version, release, bookmarked, comment count/content, tags, level, latency, input/output/total tokens, input/output/total cost, categorical/numeric scores

**Two-query pattern**: `api.traces.all` (core data) + `api.traces.metrics` (latency/cost aggregates) joined by `joinTableCoreAndMetrics` — decouples fast core data from slower aggregated metrics.

**Peek view**: `PeekViewTraceDetail` renders a full `<Trace>` component in a slide-out panel with `context="peek"`.

Two data modes:
- **V3 (default)**: `TracesTable` — server-side paginated table
- **V4 (beta)**: `ObservationsEventsTable` — observation-centric events table

### 7. Data Fetching Pattern

- **tRPC + React Query**: All data fetched via `api.traces.byIdWithObservationsAndScores.useQuery()`
- **V4 Beta path**: `useEventsTraceData()` hook fetches from events table instead
- **Web Worker parsing**: Trace I/O parsed in background via `useParsedTrace()` hook to avoid blocking UI thread
- **Prefetch on hover**: `useHandlePrefetchObservation()` prefetches observation detail data when hovering tree nodes

---

## Dashboard Architecture

### Widget System

**Dashboard data model** (Postgres):
```
Dashboard { id, name, description, definition: Json }
  definition = [{ x, y, x_size, y_size, widgetId, id, type: "widget" }]

DashboardWidget { id, name, description, view, dimensions, metrics, filters, chartType, chartConfig }
```

Widgets are **reusable across dashboards** — deliberate design choice to prevent metric drift.

### Chart Library (`web/src/features/widgets/chart-library/`)

Built on **shadcn/charts + Recharts**:

| Chart Type | File | Use Case |
|------------|------|----------|
| `LineChartTimeSeries` | `LineChartTimeSeries.tsx` | Trends over time |
| `AreaChartTimeSeries` | `AreaChartTimeSeries.tsx` | Volume over time |
| `VerticalBarChart` | `VerticalBarChart.tsx` | Category comparisons |
| `VerticalBarChartTimeSeries` | `VerticalBarChartTimeSeries.tsx` | Time-bucketed bars |
| `HorizontalBarChart` | `HorizontalBarChart.tsx` | Ranked lists |
| `PieChart` | `PieChart.tsx` | Proportions |
| `HistogramChart` | `HistogramChart.tsx` | Distribution |
| `BigNumber` | `BigNumber.tsx` | KPI cards |
| `PivotTable` | `PivotTable.tsx` | Tabular aggregations |

**Chart router** (`Chart.tsx`): Maps `chartType` enum to the correct component.

### Grid Layout

`DashboardGrid.tsx` uses **react-grid-layout** (`Responsive` + `WidthProvider`) for drag-and-drop resizable widgets. Row height auto-calculated from container width (16:9 aspect ratio per cell).

### Query Engine

Backend query builder translates domain-specific JSON into ClickHouse SQL:
- Two-layer query pattern: inner query groups by entity ID, outer query applies aggregations
- Supports multi-level aggregations (observations → trace → user → session)
- Seven-step build pipeline: validate → FROM/JOIN → filters → inner dimensions/metrics → inner query → outer query → ORDER BY + WITH FILL

### Dashboard Page (`web/src/pages/project/[projectId]/dashboards/[dashboardId]/index.tsx`)

Key features:
- `DashboardQuerySchedulerProvider` — Concurrent query scheduler (rate-limits ClickHouse queries)
- `PopoverFilterBuilder` — Advanced filtering UI
- `TimeRangePicker` — Date range with aggregation granularity
- Langfuse-managed dashboards (pre-built, cloneable)
- Widget CRUD with `SelectWidgetDialog` and `WidgetForm`

---

## UI Component Library & Key Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| Next.js | 16.2.1 | Web framework (Pages + App Router hybrid) |
| React | 19.2.4 | UI framework |
| tRPC | ^11.13.4 | Type-safe API layer |
| @tanstack/react-query | v5 | Server state management |
| @tanstack/react-table | v8 | Data tables |
| @tanstack/react-virtual | v3 | Virtualized lists (trace tree, timeline) |
| shadcn/ui + Radix UI | 20+ packages | Base component library |
| Tailwind CSS | ^4.2.1 (v4) | CSS-first styling |
| Recharts | v3 | Chart rendering |
| react-grid-layout | - | Dashboard grid |
| react-resizable-panels | - | Trace detail left/right panels |
| use-query-params | - | URL state for view modes, filters |
| decimal.js | - | Precise cost calculations |
| Zod | 4.3.6 | Schema validation |
| lucide-react | - | Icons |
| next-auth | ^4.24.13 | Authentication |
| CodeMirror 6 | - | Code editor (prompt playground) |
| pnpm | 10.33.0 | Package manager |
| Node.js | 24 | Runtime (pinned) |

---

## Maturity & Traction

- **License**: Other (mixed, check LICENSE file)
- **Stars**: 23,933 | **Forks**: 2,412
- **Open Issues**: 603
- **Latest Push**: 2026-03-27
- **Backing**: ClickHouse (acquired Jan 2026), formerly YC W23
- **Production Users**: AINews, Handshake, Tripadvisor, Microsoft (via Arize)
- **Ecosystem**: Python SDK, JS SDK, OpenTelemetry bridge, LangChain/LlamaIndex/OpenAI integrations

## Strengths

- **Mature trace visualization**: Tree, timeline (Gantt), graph, and log views with virtualization for 10k+ observations
- **Reusable dashboard widget system**: Chart library + grid layout + ClickHouse query engine is production-hardened
- **Context provider architecture**: Clean separation of data, selection, preferences, search — easy to extract patterns
- **O(N) iterative tree building**: Handles unlimited depth without stack overflow, with bottom-up cost aggregation
- **Split-bar timeline**: Time-to-first-token visualization is unique to LLM observability
- **Web Worker I/O parsing**: Background parsing prevents UI thread blocking on large traces
- **Well-documented code**: Component files have clear JSDoc headers explaining purpose, responsibilities, hooks, and re-render triggers

## Limitations & Risks

- **1MB trace size limit**: Production traces get truncated; this is a known open issue (#4940)
- **No built-in alerting**: Teams must export to Datadog/Grafana for alerts
- **ClickHouse dependency**: The dashboard query engine is deeply coupled to ClickHouse SQL (WITH FILL, FINAL, etc.)
- **Complex self-hosting**: Requires Postgres + ClickHouse + Redis — non-trivial ops burden
- **V3/V4 dual paths**: The codebase is in transition between trace-centric (v3) and observation-centric (v4) data models, with beta toggles throughout
- **Log view disabled at 350 observations**: Large traces can't use the concatenated log view
- **Engineering-only workflows**: Product managers can't run prompt experiments without developer involvement

## Competitive Landscape

| Alternative | Differentiator | Trade-off |
|-------------|---------------|-----------|
| LangSmith | Deep LangChain/LangGraph integration, built-in alerting | Proprietary, closed-source, no self-hosting (enterprise only) |
| Arize Phoenix | Open-source, strong ML background | Less mature LLM-specific features |
| Braintrust | Custom Brainstore DB (86x faster search), CI/CD eval integration | Closed source |
| Comet Opik | 7-14x faster trace logging (benchmarked), truly OSS | Smaller ecosystem |
| Helicone | Proxy-based (zero-code instrumentation) | Less deep trace visualization |

## Community Signal

**Hacker News:**
- Consistently positive reception since Show HN in 2023
- Users praise OSS nature and API-first design ("we've been happy adopters for 15 months")
- ClickHouse acquisition (Jan 2026) generated discussion — some speculated "fire sale" given $4M seed raise, though team had Series A term sheet
- Criticism that Langfuse/Helicone "work well for traditional LLM ops, but AI agents present new challenges" — per-session evaluation vs per-prompt

**Key practitioner quote**: "With the API, you can decide what parts are core to your business logic and outsource the parts you don't want to deal with to Langfuse."

---

## Integration Analysis: OpenTraces Agent Trace Dashboard

### Fit Assessment

**Strong Fit** — Langfuse's trace visualization architecture is directly applicable to code agent traces. The tree/timeline/graph pattern maps well to agent sessions with tool calls.

### What to Extract for OpenTraces

#### 1. Tree Building Algorithm (Highest Value)
**Source**: `web/src/components/trace2/lib/tree-building.ts`

The iterative O(N) tree builder is directly portable. For agent traces:
- Map Claude Code conversation turns → observations
- Tool calls (Edit, Bash, Read, Write) → TOOL-type observations
- LLM responses → GENERATION-type observations
- Nested tool calls → parent-child relationships via `parentObservationId`

**Key adaptation**: Agent traces are deeper than typical LLM chains. The iterative (non-recursive) approach is essential.

#### 2. Timeline Visualization Pattern
**Source**: `web/src/components/trace2/components/TraceTimeline/`

The Gantt chart pattern with:
- Split bars for streaming (time-to-first-token)
- Heatmap coloring for cost/duration relative to parent
- Proportional width mapping

**Adaptation for agents**: Map file edit operations and bash commands to spans with real wall-clock duration. Tool call latency is the key metric (vs token generation for LLMs).

#### 3. Context Provider Architecture
**Pattern**: 6 focused context providers instead of a single global store

| Langfuse Context | OpenTraces Equivalent |
|------------------|----------------------|
| TraceDataProvider | SessionDataProvider (parsed JSONL session) |
| SelectionProvider | Same (selected step in trace) |
| ViewPreferencesProvider | Same (tree/timeline toggle, filters) |
| SearchProvider | Same (search within session) |
| JsonExpansionProvider | IOExpansionProvider (expand/collapse tool I/O) |
| TraceGraphDataProvider | Optional (agent decision graph) |

#### 4. Dashboard Widget System
**Source**: `web/src/features/widgets/`

The widget architecture is over-engineered for a v1 OpenTraces dashboard. However, the **chart library** (Recharts + shadcn/charts) is directly reusable:
- `BigNumber` for KPI cards (total traces, avg cost, etc.)
- `LineChartTimeSeries` for upload trends
- `HorizontalBarChart` for model usage breakdown
- `PieChart` for security tier distribution

#### 5. Observation Type System
Langfuse's typed observation system (SPAN, GENERATION, EVENT, TOOL, etc.) with `ItemBadge` component for type-colored badges is a good pattern. OpenTraces could use:
- `TOOL_CALL` (Edit, Bash, Read, Write, Glob, Grep)
- `LLM_RESPONSE` (assistant messages)
- `USER_MESSAGE` (human turns)
- `ERROR` (tool failures)

### Key Differences from Langfuse

| Aspect | Langfuse | OpenTraces |
|--------|----------|------------|
| Data source | Live SDK ingestion | Parsed JSONL from completed sessions |
| Backend | ClickHouse + Postgres | HuggingFace Datasets (Parquet) |
| Real-time | Yes (streaming ingestion) | No (batch upload) |
| Multi-tenant | Yes (projects, orgs) | Community dataset (public or per-user) |
| Dashboard queries | ClickHouse SQL | Client-side or HF Dataset Viewer API |

### Proposed Bootstrap Path

**Phase 1 — Static Trace Viewer (Gradio)**
- Port the tree-building algorithm from `tree-building.ts` to Python
- Build a Gradio-based trace detail view with tree navigation + I/O preview
- Render in HF Space alongside existing explorer

**Phase 2 — Rich Trace Visualization (React)**
- Extract the trace2 component architecture: TracePage → Trace → [Tree|Timeline] + DetailPanel
- Swap tRPC data fetching for HF Datasets API (load Parquet files client-side)
- Use shadcn/ui + Recharts for charts
- Adapt TreeNode type for agent-specific fields (files_edited, commands_run, git_signals)

**Phase 3 — Community Dashboard**
- Aggregate metrics across uploaded traces
- BigNumber widgets for community stats (total traces, unique contributors, models used)
- Time series for upload velocity
- Bar charts for tool usage patterns, error rates, security tier distribution

### Effort Estimate

- Phase 1 (Python tree builder + Gradio viewer): **Short (2-3 days)**
- Phase 2 (React trace viewer): **Medium (1-2 weeks)**
- Phase 3 (Community dashboard): **Medium (1-2 weeks)**

### Open Questions

- Should the trace viewer be a standalone React app or embedded in the Gradio Space?
- Is client-side Parquet parsing (via Apache Arrow JS) fast enough for large traces, or do we need a backend?
- Should we maintain Langfuse-compatible trace format for potential interop?

## Key Takeaways

1. **The trace2 component architecture is the blueprint**: 6 context providers, iterative tree building, virtualized tree/timeline rendering, responsive desktop/mobile layouts. This is the most mature open-source trace visualization available.

2. **The dashboard widget system is production-grade but over-scoped for v1**: Extract the chart library (Recharts + shadcn) and the grid layout pattern, skip the ClickHouse query builder.

3. **Agent traces need different metrics than LLM chains**: Langfuse optimizes for token usage and model cost. OpenTraces should optimize for tool call patterns, file edit attribution, and session outcomes — metrics Langfuse doesn't track.

## Sources

- [Langfuse GitHub Repository](https://github.com/langfuse/langfuse)
- [Langfuse Observability Docs](https://langfuse.com/docs/observability/overview)
- [Langfuse Data Model](https://langfuse.com/docs/observability/data-model)
- [New Trace View Changelog](https://langfuse.com/changelog/2025-03-19-new-trace-view)
- [How We Built Customizable Dashboards](https://langfuse.com/blog/2025-05-21-customizable-dashboards)
- [ClickHouse Acquires Langfuse](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability)
- [Langfuse Scaling with ClickHouse](https://clickhouse.com/blog/langfuse-llm-analytics)
- [AI Agent Observability with Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- [HN: Launch HN: Langfuse](https://news.ycombinator.com/item?id=42441258)
- [HN: ClickHouse acquires Langfuse](https://news.ycombinator.com/item?id=46656552)
- [Langfuse vs LangSmith (HuggingFace Blog)](https://huggingface.co/blog/daya-shankar/langfuse-vs-langsmith-vs-langchain-comparison)
- [Top Langfuse Alternatives (Confident AI)](https://www.confident-ai.com/knowledge-base/top-langfuse-alternatives-and-competitors-compared)
- [Trace Redesign PR #5718](https://github.com/langfuse/langfuse/pull/5718)
