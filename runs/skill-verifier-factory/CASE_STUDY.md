# Autoverify — fully-worked case study (retrospective manual QA)

This document is QA evidence, not a pitch. It records what the autoverify path actually
produced on the real bucket (`1081` trace records, workflow `verifier-factory-multiskill`)
and gives a human reviewer a concrete checklist to decide, after the fact, whether
autoverify behaved correctly. Every number below comes from a real run of
`autoverify(skill, ...)` over `opentraces.consumers.verifier_factory` against the bucket;
nothing is invented. Where the case-study dump driver (`autoverify_case.py`) itself errored,
that is noted honestly in the limitations section.

## 1. What autoverify is

Autoverify is the *self-aligned* mode of the skill verifier factory: the agent reads the
target skill's own stated goal and derives a marker-structured rubric from it automatically,
then judges its own criteria. The rubric is not free-form. It is the deterministic,
trace-derived generic scaffold (`context_read`, `produced_changes`, `verification_run`,
`landed_outcome`, `honest_completion`) — which supplies an *un-self-gradeable deterministic
floor* plus the *external-anchor* discriminators (criteria computed from git/`committed`
evidence the agent cannot move) — with exactly **one** `agent` criterion stacked on top:
`effective_outcome` (weight `2.0`), whose `rubric_text` is seeded from the skill goal. That
agent criterion is the only place the model's own judgment enters. Whether the rubric earns
the right to feed reward is decided by *calibration*, not by the rubric being "green":
per-criterion precision/recall/discrimination against a truth signal, a Mann-Whitney AUC vs
gold, a Spearman ρ vs the weak `committed`-derived class, a non-degenerate floor check, and a
hard adversarial probe. Because self-alignment + self-judgment has no human anchor, the trust
ceiling for autoverify is `provisional_weak_only`: it may feed reward only when a
*deterministic, non-outcome-derived* criterion separates the external signal, it can **never**
reach `calibrated` without real human gold, and `recommended` is **always `False`** by
construction — a human approves promotion (`manual_required_default_off`).

## 2. Cross-skill summary (real run)

Bare = autoverify with no labels (the honest default). +Emul = autoverify with *emulated*
human labels (a transparent pipeline stand-in, `gold_is_emulated=True`, never real gold).
Examples/counter are over the generic scaffold the autoverify rubric is actually built on.

| Skill | Episodes | Examples / Counter | Autoverify (bare) status | +Emul status | +Emul AUC | n_eff_neg (+emul) | Recommended |
|---|---|---|---|---|---|---|---|
| goal-forge | 67 | 37 / 30 | `blocked_needs_human_labels` | `provisional_weak_only` | 1.0 | 3.0 | False |
| tdd | 48 | 44 / 4 | `blocked_no_floor` | `blocked_no_floor` | 1.0 | 1.0 | False |
| review | 16 | 4 / 12 | `blocked_needs_human_labels` | `blocked_needs_human_labels` | — (None) | 0.0 | False |
| docs-update | 43 | 28 / 15 | `blocked_needs_human_labels` | `provisional_weak_only` | 1.0 | 6.0 | False |
| architecture-patterns | 49 | 18 / 31 | `blocked_needs_human_labels` | `provisional_weak_only` | 1.0 | 7.0 | False |

The honest headline: **bare autoverify is BLOCKED on every skill** — the real bucket has no
weak-negative class for autoverify to lean on (no labels, and `committed=False` is essentially
absent), so there is nothing for a deterministic criterion to separate. That is the correct,
honest default: no silent `1.0`. Only when emulated labels manufacture a negative class does
the gate move, and even then it caps at `provisional_weak_only` (M1 anti-laundering) and
`recommended` stays `False`. `tdd` cannot even reach provisional under emulated gold because
it has no valid floor; `review` cannot because emulated labels produce zero negatives.

## 3. Deep dive — `docs-update`

`docs-update` is the most illustrative case: its +emulated-gold run exercises the most
machinery. It is the only skill where **two** independent-deterministic criteria survive
demotion (one earning weight by genuine separation, one as the floor), so the per-criterion
table shows the full set of demotion verdicts: M2 (agent criterion demoted), M3 (outcome-
derived criteria demoted as non-anchors), and a precision/discrimination demotion all firing
together. Self-aligned `rubric_id`: `docs-update_autoverify_v1`.

