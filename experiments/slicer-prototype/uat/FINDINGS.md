# Trace Slicer Library — UAT (10 traces × 4 slicers + agent-experience)

Follow-up to issue #141. 10 diverse real bucket traces (claude + codex; 23 → 1,490 steps; control-leak / autonomous / subagent-heavy / error-heavy) driven through the shipped `opentraces trace partition` CLI by **10 fresh agents** who had to discover the rc=10 → `--answers` → rc=0 protocol from the CLI output alone.

## Headline

- **40/40 (trace × slicer) cells tiling-valid** (independently recomputed); **40/40 reached a final tiling**.
- **10/10 agents fully resolved all four slicers.**
- Of the 8 agents that actually hit the agent loop (2 traces needed no judgments), **8/8 found the rc=10 output self-explanatory and wrote a valid `--answers` file on the first try** (1 trace took a 2nd answers iteration).
- The agent answered a **216-request s4 loop on a 1,490-step trace** and still reached rc=0 (it wrote one decision rule and applied it across all 216).

## Side-by-side matrix (Nt = trajectories; +Kj = cheap-LLM judgments asked)

| trace | steps | S1 | S2 | S3 | S4 |
|---|--:|--:|--:|--:|--:|
| codex tiny | 23 | 1t | 1t | 1t | 1t |
| claude tiny | 28 | 2t | 4t | 9t +1j | 2t +7j |
| claude medium | 69 | 2t | 4t | 7t +4j | 3t +16j |
| codex medium | 139 | 1t | 3t | 16t +9j | 4t +33j |
| error-heavy | 153 | 8t | 12t | 23t +5j | 10t +16j |
| codex huge | 1044 | 8t | 22t | 53t +25j | 32t +124j |
| claude huge | 1490 | 47t | 73t | 189t +56j | 118t +216j |
| control-leak | 102 | 2t | 4t | 27t +4j | 13t +33j |
| autonomous | 41 | 1t | 1t | 1t | 1t |
| subagent-heavy | 382 | 13t | 15t | 115t +35j | 27t +151j |

## Per-slicer experience

- **S1 / S2 (deterministic):** "instant, satisfying one-shot calls… genuinely readable with zero protocol knowledge. No friction." S2 is the robust default; **S1 degenerates to one trajectory on autonomous/codex sessions** with no user turns.
- **S3 milestone (cheap-LLM):** moderate loop; judgment volume scales gently (≤56 on the 1,490-step trace). The most granular slicing.
- **S4 subgoal (cheap-LLM):** "protocol-clean… genuinely groundable." Shares S1's default shape but **asks by far the most judgments** (216 on the huge trace); its segmentation is realised only by answering them (71 pivots / 145 stays → 118 subgoals on the huge trace).

## Friction (42 items; dominant themes)

1. **Judgment requests lack inline step content (16 mentions).** A `JudgmentRequest` gives `window` indices + a terse prompt but not the candidate steps' text/files, so to judge *well* the agent runs a second command (`trace map --json`) and self-parses for grounding. The loop *mechanics* are clean; the *grounding* needs a side-fetch.
2. **S4 judgment volume at scale (3 mentions).** Hundreds of bare-index questions on a huge trace are impractical one-by-one; agents wrote a single decision rule and applied it programmatically.
3. Artifact placeholders (`<answer>` / `<test>` / `<apply_patch>`) are cryptic until cross-referenced (5 mentions).
4. Minor: cross-surface step-count mismatch (`trace map`/`get` model nodes vs the slicer's atomic step indices) confused grounding once.

## Top follow-up — LANDED + A/B validated

Made the S3/S4 `prompt` self-grounding by inlining a bounded preview of the relevant steps (S3: each same-artifact success in the cluster; S4: prior-outcome / candidate / next). The `prompt` is a free-text field, so this needed **no `JudgmentRequest` schema change**; tiling, idempotence, the otbox journey, and the mutation kills are all unchanged/green, and the huge-trace envelope stays bounded (164 KB / 216 requests).

A/B — 8 agents judging from the rc=10 envelope ALONE (no trace access), bare vs enriched:

| condition | "could judge from envelope alone" | mean confidence |
|---|---|---|
| bare (old) | 0/4 | 0.526 |
| enriched (new) | 3/4 | 0.744 |

Every enriched agent beat its bare counterpart; S4 (pivot) benefits most (0.42→0.82, 0.50→0.76, both flip to self-sufficient). Honest residual: S3 can still want more when the artifact is an opaque `<answer>` placeholder or successes are far apart with hidden intervening steps — a smaller v1.2 refinement. Escape hatch unchanged: `--judge deterministic` returns a valid tiling instantly, skipping the loop when a quick split is enough.
