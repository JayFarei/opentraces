// AUTO-GENERATED demo data for TraceSlicerCompare (real bucket traces, deterministic).
// Step codes: u=user p=plan t=think r=read e=exec w=write (hub classifyStep).
export type SlicerKey = 's1' | 's2' | 's3' | 's4';
export interface DemoSlice { s: number; e: number; k: string; l: string }
export interface DemoTrace { label: string; agent: string; total: number; steps: string; slicers: Record<SlicerKey, DemoSlice[]> }
export const SLICER_DEMO: Record<string, DemoTrace> = {"T3": {"label": "Hub slicer-feature session", "agent": "Claude Code", "total": 32, "steps": "uurrrerwwwwrwrweeeereeeetuueeree", "slicers": {"s1": [{"s": 0, "e": 24, "k": "trajectory", "l": "can you [Image #1] [Image #2] put the trace decomposition as a feature rather th"}, {"s": 25, "e": 31, "k": "trajectory", "l": "align it to the style of the rest of the examples, use agent-browser to identify"}], "s2": [{"s": 0, "e": 6, "k": "explore/verify", "l": "explore"}, {"s": 7, "e": 14, "k": "change-burst", "l": "burst: globals.css, hub-features.ts, page.tsx"}, {"s": 15, "e": 24, "k": "explore/verify", "l": "explore"}, {"s": 25, "e": 31, "k": "explore/verify", "l": "explore"}], "s3": [{"s": 0, "e": 8, "k": "milestone", "l": "Add slicer to workspace feature group"}, {"s": 9, "e": 10, "k": "milestone", "l": "Update FeatureCard for slicer rendering"}, {"s": 11, "e": 14, "k": "milestone", "l": "Refactor CSS for slicer component"}, {"s": 15, "e": 24, "k": "milestone", "l": "Verify slicer integrates in feature tour"}, {"s": 25, "e": 31, "k": "milestone", "l": "Capture visual result of slicer UI"}], "s4": [{"s": 0, "e": 24, "k": "subgoal", "l": "Integrated trace decomposition into Hub tour"}, {"s": 25, "e": 31, "k": "subgoal", "l": "Captured slicer card comparison visuals"}]}}};

export const SLICER_META: { key: SlicerKey; name: string; tier: 'deterministic' | 'cheap_llm'; sig: string; desc: string }[] = [
  { key: 's1', name: 'S1 user-turn', tier: 'deterministic', sig: 'user', desc: 'new trajectory at each human ask' },
  { key: 's2', name: 'S2 change-burst', tier: 'deterministic', sig: 'write', desc: 'edit clusters cut on S1 boundaries' },
  { key: 's3', name: 'S3 milestone', tier: 'cheap_llm', sig: 'exec', desc: 'close on a verified outcome' },
  { key: 's4', name: 'S4 subgoal', tier: 'cheap_llm', sig: 'read', desc: 'S1 spine + internal agent pivots' },
];
export const ACTION_LEGEND = ['user', 'plan', 'think', 'read', 'exec', 'write'] as const;
