#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { performance } from "node:perf_hooks";

const FIXED_NOW = Date.parse("2026-04-18T12:00:00Z");
process.env.NODE_ENV ??= "production";
Date.now = () => FIXED_NOW;

const runnerFile = fileURLToPath(import.meta.url);
const runnerDir = path.dirname(runnerFile);
const repoRoot = path.resolve(runnerDir, "../../..");
const viewerRoot = path.join(repoRoot, "web", "viewer");

const { createRequire } = await import("node:module");
const viewerRequire = createRequire(path.join(viewerRoot, "package.json"));
const Module = viewerRequire("module");
const ts = viewerRequire("typescript");
const React = viewerRequire("react");
const ReactDOM = viewerRequire("react-dom");
const ReactDOMClient = viewerRequire("react-dom/client");
const { QueryClient, QueryClientProvider } = viewerRequire("@tanstack/react-query");
const { JSDOM } = viewerRequire("jsdom");

const BENCHMARK_IDS = ["flattenTree", "ReviewView", "GraphView"];
const DEFAULT_ITERATIONS = 12;
const DEFAULT_WARMUP = 3;

const restoreTsHooks = installTypeScriptHooks();

let domHarness = null;

try {
  const options = parseCli();
  if (options.help) {
    printHelp();
    process.exit(0);
  }
  if (options.list) {
    process.stdout.write(`${BENCHMARK_IDS.join("\n")}\n`);
    process.exit(0);
  }

  domHarness = createDomHarness();

  const {
    flattenTree,
    traceStepNodeId,
    traceToolCallNodeId,
    traceObservationNodeId,
    traceSubagentNodeId,
  } = viewerRequire(path.join(viewerRoot, "src", "lib", "traceTree.ts"));
  const { ReviewView } = viewerRequire(path.join(viewerRoot, "src", "components", "review", "ReviewView.tsx"));
  const { GraphView } = viewerRequire(path.join(viewerRoot, "src", "components", "graph", "GraphView.tsx"));
  const { DARK } = viewerRequire(path.join(viewerRoot, "src", "tokens.ts"));

  const reviewFixture = buildReviewFixture({
    traceStepNodeId,
    traceToolCallNodeId,
    traceObservationNodeId,
    traceSubagentNodeId,
  });
  const graphFixture = buildGraphFixture();

  const runners = {
    flattenTree: () =>
      runFlatTreeBenchmark({
        flattenTree,
        traceStepNodeId,
        traceToolCallNodeId,
        reviewFixture,
        options,
      }),
    ReviewView: () =>
      runReviewViewBenchmark({
        React,
        ReactDOM,
        ReactDOMClient,
        QueryClient,
        QueryClientProvider,
        ReviewView,
        theme: DARK,
        fixture: reviewFixture,
        options,
      }),
    GraphView: () =>
      runGraphViewBenchmark({
        React,
        ReactDOM,
        ReactDOMClient,
        QueryClient,
        QueryClientProvider,
        GraphView,
        theme: DARK,
        fixture: graphFixture,
        options,
      }),
  };

  const results = [];
  for (const benchmarkId of options.benchmarks) {
    results.push(await runners[benchmarkId]());
  }

  const payload = {
    runner: "viewer-perf",
    schemaVersion: 1,
    nodeVersion: process.version,
    config: {
      iterations: options.iterations,
      warmup: options.warmup,
      gcAvailable: typeof globalThis.gc === "function",
      benchmarks: options.benchmarks,
    },
    results,
  };

  process.stdout.write(JSON.stringify(payload, null, options.pretty ? 2 : 0));
  process.stdout.write("\n");
} catch (error) {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
} finally {
  if (domHarness) domHarness.dispose();
  restoreTsHooks();
}