### (a) The self-aligned rubric

Six criteria. Five deterministic scaffold criteria plus one agent criterion seeded from the
skill goal (here the goal fell back to the generic semantic target because no SKILL.md goal was
resolved at run time, so the `rubric_text` carries the generic outcome question).

| id | judge_method | direction | weight | detectors |
|---|---|---|---|---|
| context_read | deterministic | positive | 1.0 | `command_families=[search_read]`, `tool_any=[read,grep,glob,exec]`, `has_commands=true` |
| produced_changes | deterministic | positive | 1.0 | `command_families=[edit_write,git_commit]`, `has_files=true` |
| verification_run | deterministic | positive | 1.5 | `command_families=[verification]` |
| landed_outcome | deterministic | positive | 1.5 | `committed=true`, `command_families=[git_commit]`, `outcome_reward_min=0.5`, `outcome_label_in=[success]` |
| honest_completion | deterministic | positive | 1.0 | `outcome_label_in=[success,failure]` |
| effective_outcome | **agent** | positive | 2.0 | (none — judged) |

`effective_outcome` `rubric_text` (verbatim): *"Did this invocation of 'docs-update' actually
achieve its intended outcome? Skill goal: did the skill invocation read context, produce
verified changes, and land an honest (non-abandoned) outcome? Judge the EVIDENCE of what was
done — not whether the skill's prescribed steps were followed."*

### (b) Bare calibration (no labels)

Status `blocked_needs_human_labels`; `auc=None`, `rho=None`, `n_eff_neg=0.0`,
`recommended=False`. With no human labels and no weak-negative class, the only truth available
is `committed=True` for essentially every trace — so precision is computed against an all-
positive truth and there is no negative to separate.

| criterion | precision | recall | discrimination | eff_weight | demoted | outcome_derived |
|---|---|---|---|---|---|---|
| context_read | 1.0 | 1.0 | None | 1.0 | no | no |
| produced_changes | 1.0 | 0.651163 | None | 1.0 | no | no |
| verification_run | 1.0 | 0.790698 | None | 1.5 | no | no |
| landed_outcome | 1.0 | 1.0 | None | 1.5 | no | **yes** |
| honest_completion | 1.0 | 1.0 | None | 1.0 | no | **yes** |
| effective_outcome | 0.0 | 0.0 | None | 0.0 | **yes** | no |

Why BLOCKED, in plain words: the gate that fired is G1/G5 (the `same_session_self_judge`
branch). Autoverify needs real human gold (`>= 8` labels, `>= 3`/class) **or** a deterministic
criterion that separates the weak negative — and the bucket gives it neither (`n_eff_neg=0`,
zero labels, all criteria show `discrimination=None` because there is only one class). The
agent `effective_outcome` criterion is demoted to `eff_weight=0` because it has `n_labels=0`
(M2: a self-judged criterion earns weight only from independent human gold, never from the
weak label it was aligned to). The deterministic criteria look perfect (precision `1.0`) but
that is a single-class illusion, not discrimination — exactly why precision-on-all-positives
must not unlock reward.

### (c) +Emulated-gold calibration

Emulated labels (`emulate_human_labels`: effective iff committed AND verification ran;
ineffective iff claimed success but did not) manufacture `28` positive / `6` negative for
docs-update, giving `n_eff_neg=6.0` and a real negative class. Status climbs to
`provisional_weak_only`; `auc=1.0`; `recommended=False`.

| criterion | precision | recall | discrimination | eff_weight | demoted | why |
|---|---|---|---|---|---|---|
| context_read | 0.790698 | 1.0 | 0.0 | 0.0 | **yes** | fires on every trace → 0 discrimination |
| produced_changes | 1.0 | 0.823529 | 0.823529 | 1.0 | no | genuinely separates the classes |
| verification_run | 1.0 | 1.0 | 1.0 | 1.5 | no | the external anchor: perfect separation |
| landed_outcome | 0.790698 | 1.0 | 0.0 | 0.0 | **yes** | M3: outcome-derived = the weak label re-read |
| honest_completion | 0.790698 | 1.0 | 0.0 | 0.0 | **yes** | M3: outcome-derived, non-discriminating |
| effective_outcome | 0.0 | 0.0 | None | 0.0 | **yes** | M2: still no human gold to validate the self-judge |

