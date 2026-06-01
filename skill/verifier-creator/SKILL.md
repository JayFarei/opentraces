---
name: opentraces-skill-verifier
description: >
  Author a trace-grounded, CALIBRATED verifier for one skill, two ways: an interactive
  ALIGNMENT SESSION (manual) or AUTOVERIFY (the agent self-aligns to the skill's goal). Use
  this skill when asked to build, align, calibrate, or autoverify a verifier/rubric for a
  skill. The agent proposes a rubric; the factory scores it against evidence + labels; a human
  approves promotion.
---

# OpenTraces Skill Verifier

A skill verifier is a **calibrated rubric**, not a checklist. The reframe that governs
everything here:

> A trace is **evidence** (what the agent did + the git lineage of what persisted), never the
> verdict. The verdict — "was this skill used *effectively*?" — lives in a **rubric** you author
> in conjunction with the skill's own goal. **Calibration is trust**: a rubric earns the right
> to feed reward only when its verdicts demonstrably separate effective from ineffective use
> against human gold and the tamper-resistant git signal. Until then it is BLOCKED, never a
> silent pass.

## Trust boundary (read first)

- **The agent PROPOSES** a rubric (criteria + evidence bindings + judging questions).
- **The factory SCORES** mechanically. A rubric can declare WHAT to judge; it can never set
  `reward`/`gate`/`split`/`value` (the validator rejects those keys). Verdicts are computed
  from EVIDENCE, never from the skill text being optimized — so marker-stuffing has no channel.
- **A human APPROVES** promotion (always `manual_required_default_off`).

## Two modes

| | AUTOVERIFY (self-align) | MANUAL (alignment session) |
|---|---|---|
| Who sets the desired outcome | the agent, from the skill's stated goal | you + the agent, together |
| Labels | none (or weak git signal) | you label a few real traces |
| Trust ceiling | `provisional_weak_only` (reward usable, flagged) | `calibrated` |
| Auto-recommended | **never** | never (human approves) |

**AUTOVERIFY** is the fast path: the agent reads the skill's goal, self-aligns a
marker-structured rubric, judges it, and calibrates. Because self-alignment + self-judgment has
no human anchor, its ceiling is `provisional_weak_only` — it may feed reward **only** when a
*deterministic* criterion separates the external git/`committed` signal, and it can **never**
reach `calibrated` (self-judged criteria earn no trust). Use it to bootstrap and to triage.

**MANUAL** lifts the ceiling: in one sitting you co-establish the desired outcome, edit the
draft criteria, and label a handful of example/counterexample traces. Those labels are the gold
that unlocks `calibrated`. The alignment session is where the labeling worklist *happens* — not
a separate chore.

## Criterion vocabulary

Each criterion has a `judge_method`:

- `deterministic` — a data-only `DetectorSpec` read off evidence (cheap, tamper-resistant). The
  un-self-gradeable **floor**: every rubric needs ≥1 deterministic criterion carrying ≥20% of
  total weight, firing on <95% of traces and not presence-only.
- `agent` — the in-loop agent (you, on the user's subscription) judges an **evidence-blind**
  packet locally and posts a verdict. The default for semantic criteria ("are the findings
  substantive?"). Earns weight only after it is judged AND calibration shows it discriminates.
- `human` — gold labels; the calibration anchor and the only writer to the gold ledger.

A `direction: negative` criterion's *presence is a failure* (e.g. "claimed success but nothing
committed") — this is how you manufacture a discriminating negative class from data.

## The loop (both modes)

0. **ORIENT** — `read_skill_definition(skill)`: read the TARGET skill's goal. In MANUAL mode,
   ask the user: *"what does an effective `<skill>` invocation achieve — in outcome terms, not
   the steps it follows?"*
1. **`list_candidates(skill)`** — evidence + which markers are deficient + calibration
   feasibility (`n_weak_neg`, `n_human_labels`). It says plainly when negatives are too few.
2. **`get_skill_examples(skill, episodes=...)`** — real example vs counterexample trace refs.
   Pull the windows and READ what the traces did:
   `opentraces trace slice <id> --template bursts --json`, `opentraces trace get <id> --json`,
   `opentraces trail track <id> --json` (survival, best-effort).
3. **Author the rubric:**
   - AUTOVERIFY: `autoverify(skill, episodes=..., records=...)` self-aligns + calibrates in one
     call. Read `result.calibration.status`.
   - MANUAL: `align_session(skill, ...)` scaffolds the desired-outcome prompt, an editable draft
     rubric, and the traces to label. Edit criteria, then `author_rubric(spec)` (validates;
     rejects reward/gate keys, ungrounded semantic criteria, missing floor, permissive floor).
4. **Judge the semantic criteria (agent):** for each `agent` criterion × trace,
   `build_judge_packet(...)` → reason over the bound evidence (NO skill text) → `post_verdict(...,
   evidence_quote=<verbatim span>)`. Judge the OUTCOME, not step-conformance. Abstain if the
   evidence is insufficient.
5. **Label (manual):** `record_human_label(package_dir, ..., label=0|1, human_confirm=True)` —
   the only writer to the gold ledger, requires an explicit human keystroke. Label the
   example/counterexample traces from step 2 together.
6. **`calibrate_rubric(rubric, episodes=...)`** — per-criterion precision/recall/discrimination,
   AUC vs gold, Spearman vs the weak class, the adversarial gate. READ THE STATUS:
   - `calibrated` — verdicts separate good from bad vs gold; may feed reward.
   - `provisional_weak_only` — discriminates the weak class but no gold; reward usable, flagged,
     `recommended=False`.
   - `blocked_*` — STOP. The blocker names the remedy (gather N more negatives, redesign a
     non-discriminating criterion, fix an inverted one). Never report a passing score on a
     blocked rubric.
7. **`score_rubric` / present for approval** — only on `calibrated`/`provisional`. The headline
   shows AUC + per-criterion discrimination + adversarial PASS + status, NOT a bare `Dsel 0→1`.
   Present for HUMAN APPROVAL. Do not promote.

## MUST NOT

- Set `reward`/`gate`/`split`/`value`/`calibration`/`recommended` in a rubric (rejected).
- Judge the optimized skill text (judge the evidence); judge step-conformance instead of outcome.
- Write the gold ledger from the agent path; mark recommended or promote; author a broad/
  degenerate rubric to pass the gate (hard-gated).
- Present a green headline for a blocked or merely provisional rubric. Autoverify is never
  `calibrated` and never auto-recommended without human gold.