function parseCli() {
  const parsed = parseArgs({
    args: process.argv.slice(2),
    options: {
      benchmark: { type: "string", multiple: true, short: "b" },
      iterations: { type: "string", short: "i" },
      warmup: { type: "string", short: "w" },
      pretty: { type: "boolean" },
      list: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
    allowPositionals: false,
  });

  const rawBenchmarks = (parsed.values.benchmark ?? [])
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter(Boolean);
  const benchmarks = rawBenchmarks.length === 0
    ? BENCHMARK_IDS
    : BENCHMARK_IDS.filter((id) => rawBenchmarks.includes(id));

  const unknown = rawBenchmarks.filter((id) => !BENCHMARK_IDS.includes(id));
  if (unknown.length > 0) {
    throw new Error(`Unknown benchmark id(s): ${unknown.join(", ")}`);
  }

  const iterations = parsePositiveInt(parsed.values.iterations, DEFAULT_ITERATIONS, "iterations");
  const warmup = parsePositiveInt(parsed.values.warmup, DEFAULT_WARMUP, "warmup");

  return {
    benchmarks,
    iterations,
    warmup,
    pretty: parsed.values.pretty ?? false,
    list: parsed.values.list ?? false,
    help: parsed.values.help ?? false,
  };
}

function parsePositiveInt(rawValue, fallback, name) {
  if (rawValue == null) return fallback;
  const value = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`Expected --${name} to be a non-negative integer, received: ${rawValue}`);
  }
  return value;
}

function printHelp() {
  process.stdout.write(
    [
      "Usage: node tests/perf/viewer/runner.mjs [options]",
      "",
      "Options:",
      "  -b, --benchmark <id>   Run one or more benchmarks: flattenTree,ReviewView,GraphView",
      "  -i, --iterations <n>   Measured iterations per benchmark (default: 12)",
      "  -w, --warmup <n>       Warmup iterations per benchmark (default: 3)",
      "      --pretty           Pretty-print JSON output",
      "      --list             Print benchmark ids and exit",
      "  -h, --help             Show help",
    ].join("\n"),
  );
  process.stdout.write("\n");
}

function installTypeScriptHooks() {
  const originalResolveFilename = Module._resolveFilename;
  const originalTs = Module._extensions[".ts"];
  const originalTsx = Module._extensions[".tsx"];
  const originalCss = Module._extensions[".css"];

  const compile = (module, filename) => {
    const source = fs.readFileSync(filename, "utf8");
    const transpiled = ts.transpileModule(source, {
      fileName: filename,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
        allowSyntheticDefaultImports: true,
      },
    });
    module._compile(transpiled.outputText, filename);
  };

  Module._extensions[".ts"] = compile;
  Module._extensions[".tsx"] = compile;
  Module._extensions[".css"] = (module) => {
    module._compile("", module.filename);
  };

  Module._resolveFilename = function resolveFilename(request, parent, isMain, options) {
    try {
      return originalResolveFilename.call(this, request, parent, isMain, options);
    } catch (error) {
      if (!shouldTryTypeScriptPath(request)) throw error;
      for (const candidate of makeTypeScriptCandidates(request)) {
        try {
          return originalResolveFilename.call(this, candidate, parent, isMain, options);
        } catch {
          continue;
        }
      }
      throw error;
    }
  };

  return () => {
    Module._resolveFilename = originalResolveFilename;
    if (originalTs) Module._extensions[".ts"] = originalTs;
    else delete Module._extensions[".ts"];
    if (originalTsx) Module._extensions[".tsx"] = originalTsx;
    else delete Module._extensions[".tsx"];
    if (originalCss) Module._extensions[".css"] = originalCss;
    else delete Module._extensions[".css"];
  };
}

function shouldTryTypeScriptPath(request) {
  if (!request) return false;
  if (request.startsWith("node:")) return false;
  if (path.extname(request)) return false;
  return request.startsWith(".") || path.isAbsolute(request);
}

function makeTypeScriptCandidates(request) {
  return [
    `${request}.ts`,
    `${request}.tsx`,
    path.join(request, "index.ts"),
    path.join(request, "index.tsx"),
  ];
}