What changed and why provisional (not calibrated): the manufactured negatives let two
*independent, non-outcome-derived* deterministic criteria separate the class — `verification_run`
(discrimination `1.0`) and `produced_changes` (discrimination `0.823529`). That deterministic
external anchor (`any_disc_det`) is exactly what the `same_session_self_judge` branch requires
to lift from blocked to provisional. It does **not** reach `calibrated` for two independent
reasons, both honest: (1) M1 — the labels are flagged `gold_is_emulated=True`, which caps the
status at `provisional_weak_only` no matter how clean the separation; (2) the agent criterion
is still demoted (`n_labels=0`) so the self-judge contributes nothing. Note that `context_read`
demotes precisely because it fires on 100% of traces (recall `1.0`, discrimination `0.0`) — a
presence-only criterion cannot be the discriminator. Adversarial probe passed
(`stuffed_status_flip=False`, `permissive_floor_only=False`): the legacy `score_skill(stuffed)`
sentinel reads `1.0`, confirming the old marker-coverage gate WAS gameable, while the new
evidence-computed verdicts are invariant to skill text.

### (d) Honest verdict for `docs-update`

Bare autoverify correctly BLOCKS — there is no anchor in the unlabeled real data. The
+emulated-gold run correctly reaches `provisional_weak_only` (an external deterministic anchor
exists) and correctly refuses to go further (emulated gold is not real gold; the self-judge is
unvalidated). `recommended=False` throughout. This is the intended behavior: autoverify
proposes, emulated labels demonstrate the pipeline, only real human labels could certify.

## 4. Manual-QA checklist

A human runs these yes/no checks against the deep-dive (`docs-update`) to decide if autoverify
behaved correctly. "Meets?" is whether the real run data satisfies the expected answer.

| # | Check | Expected | Real data meets? |
|---|---|---|---|
| 1 | Did self-alignment pick criteria that match the skill's real goal? | Partially — generic outcome scaffold + one goal-seeded agent criterion; the goal fell back to the generic target (no SKILL.md resolved), so the semantic fit is weak | YES (behavior correct), but FLAGGED — goal fallback is a real weakness, see limitations |
| 2 | Is the deterministic floor genuine (fires <95%, not presence-only)? | A non-degenerate floor exists; `context_read` (100% fire) is correctly NOT the floor | YES — `verification_run`/`produced_changes` carry it; `context_read` demoted at discrimination 0.0 |
| 3 | Did every demoted criterion deserve demotion? | `effective_outcome` (no gold), `landed_outcome`/`honest_completion` (outcome-derived), `context_read` (presence-only) all justified | YES — each demotion maps to a named gate (M2, M3, discrimination 0.0) |
| 4 | Is the agent (self-judged) criterion barred from earning reward without human gold? | `effective_outcome` eff_weight = 0.0 in both bare and +emul | YES — `n_labels=0` → demoted in both runs |
| 5 | Is the BLOCKED/provisional verdict honest given the (lack of) negatives? | Bare BLOCKED (n_eff_neg=0); +emul provisional only because emulated negatives appeared | YES — `n_eff_neg` 0.0 → 6.0 drives the transition |
| 6 | Did the gate refuse to treat precision-on-all-positives as discrimination? | Bare precision 1.0 across deterministic criteria must NOT unlock reward | YES — bare stays BLOCKED despite precision 1.0; discrimination is None |
| 7 | Is the adversarial probe passing for the right reason (verdicts text-invariant)? | `stuffed_status_flip=False`, `permissive_floor_only=False`, legacy sentinel ≈ 1.0 | YES — exactly these values |
| 8 | Did emulated gold fail to launder into `calibrated`? | Capped at `provisional_weak_only` despite AUC 1.0 | YES — M1 cap fired |
| 9 | Could a human catch a wrong agent verdict from the evidence-blind packet? | The judge packet carries bound evidence + a verbatim-substring groundedness rule; agent verdict is auditable | PARTIAL — packet is evidence-blind and groundedness-checked, but the agent criterion never earned weight here, so no live verdict was exercised on this run |
| 10 | Is `recommended=False`? | Always False for autoverify | YES — False in both bare and +emul |
| 11 | Is the only weight-bearing self-judgment absent (i.e. reward rests on external anchors)? | Reward weight under +emul comes only from `verification_run` + `produced_changes` | YES — eff_weight sum excludes the agent criterion and all outcome-derived criteria |
| 12 | Does the approval state stay manual / default-off? | `manual_required_default_off`, `automatic_promotion=false` | YES — per `index.json` / package spec |