function createDomHarness() {
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://127.0.0.1/",
    pretendToBeVisual: true,
  });

  const previous = new Map();
  const window = dom.window;
  const rafQueue = new Map();
  let rafId = 0;
  let rafTime = 0;

  const installGlobal = (name, value) => {
    previous.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, {
      value,
      configurable: true,
      writable: true,
    });
  };

  const requestAnimationFrame = (callback) => {
    const id = ++rafId;
    rafQueue.set(id, callback);
    return id;
  };

  const cancelAnimationFrame = (id) => {
    rafQueue.delete(id);
  };

  const flushAnimationFrames = (limit = 8) => {
    let frames = 0;
    while (rafQueue.size > 0 && frames < limit) {
      const batch = [...rafQueue.entries()].sort((a, b) => a[0] - b[0]);
      rafQueue.clear();
      rafTime += 16.67;
      for (const [, callback] of batch) callback(rafTime);
      frames += 1;
    }
  };

  installGlobal("window", window);
  installGlobal("document", window.document);
  installGlobal("navigator", window.navigator);
  installGlobal("Node", window.Node);
  installGlobal("Element", window.Element);
  installGlobal("HTMLElement", window.HTMLElement);
  installGlobal("HTMLInputElement", window.HTMLInputElement);
  installGlobal("HTMLTextAreaElement", window.HTMLTextAreaElement);
  installGlobal("SVGElement", window.SVGElement);
  installGlobal("Text", window.Text);
  installGlobal("Event", window.Event);
  installGlobal("KeyboardEvent", window.KeyboardEvent);
  installGlobal("MouseEvent", window.MouseEvent);
  installGlobal("getComputedStyle", window.getComputedStyle.bind(window));
  installGlobal("requestAnimationFrame", requestAnimationFrame);
  installGlobal("cancelAnimationFrame", cancelAnimationFrame);
  installGlobal("performance", performance);

  window.requestAnimationFrame = requestAnimationFrame;
  window.cancelAnimationFrame = cancelAnimationFrame;
  window.navigator.sendBeacon = () => true;
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true, writable: true });
  Object.defineProperty(window, "innerHeight", { value: 900, configurable: true, writable: true });
  Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
    value() {},
    configurable: true,
  });
  Object.defineProperty(window.HTMLElement.prototype, "getBoundingClientRect", {
    value() {
      const stepIndex = Number.parseInt(this.getAttribute?.("data-step-index") ?? "", 10);
      if (this.getAttribute?.("data-testid") === "conversation-scroll") {
        return rect(0, 0, 760, 640);
      }
      if (this.getAttribute?.("data-testid") === "trace-tree-scroll") {
        return rect(0, 0, 420, 620);
      }
      if (Number.isFinite(stepIndex)) {
        const top = (stepIndex - 1) * 78;
        return rect(0, top, 680, 60);
      }
      return rect(0, 0, 240, 32);
    },
    configurable: true,
  });

  return {
    window,
    document: window.document,
    flushAnimationFrames,
    async settleUi() {
      for (let i = 0; i < 4; i += 1) {
        await Promise.resolve();
        flushAnimationFrames();
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    },
    dispose() {
      dom.window.close();
      for (const [name, descriptor] of previous.entries()) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    },
  };
}

function rect(left, top, width, height) {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON() {
      return this;
    },
  };
}

function takeMemorySnapshot() {
  const usage = process.memoryUsage();
  return {
    rss: usage.rss,
    heapTotal: usage.heapTotal,
    heapUsed: usage.heapUsed,
    external: usage.external,
    arrayBuffers: usage.arrayBuffers,
  };
}

function forceGc() {
  if (typeof globalThis.gc === "function") globalThis.gc();
}

function updatePeak(peak, snapshot) {
  for (const key of Object.keys(peak)) {
    peak[key] = Math.max(peak[key], snapshot[key]);
  }
}

function pushMemorySample(samples, key, snapshot) {
  for (const field of Object.keys(snapshot)) {
    samples[field][key].push(snapshot[field]);
  }
}

function createMemorySamples() {
  return {
    rss: { before: [], after: [], peak: [] },
    heapTotal: { before: [], after: [], peak: [] },
    heapUsed: { before: [], after: [], peak: [] },
    external: { before: [], after: [], peak: [] },
    arrayBuffers: { before: [], after: [], peak: [] },
  };
}

function summarizeStats(values) {
  if (values.length === 0) {
    return { count: 0, min: 0, max: 0, mean: 0, median: 0, p95: 0 };
  }
  const sorted = [...values].sort((a, b) => a - b);
  const sum = sorted.reduce((total, value) => total + value, 0);
  return {
    count: sorted.length,
    min: round(sorted[0]),
    max: round(sorted[sorted.length - 1]),
    mean: round(sum / sorted.length),
    median: round(percentile(sorted, 0.5)),
    p95: round(percentile(sorted, 0.95)),
  };
}

function summarizeMemory(samples) {
  const summary = {};
  for (const [field, buckets] of Object.entries(samples)) {
    const retained = buckets.after.map((value, index) => value - buckets.before[index]);
    const peakDelta = buckets.peak.map((value, index) => value - buckets.before[index]);
    summary[field] = {
      beforeMean: Math.round(mean(buckets.before)),
      afterMean: Math.round(mean(buckets.after)),
      retainedMean: Math.round(mean(retained)),
      retainedMax: Math.round(Math.max(...retained)),
      peakDeltaMax: Math.round(Math.max(...peakDelta)),
      peakMax: Math.round(Math.max(...buckets.peak)),
    };
  }
  return summary;
}

function mean(values) {
  if (values.length === 0) return 0;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function percentile(sorted, ratio) {
  if (sorted.length === 1) return sorted[0];
  const index = (sorted.length - 1) * ratio;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  const weight = index - lower;
  return sorted[lower] * (1 - weight) + sorted[upper] * weight;
}

function round(value) {
  return Number(value.toFixed(3));
}

async function runMeasuredBenchmark({ id, type, meta, options, execute }) {
  const initialSamples = [];
  const updateSamples = [];
  const memorySamples = createMemorySamples();

  for (let i = 0; i < options.warmup; i += 1) {
    await execute();
  }

  for (let i = 0; i < options.iterations; i += 1) {
    forceGc();
    const before = takeMemorySnapshot();
    const peak = { ...before };
    const run = await execute();
    updatePeak(peak, run.peak);
    forceGc();
    const after = takeMemorySnapshot();

    initialSamples.push(run.initialMs);
    updateSamples.push(run.updateMs);
    pushMemorySample(memorySamples, "before", before);
    pushMemorySample(memorySamples, "after", after);
    pushMemorySample(memorySamples, "peak", peak);
  }

  return {
    id,
    type,
    iterations: options.iterations,
    warmup: options.warmup,
    metrics: {
      initialMs: summarizeStats(initialSamples),
      updateMs: summarizeStats(updateSamples),
    },
    memory: summarizeMemory(memorySamples),
    meta,
  };
}

async function runFlatTreeBenchmark({ flattenTree, traceStepNodeId, traceToolCallNodeId, reviewFixture, options }) {
  const roots = reviewFixture.traceTrees[reviewFixture.selectedIds.initial].tree;
  const initialSelectedId = traceToolCallNodeId(48, 1);
  const updateSelectedId = traceStepNodeId(79);
  const initialFolded = new Set(["s15", "s35", "s65"]);
  const updateFolded = new Set(["s15", "s35", "s65", "s75", "s95"]);
  const initialConfig = {
    selectedId: initialSelectedId,
    folded: initialFolded,
    filterMode: "everything",
    search: "",
    options: {
      detailStepId: "s48",
      promoteActiveSiblings: false,
    },
  };
  const updateConfig = {
    selectedId: updateSelectedId,
    folded: updateFolded,
    filterMode: "user-only",
    search: "user prompt 7",
    options: {
      detailStepId: null,
      promoteActiveSiblings: false,
    },
  };

  const initialRows = flattenTree(
    roots,
    initialConfig.selectedId,
    initialConfig.folded,
    initialConfig.filterMode,
    initialConfig.search,
    initialConfig.options,
  );
  const updateRows = flattenTree(
    roots,
    updateConfig.selectedId,
    updateConfig.folded,
    updateConfig.filterMode,
    updateConfig.search,
    updateConfig.options,
  );

  return runMeasuredBenchmark({
    id: "flattenTree",
    type: "function",
    options,
    meta: {
      roots: roots.length,
      treeNodes: reviewFixture.treeNodeCounts[reviewFixture.selectedIds.initial],
      initialRows: initialRows.length,
      updateRows: updateRows.length,
    },
    execute: async () => {
      const peak = takeMemorySnapshot();

      let start = performance.now();
      flattenTree(
        roots,
        initialConfig.selectedId,
        initialConfig.folded,
        initialConfig.filterMode,
        initialConfig.search,
        initialConfig.options,
      );
      const initialMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      start = performance.now();
      flattenTree(
        roots,
        updateConfig.selectedId,
        updateConfig.folded,
        updateConfig.filterMode,
        updateConfig.search,
        updateConfig.options,
      );
      const updateMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      return { initialMs, updateMs, peak };
    },
  });
}

async function runReviewViewBenchmark({
  React,
  ReactDOM,
  ReactDOMClient,
  QueryClient,
  QueryClientProvider,
  ReviewView,
  theme,
  fixture,
  options,
}) {
  return runMeasuredBenchmark({
    id: "ReviewView",
    type: "component",
    options,
    meta: {
      traceCount: fixture.traces.length,
      selectedTraceSteps: fixture.traceStepCounts[fixture.selectedIds.initial],
      selectedTraceTreeNodes: fixture.treeNodeCounts[fixture.selectedIds.initial],
      updatedTraceSteps: fixture.traceStepCounts[fixture.selectedIds.update],
      updatedTraceTreeNodes: fixture.treeNodeCounts[fixture.selectedIds.update],
    },
    execute: async () => {
      const queryClient = createStableQueryClient(QueryClient);
      seedReviewQueryData(queryClient, fixture);

      const container = domHarness.document.createElement("div");
      domHarness.document.body.appendChild(container);
      const root = ReactDOMClient.createRoot(container);
      const peak = takeMemorySnapshot();

      const initialElement = React.createElement(
        QueryClientProvider,
        { client: queryClient },
        React.createElement(ReviewView, {
          t: theme,
          wide: true,
          selectedId: fixture.selectedIds.initial,
          setSelectedId() {},
          openPush() {},
          openInfo() {},
        }),
      );

      let start = performance.now();
      ReactDOM.flushSync(() => {
        root.render(initialElement);
      });
      await domHarness.settleUi();
      const initialMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      const updateElement = React.createElement(
        QueryClientProvider,
        { client: queryClient },
        React.createElement(ReviewView, {
          t: theme,
          wide: true,
          selectedId: fixture.selectedIds.update,
          setSelectedId() {},
          openPush() {},
          openInfo() {},
        }),
      );

      start = performance.now();
      ReactDOM.flushSync(() => {
        root.render(updateElement);
      });
      await domHarness.settleUi();
      const updateMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      root.unmount();
      container.remove();
      queryClient.clear();
      return { initialMs, updateMs, peak };
    },
  });
}

async function runGraphViewBenchmark({
  React,
  ReactDOM,
  ReactDOMClient,
  QueryClient,
  QueryClientProvider,
  GraphView,
  theme,
  fixture,
  options,
}) {
  return runMeasuredBenchmark({
    id: "GraphView",
    type: "component",
    options,
    meta: {
      commits: fixture.commits.length,
      pages: fixture.graphPages.length,
      tracesPerCommit: fixture.commits[0]?.traces.length ?? 0,
      blameSessionsPerCommit: fixture.blameBySha[fixture.commits[0].sha]?.sessions.length ?? 0,
      initialCommit: fixture.commits[0]?.sha ?? null,
      updatedCommit: fixture.commits[1]?.sha ?? null,
    },
    execute: async () => {
      const queryClient = createStableQueryClient(QueryClient);
      seedGraphQueryData(queryClient, fixture);

      const container = domHarness.document.createElement("div");
      domHarness.document.body.appendChild(container);
      const root = ReactDOMClient.createRoot(container);
      const peak = takeMemorySnapshot();

      const element = React.createElement(
        QueryClientProvider,
        { client: queryClient },
        React.createElement(GraphView, {
          t: theme,
          wide: true,
          mode: "dark",
          gotoTrace() {},
        }),
      );

      let start = performance.now();
      ReactDOM.flushSync(() => {
        root.render(element);
      });
      await domHarness.settleUi();
      const initialMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      start = performance.now();
      const event = new domHarness.window.KeyboardEvent("keydown", {
        key: "ArrowDown",
        bubbles: true,
        cancelable: true,
      });
      domHarness.window.dispatchEvent(event);
      await domHarness.settleUi();
      const updateMs = performance.now() - start;
      updatePeak(peak, takeMemorySnapshot());

      root.unmount();
      container.remove();
      queryClient.clear();
      return { initialMs, updateMs, peak };
    },
  });
}

function createStableQueryClient(QueryClient) {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
        gcTime: Infinity,
        refetchOnMount: false,
        refetchOnReconnect: false,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function seedReviewQueryData(queryClient, fixture) {
  queryClient.setQueryData(["traces"], fixture.traces);
  queryClient.setQueryData(["context"], fixture.context);
  for (const [traceId, detail] of Object.entries(fixture.traceDetails)) {
    queryClient.setQueryData(["trace", traceId], detail);
  }
  for (const [traceId, tree] of Object.entries(fixture.traceTrees)) {
    queryClient.setQueryData(["trace-tree", traceId], tree);
  }
  for (const [traceId, inverseBlame] of Object.entries(fixture.inverseBlameByTraceId)) {
    queryClient.setQueryData(["inverse-blame", traceId], inverseBlame);
  }
}

function seedGraphQueryData(queryClient, fixture) {
  queryClient.setQueryData(["graph"], {
    pages: fixture.graphPages.map((commits) => ({ commits })),
    pageParams: fixture.graphPages.map((_, index) => index + 1),
  });
  for (const [sha, blame] of Object.entries(fixture.blameBySha)) {
    queryClient.setQueryData(["blame", sha], blame);
  }
}

function buildReviewFixture({
  traceStepNodeId,
  traceToolCallNodeId,
  traceObservationNodeId,
  traceSubagentNodeId,
}) {
  const traces = [];
  const traceDetails = {};
  const traceTrees = {};
  const inverseBlameByTraceId = {};
  const traceStepCounts = {};
  const treeNodeCounts = {};

  for (let index = 1; index <= 48; index += 1) {
    const traceId = `trace-${String(index).padStart(3, "0")}`;
    const stage = index <= 28 ? "inbox" : index <= 38 ? "staged" : "pushed";
    traces.push({
      trace_id: traceId,
      task: `Viewer perf task ${index}`,
      agent: "claude-code",
      model: "anthropic/claude-opus-4-6",
      steps: 96 + (index % 3) * 12,
      tool_calls: 84 + index,
      timestamp: `2026-04-${String((index % 9) + 10).padStart(2, "0")}T10:00:00Z`,
      status: "parsed",
      _stage: stage,
      security_flags: index % 5 === 0 ? 1 : 0,
      generation_index: (index % 3) + 1,
      project: "viewer-perf",
    });
  }

  const selectedIds = {
    initial: "trace-006",
    update: "trace-018",
  };

  for (const [offset, traceId] of Object.entries(selectedIds)) {
    const numeric = Number.parseInt(traceId.slice(-3), 10);
    const stepCount = offset === "initial" ? 112 : 128;
    const steps = [];
    for (let stepIndex = 1; stepIndex <= stepCount; stepIndex += 1) {
      const isUser = stepIndex % 2 === 1;
      const toolCalls = isUser
        ? []
        : [
            {
              tool_name: `search_repo_${(stepIndex % 4) + 1}`,
              input: {
                query: `symbol_${numeric}_${stepIndex}`,
                path: `src/module_${(stepIndex % 7) + 1}.ts`,
                limit: 15,
              },
            },
            {
              tool_name: `edit_file_${(stepIndex % 3) + 1}`,
              input: {
                file: `src/component_${(stepIndex % 5) + 1}.tsx`,
                line: stepIndex * 2,
                preview: `patch ${stepIndex}`,
              },
            },
          ];
      const observations = isUser
        ? []
        : [
            {
              tool_name: toolCalls[0].tool_name,
              content: `observation ${numeric}-${stepIndex}-1`,
            },
            {
              tool_name: toolCalls[1].tool_name,
              output_summary: `observation ${numeric}-${stepIndex}-2`,
              error: stepIndex % 8 === 0 ? "non-fatal" : null,
            },
          ];
      steps.push({
        step_index: stepIndex,
        role: isUser ? "user" : "agent",
        content: isUser
          ? `User prompt ${stepIndex} for ${traceId}. Need deterministic benchmark coverage across the review surface.`
          : `Agent response ${stepIndex} for ${traceId}. Includes file edits, summaries, and next-step reasoning.`,
        reasoning_content: isUser ? null : `Reasoning ${stepIndex} for ${traceId}. Compare tree focus, preview cost, and selection updates.`,
        timestamp: `2026-04-${offset === "initial" ? "16" : "17"}T${String((stepIndex % 12) + 8).padStart(2, "0")}:00:00Z`,
        tool_calls: toolCalls,
        observations,
        subagent_trajectory_ref: !isUser && stepIndex % 10 === 0 ? `subagent-${traceId}-${stepIndex}` : null,
      });
    }

    const tree = buildTraceTree({
      steps,
      traceStepNodeId,
      traceToolCallNodeId,
      traceObservationNodeId,
      traceSubagentNodeId,
    });

    traceDetails[traceId] = {
      trace_id: traceId,
      generation_index: (numeric % 3) + 1,
      task: { description: `Perf detail for ${traceId}` },
      agent: { name: "claude-code", model: "anthropic/claude-opus-4-6" },
      steps,
      timestamp_start: "2026-04-16T10:00:00Z",
      metrics: {
        total_steps: stepCount,
        total_input_tokens: 54_000 + numeric,
        total_output_tokens: 22_000 + numeric,
        total_cache_read_tokens: 780_000 + numeric * 10,
        estimated_cost_usd: 7.4 + numeric / 1000,
      },
      security: {
        scanned: true,
        flags_reviewed: 1,
        redactions_applied: numeric % 4,
        classifier_version: "0.3.0",
      },
      _security_flags: numeric % 4 === 0 ? [{ type: "secret", reason: "mock", severity: "medium" }] : [],
      _stage: "inbox",
    };
    traceTrees[traceId] = { tree };
    inverseBlameByTraceId[traceId] = buildInverseBlame(traceId, numeric);
    traceStepCounts[traceId] = stepCount;
    treeNodeCounts[traceId] = countTreeNodes(tree);
  }

  return {
    context: {
      project_name: "viewer-perf",
      remote: "owner/viewer-perf-dataset",
      review_policy: "review",
      push_policy: "manual",
      authenticated: true,
      username: "perf-bot",
    },
    traces,
    traceDetails,
    traceTrees,
    inverseBlameByTraceId,
    traceStepCounts,
    treeNodeCounts,
    selectedIds,
  };
}

function buildTraceTree({
  steps,
  traceStepNodeId,
  traceToolCallNodeId,
  traceObservationNodeId,
  traceSubagentNodeId,
}) {
  const roots = [];
  let currentParent = null;

  for (const step of steps) {
    const stepId = traceStepNodeId(step.step_index);
    const node = {
      id: stepId,
      parent_id: currentParent?.id ?? null,
      kind: "step",
      step_index: step.step_index,
      timestamp: step.timestamp ?? null,
      preview: `${step.role}: ${step.content?.slice(0, 96) ?? ""}`,
      label: step.role === "user" ? "Prompt" : "Reply",
      on_active_path: false,
      entity_ref: null,
      role: step.role,
      children: [],
    };

    if ((step.step_index - 1) % 6 === 0) {
      roots.push(node);
      currentParent = node;
    } else if (currentParent) {
      currentParent.children.push(node);
      currentParent = node;
    } else {
      roots.push(node);
      currentParent = node;
    }

    (step.tool_calls ?? []).forEach((toolCall, index) => {
      node.children.push({
        id: traceToolCallNodeId(step.step_index, index),
        parent_id: stepId,
        kind: "tool_call",
        step_index: step.step_index,
        timestamp: step.timestamp ?? null,
        preview: `${toolCall.tool_name} ${toolCall.input.query ?? toolCall.input.file ?? ""}`.trim(),
        label: toolCall.tool_name,
        on_active_path: false,
        entity_ref: null,
        children: [],
      });
    });

    (step.observations ?? []).forEach((observation, index) => {
      node.children.push({
        id: traceObservationNodeId(step.step_index, index),
        parent_id: stepId,
        kind: "observation",
        step_index: step.step_index,
        timestamp: step.timestamp ?? null,
        preview: observation.content ?? observation.output_summary ?? "",
        label: observation.tool_name ?? "result",
        on_active_path: false,
        entity_ref: null,
        children: [],
      });
    });

    if (step.subagent_trajectory_ref) {
      node.children.push({
        id: traceSubagentNodeId(step.step_index),
        parent_id: stepId,
        kind: "subagent_ref",
        step_index: step.step_index,
        timestamp: step.timestamp ?? null,
        preview: `subagent ${step.subagent_trajectory_ref.slice(0, 12)}`,
        label: "subagent",
        on_active_path: false,
        entity_ref: null,
        children: [],
      });
    }
  }

  return roots;
}

function buildInverseBlame(traceId, numeric) {
  return {
    trace: {
      id: traceId.slice(0, 8),
      trace_id: traceId,
      name: `Trace ${traceId}`,
      lines: 420 + numeric,
      model: "anthropic/claude-opus-4-6",
    },
    commits: Array.from({ length: 8 }, (_, index) => ({
      id: `c${numeric}${index}`,
      sha: `sha-${traceId}-${index}`,
      msg: `Commit ${index} for ${traceId}`,
      linesInCommit: 12 + index * 3,
      totalCommitLines: 80 + index * 5,
      pct: `${15 + index * 4}%`,
      source: index < 5 ? "attribution" : "git_links",
      tier: index % 3 === 0 ? "tool_emitted" : index % 3 === 1 ? "tool_emitted_with_divergence" : null,
    })),
    files: Array.from({ length: 10 }, (_, index) => ({
      path: `src/review/file_${numeric}_${index}.tsx`,
      lines: 24 + index * 5,
    })),
  };
}

function buildGraphFixture() {
  const commits = Array.from({ length: 26 }, (_, index) => {
    const commitIndex = index + 1;
    return {
      id: `c${String(commitIndex).padStart(8, "0")}`,
      sha: `sha-${String(commitIndex).padStart(4, "0")}`,
      msg: `Viewer graph benchmark commit ${commitIndex}`,
      timestamp: `2026-04-${String((commitIndex % 9) + 10).padStart(2, "0")}T12:00:00Z`,
      pct: (commitIndex * 11) % 101,
      coverage_role: commitIndex % 3 === 0 ? "green" : commitIndex % 3 === 1 ? "yellow" : "red",
      traces: Array.from({ length: 3 }, (_, traceIndex) => ({
        id: `trace-${commitIndex}-${traceIndex + 1}`,
        trace_id: `graph-trace-${commitIndex}-${traceIndex + 1}`,
        canonical: traceIndex !== 2,
        lines: 80 + commitIndex * 3 + traceIndex * 7,
        short_name: `viewer-hotspot-${traceIndex + 1}`,
        changes: traceIndex === 0
          ? "+ Added flattenTree, treeRows · ~ Modified ReviewView, TracePreview"
          : traceIndex === 1
            ? "~ Modified GraphView, ForwardBlame · - Deleted staleBench"
            : "↷ Renamed oldPanel, graphState",
      })),
    };
  });

  const blameBySha = Object.fromEntries(
    commits.map((commit, index) => [
      commit.sha,
      {
        sha: commit.sha,
        id: commit.id,
        msg: index % 2 === 0 ? "" : commit.msg,
        pct: commit.pct,
        coverage: `${commit.pct}%`,
        attributed: `${60 + index * 2}/${120 + index * 3} lines`,
        fileCount: 6,
        sessions: commit.traces.map((trace, traceIndex) => ({
          id: trace.id,
          trace_id: trace.trace_id,
          name: trace.short_name ?? trace.id,
          lines: trace.lines,
          pct: `${15 + traceIndex * 20}%`,
          model: "anthropic/claude-opus-4-6",
          entities: trace.changes ?? "",
          canonical: trace.canonical,
        })),
        files: Array.from({ length: 6 }, (_, fileIndex) => ({
          path: `web/viewer/src/graph/file_${index + 1}_${fileIndex + 1}.tsx`,
          total: 90 + fileIndex * 17,
          attr: 12 + fileIndex * 4,
        })),
        hookLinked: index % 4 === 0
          ? [
              {
                id: `hook-${index + 1}`,
                trace_id: `hook-trace-${index + 1}`,
                name: `hooked-${index + 1}`,
                model: "claude-opus-4-6",
              },
            ]
          : undefined,
      },
    ]),
  );

  return {
    commits,
    graphPages: [commits.slice(0, 10), commits.slice(10, 20), commits.slice(20)],
    blameBySha,
  };
}

function countTreeNodes(roots) {
  let count = 0;
  const walk = (node) => {
    count += 1;
    node.children.forEach(walk);
  };
  roots.forEach(walk);
  return count;
}