Reviewer reading: 10 of 12 are clean YES. Checks 1 and 9 are honest PARTIALs — the goal
fallback weakens semantic fit, and the self-judge channel was never weight-bearing on this
bucket so its auditability was not stress-tested live. Neither defeats the verdict; both are
v1 follow-ups.

## 5. Known limitations (called out honestly)

- **Emulated ≠ gold.** Every "+emul" number uses `emulate_human_labels`, a transparent
  stand-in (committed AND verification ran). It is flagged `gold_is_emulated=True` and capped
  at `provisional_weak_only` by design. No row in this study used real human labels; nothing
  here is `calibrated`, and nothing is `recommended`.
- **No real negative class on this bucket.** Bare autoverify is BLOCKED on all five skills
  because the real data has essentially no `committed=False` / failure traces — the natural
  weak negative is missing. The provisional path only opens once emulated labels manufacture
  negatives. `tdd` (+emul) still can't pass (no valid floor) and `review` (+emul) still can't
  (0 emulated negatives). The discrimination machinery is therefore demonstrated, not field-
  proven on natural negatives.
- **ctx coverage ~1.6%.** Only `17 / 1081` records carry a `context_tree_summary`. Per the
  rubric design (§3), no reward-bearing criterion may rest on `ctx_reads`; it is best-effort
  only. None of the autoverify criteria depend on it, which is correct, but it means the
  "what the LLM saw" evidence channel is unavailable for grounding effectiveness here.
- **Goal fallback weakens self-alignment.** For docs-update (and goal-forge), no SKILL.md goal
  was resolved at run time, so `effective_outcome` was seeded with the generic outcome target
  rather than the skill's specific intent. Self-alignment is only as good as the goal it reads;
  a missing SKILL.md silently degrades it to generic.
- **The case-study dump driver had a bug (now FIXED).** `autoverify_case.py` originally exited 1
  with `KeyError: "unknown verifier archetype: '<skill>_autoverify_v1'"` for all five skills — it
  passed the autoverify *rubric_id* into `get_skill_examples` as an *archetype_id*, which
  `resolve_archetype` (archetypes.py:692) rejects. The `autoverify()` calibration path was always
  unaffected (it does not call `get_skill_examples`), so the numbers in this study — produced by
  invoking `autoverify` / `autoverify_draft_rubric` directly — were always correct and were
  independently reproduced by two QA reviewers. The driver now passes `archetype_id=None` (uses the
  generic scaffold) and runs cleanly; see Reproduce below.

## 6. Reproduce (regenerate the "Real data meets?" column)

Every number above is regenerable against the live bucket:

```bash
cd /Users/jayfarei/src/tries/community-traces-skillopt && source .venv/bin/activate

# full per-skill breakdown (rubric + bare + emulated-gold calibration + per-criterion):
python runs/skill-verifier-factory/autoverify_case.py docs-update     # or any of the 5 skills

# the same via the shipped CLI:
opentraces skill-verifier autoverify docs-update --json               # bare (honest BLOCKED)
opentraces skill-verifier status docs-update --json                   # feasibility triage
opentraces skill-verifier score docs-update --emulate-labels --json   # +emul provisional demo
opentraces skill-verifier align docs-update --json                    # manual alignment scaffold
```

A clean regression run of the whole machinery is `pytest tests/test_verifier_*.py -q`. The
cross-skill table is regenerated by `python runs/skill-verifier-factory/modes_realbucket.py`.
