# SEO + AEO monitoring & tweak loop for opentraces.ai

## 1. Honest framing

The single most important distinction in this loop is between a **working signal** and an **external acceptance gate**, and they are not measured, trusted, or acted on the same way. The **working signal** is the deterministic, first-party, near-real-time truth: does Google index our priority pages, is our JSON-LD valid, do our canonicals resolve to themselves, are AI crawlers actually fetching `/docs` and `/schema`, what does Google Search Console report for clicks/impressions/position. This layer is queryable through stable APIs, behaves like the append-only event logs opentraces already thinks in, and is safe to act on automatically. The **external acceptance gate** is the probabilistic, third-party, slow, noisy truth: does ChatGPT/Perplexity/Gemini/Claude cite us, and do they describe us correctly. This is the outcome we actually care about, but it is a *distribution*, not a point estimate. Vendor studies (directional, not measured constants — see the caveat below) report month-over-month citation churn around 40-60% for the same query, the same prompt returns different citations run-to-run, and a single sample is "noise with a UI." The loop must treat the working signal as something it can fix and the acceptance gate as something it can only *sample and trend* — never let an acceptance-gate dip trigger a site change or a revert by itself.

> **Caveat on the vendor statistics this doc cites.** Several load-bearing figures — "~81% of listicle citations are third-party," "citation churn 40-60% month-over-month," "~11% Perplexity↔ChatGPT citation overlap," and the "45-point freshness premium" attributed to Perplexity — come from single vendor or blog studies (Similarweb, BuzzStream, AthenaHQ, insightscout, and assorted AEO-platform marketing) that are vendor-marketing-adjacent and unreplicated. Treat them as **directional priors about which way the levers point**, not measured constants, and explicitly do **not** anchor a hypothesis target or a keep/revert threshold to any of them. The same skepticism the doc applies to vendor share-of-voice numbers applies here.

**Verdict on the 2-3 day cadence: split MONITOR from CHANGE. Tweaking every 2-3 days is wrong and would actively destroy the loop.** Every authoritative source agrees a 2-3 day MONITOR rhythm is correct and a 2-3 day CHANGE rhythm is harmful. Google states quality/content changes take days-to-weeks to recrawl and reprocess; GSC data itself lags 24-72h and is noisy at low traffic; AI citation shifts are reported by practitioners to lag re-crawl + re-index + engine cache by roughly **2-4 weeks** (a range of observations, not a measured constant for this site; Perplexity is the engine that reportedly reacts fastest, so the server-log crawler signal is the only roughly-2-4-week-early read). The site has **~5 priority pages atop ~35 heterogeneous one-off docs pages** (plus the blog post, `/explorer`, and per-version `/schema` routes) and low traffic, so it is *structurally* below the floor for causal SEO split-testing (SearchPilot's practical floor is hundreds of *same-template* pages and ~30k organic sessions/month to the tested group; our docs are heterogeneous one-offs, not a same-template cohort). There is no clean A/B here, only monitored single interventions with attribution caveats. Therefore: **MONITOR every 2-3 days (pure read, record-and-do-nothing by default). CHANGE at most one bounded edit per page per 2-4 week measurement window, then FREEZE that change for one full recrawl cycle (1-2 weeks minimum, 3-4 weeks before any causal verdict) before judging it or touching that page again.** Shipping a new tweak to the same page every 2-3 days permanently collapses attribution by construction.

**Honest throughput ceiling.** With at most one change in flight per page, a ~28-day confirm-the-opportunity gate, and a 21-28 day post-change verdict window, the realistic floor is roughly **one judged change per page every ~2 months.** Across ~5 priority pages and ~5 candidate levers, the honest expected output of the experiment loop is **a handful of judged interventions per year — single digits, not dozens.** State this outright so no reader believes the loop ships meaningfully often. The agent's compounding value is discipline (always pull, always annotate the change date, always wait out the window), not speed.

**What is "converged" and what is not.** The on-page *technical* layer is already converged (per-page titles/descriptions/canonicals, JSON-LD, sitemap, llms.txt, Link headers, agent-skills, robots Content-Signal — see `DISCOVERABILITY-LOOP.md`). This loop is a measurement-and-feedback loop for that layer, **not** more tags. But "converged" applies only to the technical scaffolding. The findings' own highest-leverage citation levers are **net-new on-page content structures the site does not yet have**: an answer-first 40-150 word entity-attributed block per priority page, one comparison table per page, an FAQ/`FAQPage` block, inline stats/citations, and an entity layer (`Person`/founder JSON-LD, `sameAs` to the HF org, a Wikidata QID). Those are a **substantial one-time content build** (the findings treat them as the *actual* citation levers), not "more tags," and they are sequenced as Cycle-1-3 build-out in §7, separate from the steady-state measurement loop. Do not let "converged, just measure now" hide that build.

## 2. What to measure

The dominant skeptical truths that shape this table: (a) **llms.txt is dead as a citation lever** — Google's Mueller and Illyes confirm Google does not use it, ~97% of llms.txt files got zero fetches, a 300k-domain SE Ranking study found *no* correlation with citation frequency. Keep it for the one job it does (a clean structured entry point for coding agents / Claude Code / Cursor / MCP — exactly opentraces' audience), measure it via server logs, and **forbid the loop from spending change budget "optimizing" it.** Note the repo's `public/llms.txt` is 225KB / ~5,200 lines — the loop must not optimize this bloat, only watch that it is reachable. (b) **Google's own AI Optimization guide (May/Jun 2026) says schema and llms.txt are NOT required for AI Overviews/AI Mode** ("still SEO," RAG over the normal index with query fan-out) — so JSON-LD is hygiene + rich-result eligibility + non-Google-agent extraction, not the primary Google-AI lever. (c) **Client-side JS analytics cannot see bots** (bots don't run JS); only server/edge logs — or server-side/edge-rendered analytics — capture AI-crawler fetches, and those fetches are a roughly 2-4 week *leading* indicator of citations. (Confirm whether the deployed self-hosted analytics is client-JS or server-side before relying on it for crawler counts; the crawler signal is routed to the Vercel/edge **log drain** precisely so it does not depend on that answer.) (d) **The biggest GEO lever for a dev tool is reported to be off-site** (Reddit/HN/GitHub/comparison threads — vendor data puts third-party domains at the large majority of listicle citations), which the loop must surface as a community action, not a site edit — see §4a for how that off-site action is actually owned and tracked.

### SEO signals (deterministic — trustworthy, may act automatically)

| Priority | Metric | Source / tool | How the agent reads it | Cadence |
|---|---|---|---|---|
| P0 | Index verdict per priority page (on-Google vs not-indexed; coverageState) | GSC URL Inspection API | Free OAuth2/service-account REST; webmasters.readonly; 2,000/day, 600/min cap → fits ~5 priority pages easily | 2-3 days + per deploy |
| P0 | Canonical drift (Google-selected canonical == declared canonical), last-crawl freshness, referring-sitemap present, robots-allowed | GSC URL Inspection API | Same call as above; parse `inspectionResult` fields | 2-3 days + per deploy |
| P0 | JSON-LD validity per page (parses, required props, rich-result eligible, **schema facts match visible text**) | Local JSON-LD parse of prerendered HTML (schema-dts/ajv) in CI; SchemaCheck API for gate; GSC URL Inspection for post-index rich-result status (Rich Results Test has no public API) | Parse the `@graph` from built HTML offline; hard pre-deploy gate on zero new errors | Per deploy (+ 2-3 day re-parse) |
| P0 | **Sitemap freshness honesty** (`lastmod` per URL reflects a *real* content change, not the build clock) | Fetch `sitemap.xml` + diff against the standings store | **Known present bug:** `web/site/src/app/sitemap.ts` stamps `lastModified: new Date()` on **every** URL at build time, so every deploy claims every page changed. This violates the §4 freshness rule at the sitemap level. The per-page parity check below does NOT catch it (it checks `dateModified` vs visible text, not sitemap `lastmod`). Add an explicit assertion: a URL's `lastmod` may only advance when that page's content hash changed since the last snapshot. | Per deploy |
| P0 | Crawl access invariant: `Allow: /` + Content-Signal `search/ai-train/ai-input = yes` intact (per `public/robots.txt`), 200 status, correct Link headers | Fetch + assert in CI | Plain HTTP fetch + header assert; **do-not-break invariant, not a lever** (blocking doesn't cut citations but cuts traffic) | Per deploy |
| P1 | Clicks, impressions, CTR, avg position per priority page + query cluster | GSC Search Analytics API | Free; 25k rows/call, paginate; query with **3-day lag**; trailing 7 & 28-day windows | 2-3 days (trend), weekly verdict |
| P1 | Indexed-vs-submitted delta (sitemap URL count minus confirmed-indexed) | GSC + sitemap | Should trend to zero after each deploy | Per deploy |
| P1 | **GSC Gen AI performance report** (AI Overviews / AI Mode **impressions** for the property) | GSC — **UI export today; API may not exist at launch** | The **only first-party Google-AI-surface signal we get.** Hard limits: **impressions-only** (no clicks/CTR/queries as of Jun 2026), **no historical backfill**, UK-first / staged rollout, and **likely UI-only at launch** — so it may be **outside the "all programmatic" loop** and require a manual monthly snapshot. **Snapshot the moment access lands.** See the §7 Phase-1 runbook. | Monthly snapshot (manual until/unless an API ships) |
| P1 | AI-crawler fetch presence per page + llms.txt: GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-SearchBot, PerplexityBot, Google-Extended | Vercel/edge access logs (log drain) + AI-UA regex, **reverse-DNS verified** | Grep logs by UA, verify rDNS (real ClaudeBot → anthropic.com); leading indicator; client-JS analytics cannot see it | 2-3 days (trailing 7-day counts) |
| P1 | GSC query mix: newly-ranking queries + high-impression queries stuck beyond position 10 | GSC Search Analytics API | The content-gap opportunity feed | 2-3 days |
| P2 | Core Web Vitals (75th-pct LCP/INP/CLS) field + History trend | CrUX API + CrUX History API | Free API key; 150/min; 28-day rolling — **do NOT react on the 2-3 day beat, chase only sustained shift / bucket flip** | Weekly |
| P2 | Lab Lighthouse score per page (post-deploy regression guard) | PageSpeed Insights API | Free API key; 240/min | Per deploy |
| P2 | Bing indexation + IndexNow submission success | Bing Webmaster API + IndexNow (keyless POST to api.indexnow.org) | Free; fire IndexNow on deploy. **Premise caveat:** the "ChatGPT Search = Bing index proxy" rationale (true in 2024-25) is **materially weaker by 2026** — OpenAI now runs its own crawler/index (OAI-SearchBot). Keep Bing/IndexNow as cheap, free hygiene; **do NOT over-invest** in a Bing path as a ChatGPT proxy. | Per deploy |

### AEO/GEO signals (probabilistic — observe-and-report only, NEVER auto-act on a single sample)

| Priority | Metric | Source / tool | How the agent reads it | Cadence |
|---|---|---|---|---|
| P0 | Citation rate per engine: fraction of N runs where opentraces.ai (or a priority page) is an actual cited source — **Perplexity is the canonical spine** (Sonar returns a structured `search_results`/`citations[]` array; parse the structured field, not prose) | Perplexity Sonar API (DIY) | Paid, cheap; run each prompt **N≥3-5×**, store distribution. **Spine ≠ sole decider** — see the cross-engine keep/revert rule in §4 | 2-3 days (sampled), trend over 3-5 cycles |
| P0 | Mention rate per engine (named with/without link) across the fixed panel | OpenAI (web_search), Anthropic, Gemini, Perplexity APIs (DIY) | temp 0 + multiple seeds; one row per (prompt, engine, **model_version**, run_index, ts, answer, citations[], mentions[], position) | 2-3 days (sampled) |
| P0 | **Description accuracy** (LLM-judge each answer vs a ground-truth opentraces description; flag wrong license/language/company, the "centralized store" misframing) | LLM-judge pass in the DIY harness | Higher-value than raw mention rate when wrong | 2-3 days (sampled) |
| P1 | Share-of-voice vs the **frozen competitor denominator** (opentraces mentions / total brand mentions across the fixed set) | Same DIY panel | Absolute rate is noisy; SoV is the more stable comparator **only because the denominator is fixed** — see the enumerated set below | Weekly |
| P1 | Which URL got cited (home vs /docs vs /schema vs /explorer) | Perplexity `search_results` | Tells the loop which page format is winning → replicate the pattern | Weekly |
| P1 | **Off-site citation surface** (top third-party domains engines cite for the category: GitHub, HN, Reddit, comparison blogs) | Parse cited domains in the panel | When the gap is off-site → output a **community action** into the second ledger (§4a), not a site edit | Weekly |
| P1 | AI referral sessions (referrer = chatgpt.com, perplexity.ai, gemini.google.com, claude.ai, copilot.microsoft.com) | Self-hosted privacy analytics (in place) | Add an "AI Search" channel grouping; the lagging conversion proof | Weekly |
| P2 | Run-to-run variance / CI width per prompt | DIY panel (bootstrap) | Widening CI = add prompts/runs, **not** a regression | Per cycle |
| P2 | Cross-engine vendor cross-check (consumer surfaces the raw APIs can't hit: ChatGPT.com search, AI Overviews) | Optional: Peec AI (MCP on all paid plans + webhooks) or Otterly (~$29) | Defer until DIY proves a gap worth paying to close | Monthly |

**Frozen share-of-voice competitor set (the denominator).** SoV is meaningless unless the denominator is pinned cycle-to-cycle, so fix it now and version it in the standings store: **Langfuse, LangSmith, Braintrust, Helicone** (the agent-observability core), plus the trace-capture / eval-dataset adjacents **W&B Weave, Arize Phoenix, and Hugging Face datasets-as-a-publish-target**. SoV = opentraces mentions ÷ mentions of any brand in this fixed list, per prompt. Changing the set is a deliberate, recorded event (it breaks comparability with prior cycles), never an ad-hoc per-run choice.

## 3. The monitoring loop (read-only, scheduled)

The monitoring loop is **pure observation with a record-and-do-nothing default.** It runs every 2-3 days, is fully idempotent and reconciliation-based, and emits at most a list of *opportunities* — it never edits the site.

**How idempotency / reconciliation is actually implemented (not just asserted).** Schedulers fire late, twice, or never, so the loop never trusts in-memory or wall-clock state; it re-derives outstanding work from the last committed snapshot:

- **Run identity / dedupe key.** Each monitor run is keyed by its **UTC run-date bucket** (e.g. `2026-06-22` for the 2-3-day beat) plus the **commit SHA of the deployed site at read time**. Before doing any work, the run checks the standings store (`web/site/seo-snapshots/`) for an existing record with that `(run_date_bucket, deploy_sha)` key. If one exists and is complete, the run is a **no-op and exits** — this is how a duplicate/late GitHub Actions fire is detected and skipped.
- **Partial-run resumption.** Each surface's snapshot record carries a `status` of `pending|complete`. A re-fire re-derives the set of `pending` surfaces from the committed JSONL and only fetches those — never re-runs `complete` ones — so a crashed mid-run is resumable and never double-counts.
- **The change-budget lock ("never >1 change in flight") is a real artifact, not an assertion.** It is enforced two ways that must *both* pass before the experiment loop may open a PR: (1) a per-page **ledger-status check** — the loop reads the per-change annotation ledger and refuses if any change for that page has status `in_flight` (deployed, verdict-eligible date not yet reached); and (2) an **open-PR check** via `gh pr list` — refuses if any open PR touches that page's files. The ledger is the source of truth; the open-PR check is the belt-and-suspenders backstop against a ledger that wasn't updated. A change moves `in_flight → judged` only when its verdict date passes and a verdict is recorded, which is what frees the page's budget again.

**Steps per run:**
1. **Pull deterministic signals.** URL-Inspect the ~5 priority pages (verdict + canonical-match + crawl freshness + sitemap + robots state). Pull GSC Search Analytics with a 3-day lag over trailing 7 and 28-day windows. Compute indexed-vs-submitted delta.
2. **Validate structure.** Parse the `@graph` from prerendered HTML for each priority page; assert it parses, required props present, and **JSON-LD facts match visible text** (the tokenization-mismatch correctness check). Assert `Allow: /` + Content-Signal flags + Link headers intact. **Assert sitemap `lastmod` honesty** (a URL's `lastmod` only advanced if its content hash changed) — this is the check that catches the present `sitemap.ts lastModified=now` bug.
3. **Read crawler reachability.** Grep the trailing-7-day log drain for the AI-UA set, reverse-DNS verify, count fetches per priority page and llms.txt. Zero fetches on a page = a real, fixable problem (precondition for citation).
4. **Sample the acceptance gate.** Run the fixed 25-50 prompt panel N≥3-5× per engine (Perplexity Sonar is the citation spine). Compute citation rate, mention rate, SoV against the frozen competitor set, and the description-accuracy judge, each with a bootstrap 95% CI.
5. **Snapshot.** Append one timestamped JSONL record per surface to the standings store (see §5), keyed by `(run_date_bucket, deploy_sha, surface)`. Log `model_version` for every GEO run and any external confounder (Google core-update dates, model-version swaps).
6. **Diff against a trailing baseline.** Compare the latest record to the **median of the last 3-5 runs** (not the single prior run — this absorbs noise).
7. **Alert + classify.** Emit two buckets: **hard regressions** (act fast) and **opportunities** (queue for the experiment loop).

**What triggers an opportunity vs a regression:**
- **Hard regression (immediate fix, may auto-revert):** any priority page drops to not-indexed; Google-selected canonical ≠ declared; JSON-LD validation FAIL; 4xx/5xx or lost `Allow: /` on a priority page; lost rich-result eligibility; a page disappears from sitemap or llms.txt; zero crawler fetches where there were some; **sitemap `lastmod` advanced without a content change** (freshness-gaming regression). These are deterministic — auto-revert (git revert PR / Vercel Instant Rollback) is allowed.
- **Confirmed negative trend (queue, do not panic):** a >15% week-over-week impression or position drop on a priority page **sustained over the trailing 28-day window**, never a single-day delta.
- **Opportunity (queue for the experiment loop):** a category prompt where a competitor is cited and opentraces is not; a GSC query stuck beyond position 10 with high impressions; a wrong description the accuracy judge flags repeatedly; a page format that is winning citations elsewhere and could be replicated. Off-site gaps are emitted as a **community action** into the §4a ledger, not a site change.
- **Never a trigger:** a single-cycle GEO citation dip (sampling noise), a CrUX wobble inside the 28-day rolling window, or a "missing/short llms.txt" flag.

## 4. The experiment loop (gated on external signal)

The experiment loop runs on a **much slower internal clock** than the monitor and fires only when the monitor has surfaced a *confirmed* opportunity (sustained over 3-5 GEO cycles or a 28-day SEO trend) **and** the change-budget lock for that page is free (§3). Per the throughput ceiling in §1, this means **one judged change per page every ~2 months and a single-digit number of judged interventions per year** — that is the expected output, not a high-frequency tweak stream. It is the only place the site is edited, and it is rate-limited by a hard change budget.

**Shape:** `hypothesis → ONE bounded change → deploy via PR → measurement window → keep / revert → record`.

1. **Hypothesis.** State the single targeted metric and the expected direction. Because there is **no baseline until the loop has run a few cycles**, the first-cycle hypothesis target is **directional, not a fabricated decimal**: e.g. "Adding an answer-first definitional block to `/schema` should raise its citation rate for 'what is the opentraces trace schema' relative to its own measured pre-change baseline, judged after 3-4 weeks." Do **not** write targets like "from ~0.2 to >0.4" before any measurement exists and while citation churn alone is reported at 40-60% — that is false precision. Once a real per-prompt baseline distribution exists, a target may be expressed as "above the upper bound of the pre-change bootstrap CI." Pick the *kind* of edit from the **Princeton GEO menu (arXiv 2311.09735)**, treating the percentages as **directional priors from one 2023 Perplexity-measured, query-averaged study — not per-page guarantees, and reported as ranges because the source rows disagree:**
   - add cited sources: **~+30-40%**
   - add concrete statistics / specific numbers: **~+32-41%**
   - add expert quotations: **~+28-41%**
   - improve fluency: **~+15-30%**
   - (structural, not in the original menu but high-leverage) add an answer-first 40-150 word entity-attributed block, one comparison table, or one FAQ/`FAQPage` block.

   **Explicitly forbidden:** keyword-stuffing and over-"AI"-marketing (zero/negative per Princeton, actively punished by the HN/Reddit dev audience), and any llms.txt "citation tuning."
2. **One bounded change.** Exactly one bounded edit (one page's metadata, one schema block, one heading, one answer-first intro) per change-window so cause and effect stay attributable. If the edit changes content, update the visible "Last updated" line *and* `dateModified` together — **never bump the timestamp alone** (engines discount it) and **never let JSON-LD facts drift from visible text** (a correctness bug; LLMs tokenize the script block). **Fix the present sitemap freshness bug as a prerequisite**, not as an experiment: `sitemap.ts` must derive each URL's `lastModified` from that page's real content/source mtime or content hash, not `new Date()`, so the sitemap stops claiming every page changed on every deploy.
3. **Deploy via PR.** The agent **opens a PR, a human merges** (matches the maintainer's PR-first norm). Merge → Vercel auto-deploy (reviewable + Instant-Rollback-eligible), never `vercel --prod` from the agent. Fire IndexNow, re-validate JSON-LD, run the PageSpeed lab check, re-inspect changed URLs on deploy.
4. **Measurement window + FREEZE.** Hold the change untouched for **1-2 weeks minimum** (the recrawl cycle), **3-4 weeks before any causal verdict.** No new edit to that page until the window expires. Use the server-log crawler signal as the early read; expect actual citation movement to lag ~2-4 weeks (per practitioner reports, Perplexity reacts fastest).
5. **Keep / revert — the verdict, with a named method.** A single intervention on a ~5-page low-traffic site has **no clean A/B**, so the verdict is an explicit, weak-for-n=1 **CausalImpact** read, stated honestly as suggestive not proof:
   - **Method:** `google/tfp-causalimpact` (Bayesian structural time-series). It builds a counterfactual "what this series would have done absent the change" and reports whether the post-change series separates from that counterfactual with a credible interval.
   - **Pre-period:** a **stable ~100-day pre-change window** for the target series (do not start the pre-period inside a Google core update or a model-version swap).
   - **Covariates:** **other stable opentraces pages / query-groups that were NOT changed** (e.g. unchanged docs pages' impressions, an unrelated query cluster). They absorb site-wide and seasonal movement so the model attributes only the residual to the change.
   - **Data source:** feed CausalImpact the **least-sampled series available — the GSC→BigQuery bulk export, not the sampled Search Analytics API** (this is *why* §5 mandates turning the BigQuery export on day one; the export is the analytical substrate for the verdict, not just archival).
   - **Keep** if the post-change credible interval separates from the counterfactual in the hypothesized direction with no priority-query cluster dropping >15-20%. **Mark inconclusive** (neither keep nor credit) if the CI straddles zero, if n is too small to separate, or if a known confounder (Google core update, model-version swap) coincided — for n=1 this is the *expected* and honest outcome more often than not.
   - **Auto-revert ONLY on a deterministic regression** (index-status drop, validation failure, lost rich-result eligibility, 4xx/5xx) — **never** on a GEO citation dip or an inconclusive CausalImpact read.
   - **Cross-engine combination rule (resolves spine-vs-don't-overfit).** Perplexity Sonar is the *spine* for **observing** citation movement (it gives structured `search_results`), but a keep decision requires **the change not to regress the Google-index path and at least one corroborating signal beyond Perplexity** — because Perplexity↔ChatGPT citation overlap is reported at only ~11%, a Perplexity-only lift is not sufficient to keep an edit that touches Google-facing content. Concretely: **keep** = (deterministic SEO signals neutral-or-better) AND (Perplexity citation up beyond noise) AND (mention/citation up on ≥1 other engine OR GSC impressions/position not down). A Perplexity-only win with everything else flat = **inconclusive, hold**, not keep.
6. **Record.** Write to a per-change annotation ledger: date, page, the single change, hypothesis, the CausalImpact pre-period and covariate series used, confounders present, the verdict-eligible date (change-date + 21-28d), the `in_flight|judged` status that drives the change-budget lock, and the before/after on the targeted metric. The ledger is the SSoT that proves or disproves lift, enforces the lock, and lets the loop learn.

**Anti-overfitting + change-budget guardrails:** one bounded edit per page per 2-4 week window; never >1 change in flight (enforced by the §3 ledger-status + open-PR lock, not by good intentions); judge GEO on trends over 3-5 cycles, never one cycle; the cross-engine combination rule above (no Perplexity-only keeps); annotate every external confounder and refuse verdicts during update turbulence; cap auto-applied changes to the Princeton low-regret menu and human-gate any content rewrite.

## 4a. The off-site / community action loop (the dominant lever the site loop cannot touch)

The findings are blunt: for a dev tool, the **single biggest GEO lever is off-site** — the third-party citation surface (HN, Reddit, GitHub, comparison/listicle threads), which vendor data puts at the large majority of listicle citations. The rest of this doc is a **site-edit** loop on the GitHub-Actions/Vercel spine; this lever lives **outside** that spine, so it gets its own first-class structure rather than a one-line acknowledgment:

- **Second ledger.** A separate `web/site/seo-snapshots/community-actions.jsonl` (or sibling dir) records each community action: date, surface (which HN thread / subreddit / GitHub discussion / comparison post), the action taken, the linked opentraces page, and the observed outcome.
- **Owner = human, not the agent.** Off-site posting is **human-owned by default** — astroturfing and low-effort self-promotion are punished by exactly the HN/Reddit dev audience we want. The agent's role is to **detect and queue** (surface the gap: "engines cite competitor X on this category prompt via this third-party thread where we're absent") and to **draft** material a human can choose to use; it does not auto-post.
- **Success metric + cadence.** Tracked on the **weekly** AEO beat, not the 2-3 day beat: did opentraces appear (organically/legitimately) in the off-site domains engines cite for our category, and did off-site presence correlate with later citation lift. Because this is the highest-leverage and slowest-moving lever, its cadence is monthly-to-quarterly review, not per-cycle.
- **Honest asymmetry, stated.** The architecture's automated spine optimizes the *smaller* (on-site) lever because that is what is safely automatable; the *larger* (off-site) lever is deliberately human-gated and lower-frequency. This is a real limitation of any safe automated loop here, acknowledged rather than papered over.

## 5. Architecture

**Scheduler: GitHub Actions `schedule:` is the spine.** Vercel Hobby cron is once-per-day, best-effort (no retries, possible dup/missed runs) — adequate only as a heartbeat. GitHub Actions is free for public repos, supports a flexible every-N-minutes/days cadence, runs on the default branch with full filesystem, and gives the build + git + deploy context the experiment step needs *in one place*. A Claude Code scheduled agent / Routine (Anthropic-hosted cron) is the lowest-DevOps alternative, but GitHub Actions wins because the write step needs git + build + PR context co-located. Every run is idempotent and reconciliation-based per the §3 dedupe-key mechanism, so a late/duplicate/missed fire is harmless.

**Executor: Claude Code headless.** The Action invokes `claude -p "<prompt>" --output-format json --allowedTools <scoped> --max-turns <cap> --model <cheaper-for-observe>` and parses the JSON (incl. `total_cost_usd`). Use a cheaper model for the routine observation passes and reserve a stronger model for the propose/implement step. Add hard rails: `--allowedTools` scoping, `--max-turns`, a wall-clock timeout, the spend ceiling sized below, and log cost per run.

**Secrets / auth — the GSC credential is the one most likely to block week 1, so it gets a runbook, not a clause.** All secrets live in **repo (GitHub Actions) secrets**, never in the repo tree: `ANTHROPIC_API_KEY`, `PERPLEXITY_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, the free **CrUX** and **PageSpeed Insights** API keys, and the **GSC service-account JSON key**. The GSC + BigQuery path has a multi-step gotcha every finding flagged — do it in this order:
1. **Create a Google Cloud project** (or reuse one) and **enable** the *Search Console API*, *Indexing API* (optional), and *BigQuery API*.
2. **Create a service account** in that project; generate a JSON key; store the JSON in `GSC_SERVICE_ACCOUNT_JSON` repo secret.
3. **Add the service account's `client_email` as a USER on the GSC property** (Search Console → Settings → Users & permissions) with at least **Restricted/Full** access; the API call needs `https://www.googleapis.com/auth/webmasters.readonly`. *This step is the usual week-1 blocker:* the key existing is not enough — the property must explicitly grant that email.
4. **For the BigQuery bulk export:** the export needs a **billing-enabled BigQuery project + a dataset**, and the **GSC service agent** (a Google-managed `search-console-...@gcp-sa-...` account, distinct from your service account) must be **granted BigQuery Job User + Data Editor** on that project. Configure the export in **GSC → Settings → Bulk data export**. It is **not retroactive** — it only accumulates from the day it is enabled, which is why it goes on day one (see below).
5. Verify end-to-end before wiring the loop: one manual URL-Inspect call and one BigQuery query that returns rows.

**Cost model + spend ceiling (sized, not just named).** The GEO panel is the dominant cost. Worst case as written: **~40 prompts × 8 runs × 4 engines ≈ 1,280 model calls per cycle**, on a ~2-3 day cycle (~10-12 cycles/month) → on the order of **~13-15k panel calls/month**. At small-prompt sizes these are cheap per call, but unmanaged this is the line item most likely to make a solo maintainer silently switch the loop off. Controls, in order of preference:
- **Spend ceiling = a hard monthly dollar cap** (suggest **~$20-30/month** all-in for the panel + headless executor, tuned after the first month's real `total_cost_usd` numbers) enforced two ways: a per-run `total_cost_usd` budget that aborts the run if exceeded, and a monthly rollup in the standings store that pauses the experiment/propose step (not the cheap monitor) when the cap is hit.
- **Panel rotation to stay under the cap:** run the **full ~40-prompt panel weekly**, and on the intermediate 2-3-day beats run a **rotating subset (~10-12 prompts) + N=3** rather than the full 8×40. This drops the typical-cycle volume by ~4-6× while preserving weekly full coverage and the trend signal.
- **Engine tiering:** Perplexity Sonar (the spine) every cycle; the other three engines on the **weekly** full-panel run only. This alone roughly quarters per-cycle calls on the intermediate beats.
- Always use the cheaper model for observation; log per-run and per-month cost to the standings store so the ceiling is reviewed against reality, not a guess.

**Storage of standings: append-only JSONL committed to the repo** (e.g. `web/site/seo-snapshots/`, alongside the existing `DISCOVERABILITY-LOOP.md` ledger). One timestamped record per surface per run, keyed by `(run_date_bucket, deploy_sha, surface)` (the §3 dedupe key). This yields free history, diffs, and PR-based review, and mirrors opentraces' own append-only-event-log mental model. **Start archiving GSC now** — 16-month retention means waiting loses year-over-year; **enable the GSC→BigQuery Bulk Data Export on day one** (not retroactive; it is also the substrate the §4 CausalImpact verdict reads). The per-change annotation ledger, the **community-action ledger (§4a)**, and the GEO transcript store (full answers, you own them) live in the same dir.

**Deploy path:** PR-merge → Vercel git auto-deploy → Instant Rollback on regression. Hard-gate every deploy on: build success + zero new structured-data errors + priority pages render + canonicals/robots/sitemap/Link-headers intact + **sitemap `lastmod` honesty** + no Core Web Vitals lab regression. The `DISCOVERABILITY-LOOP.md` ledger discipline (observe → one change → verify → record → ask before deploy) extends verbatim to these external metrics.

## 5a. Termination / wind-down (this loop is allowed to stop)

The `DISCOVERABILITY-LOOP.md` this extends has an explicit STOP rule and so must this one — a monitoring loop with no exit on a ~5-page low-traffic site will plateau fast and quietly waste budget. Define states:
- **Active (full cadence):** the default while the experiment loop is still finding and judging opportunities.
- **Plateau → drop to monthly:** when **two consecutive ~2-month experiment cycles produce no judged keep** (all inconclusive or reverted) **and** no hard regressions are firing, the available on-site lift is effectively extracted. Drop the **monitor** to **monthly** (keep the deterministic regression watch — it's cheap and catches breakage), pause the proactive experiment loop, and re-route remaining energy to the off-site lever (§4a), which is where the real lift was anyway.
- **Cost/value reassessment trigger:** any month where spend approaches the ceiling **and** no keep was credited in the trailing two cycles forces an explicit "is this worth continuing at this cadence?" review, recorded in the ledger.
- **Re-activate** from plateau only when the monitor surfaces a genuinely new opportunity (a new competitor, a new category prompt, a Google-AI-surface change) — not on a calendar.

This makes "we've extracted the available lift, drop to monthly" a real, recorded state rather than an infinite background process.

## 6. The target-question panel

A fixed panel of ~20-50 prompts is the AEO/GEO measurement primitive; below ~15 it is statistically noise. AI answers favor 7+ word informational questions, not keywords. Cover three buckets: **brand** (does the engine know us correctly), **category** (do we get cited for the job we do), and **comparison** (head-to-head against the frozen §2 competitor set). Run each N≥3-5× per engine at temp 0 + multiple seeds, log `model_version` per run (and respect the §5 panel-rotation / engine-tiering controls). Seed set (expand to ~30):

1. What is opentraces?
2. How do I capture Claude Code agent sessions / traces?
3. What tools capture agent traces locally before publishing them?
4. How do I publish agent traces as a Hugging Face dataset?
5. What is a good schema for agent trace data?
6. How do I redact secrets / PII from agent traces before publishing?
7. What's the best CLI for turning agent runs into eval datasets?
8. How can I attribute which commits an AI agent actually authored?
9. Tools to track which agent edits survived into main vs were reverted?
10. What is the opentraces trace schema / what fields does it capture?
11. opentraces vs Langfuse for agent trace capture?
12. opentraces vs LangSmith / other agent-observability tools?

**Scoring rubric (per prompt × engine × run, then aggregate to a rate with a bootstrap 95% CI):**

| Dimension | Definition | Scoring |
|---|---|---|
| **Mention** | Is "opentraces" named in the answer text? | 1 / 0 |
| **Citation (correct URL)** | Does a priority page (opentraces.ai, /docs, /schema, /explorer, or github.com/JayFarei/opentraces) appear as an actual cited source — parse Perplexity `search_results` / engine citation array, not prose | 1 / 0, plus which URL |
| **Accuracy** | LLM-judge vs ground-truth: "open-source CLI that captures agent traces repo-locally, sanitizes them, and optionally publishes structured JSONL datasets to Hugging Face." Flag wrong license, wrong language, wrong company, "centralized store" misframing | accurate / partial / wrong (+ the specific hallucination) |
| **Position** | Rank of opentraces within the answer (1st named option, in a list, or buried) | median rank-within-answer |
| **Share-of-voice** | opentraces mentions ÷ all mentions of the frozen competitor set (§2) for that prompt | ratio |

An **inaccurate-but-frequent** result is worse than absent and is the highest-value fix target. Track "mentioned" and "cited as source" as **distinct** metrics — they diverge.

## 7. Phased rollout

Order by cheapest-high-signal-no-credential first, credentialed/paid last. Note that Phases 0-2 stand up the *measurement* loop; the **net-new content build** (§1: answer-first blocks, comparison tables, FAQ blocks, entity layer) is a **separate Cycle-1-3 build-out** that runs through the §4 experiment loop once measurement exists — it is the actual citation lever, not part of standing up the pipes.

- **Phase 0 — zero-credential, today (highest leverage, free):** Stand up the append-only JSONL standings store + the per-change annotation ledger + the **community-action ledger (§4a)** in the repo (extend `DISCOVERABILITY-LOOP.md`). Wire the **server/edge log AI-crawler grep + reverse-DNS** off the existing Vercel log drain — the ~2-4 week leading indicator that needs no API. Add the **JSON-LD parse + visible/markup parity check** AND the **sitemap `lastmod`-honesty check** to CI as hard pre-deploy gates. **Fix the present `sitemap.ts lastModified=now` bug** (derive per-URL `lastModified` from real content mtime/hash). Add an **"AI Search" referral channel** to the self-hosted analytics. Wire **IndexNow** (keyless) into the deploy step.
- **Phase 1 — cheap API credentials, week 1:** Run the **GSC + BigQuery auth runbook in §5** end-to-end (service account → property user grant → BigQuery export → verify) — budget real time here, it is the usual week-1 blocker. Make URL-Inspect indexation the primary 2-3 day check. **Turn on GSC→BigQuery Bulk Export on day one** (not retroactive; it is the CausalImpact substrate). Add free **CrUX + PageSpeed Insights** API keys. **Snapshot the GSC Gen AI performance report the moment access lands** — accept that it may be **UI-only / impressions-only / no backfill / staged-rollout**, so plan a **manual monthly snapshot** and treat any later API as a bonus, not an assumption.
- **Phase 2 — cheap paid, week 2:** Build the **DIY Perplexity Sonar panel** (citation spine, structured `search_results`), then add OpenAI (web_search), Anthropic, and Gemini for the multi-engine mention/accuracy rates with bootstrap CIs. Implement the **cost ceiling + panel-rotation + engine-tiering** controls from §5 from the start. Wire the **GitHub Actions scheduler + Claude Code headless executor** with the dedupe-key idempotency, cost caps, and `--allowedTools` scoping.
- **Phase 3 — optional vendor, only if DIY proves a gap:** Add **Bing Webmaster API + IndexNow** as cheap free hygiene — **not** as a ChatGPT-Search proxy (that premise has eroded; OpenAI runs its own index/crawler by 2026). Add one consumer-surface vendor (**Peec AI** for its MCP-on-all-plans + webhooks, or **Otterly** ~$29 as a cheap cross-check) only to cover ChatGPT.com search / AI Overviews the raw APIs can't faithfully hit, and to cross-validate the DIY matcher. Reserve **SERP APIs** (DataForSEO over post-lawsuit SerpApi) for the narrow things GSC cannot see (AI-Overview presence, competitor positions), gated and budgeted.

## 8. Sources

- https://developers.google.com/webmaster-tools/limits
- https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect
- https://developers.google.com/search/blog/2023/02/bulk-data-export
- https://developers.google.com/search/docs/fundamentals/ai-optimization-guide
- https://developers.google.com/search/docs/appearance/ai-features
- https://developers.google.com/search/blog/2026/06/gen-ai-performance-reports
- https://developers.google.com/search/docs/monitor-debug/debugging-search-traffic-drops
- https://cloud.google.com/bigquery/docs/search-console-transfer
- https://developer.chrome.com/docs/crux/api
- https://www.corewebvitals.io/pagespeed/the-crux-28-day-delay-myth
- https://www.bing.com/indexnow/getstarted
- https://learn.microsoft.com/en-us/bingwebmaster/
- https://ahrefs.com/blog/llmstxt-study/
- https://www.searchenginejournal.com/googles-mueller-says-llms-txt-cant-help-llms-differentiate-sites/579304/
- https://www.techwyse.com/news/ai-search/google-llms-txt-no-ranking-benefit-june-2026
- https://arxiv.org/pdf/2311.09735
- https://collaborate.princeton.edu/en/publications/geo-generative-engine-optimization/
- https://arxiv.org/abs/2604.07585
- https://www.similarweb.com/blog/marketing/geo/ai-citation-volatility/
- https://nicklafferty.com/blog/ai-visibility-metrics-reference/
- https://docs.perplexity.ai/docs/agent-api/prompt-guide
- https://peec.ai/blog/peec-ai-mcp
- https://otterly.ai/
- https://discoveredlabs.com/blog/profound-vs-peec-vs-otterly-which-ai-visibility-platform-should-you-buy
- https://www.searchpilot.com/resources/blog/what-is-seo-split-testing
- https://www.searchpilot.com/resources/blog/do-it-yourself-seo-split-testing-tool-with-causal-impact/
- https://www.searchenginejournal.com/googles-new-ai-search-guide-calls-aeo-and-geo-still-seo/575026/
- https://github.com/google/tfp-causalimpact
- https://ppc.land/blocking-ai-crawlers-doesnt-stop-citations-new-data-shows-why/
- https://blog.cloudflare.com/content-signals-policy/
- https://nohacks.co/blog/ai-user-agents-landscape-2026
- https://web-alert.io/blog/ai-crawler-bot-monitoring-gptbot-claudebot-perplexitybot-guide
- https://www.insightscout.co/guides/how-reddit-and-hacker-news-shape-ai-recommendations
- https://vercel.com/docs/cron-jobs/usage-and-pricing
- https://vercel.com/docs/instant-rollback
- https://code.claude.com/docs/en/scheduled-tasks
- https://www.codewithseb.com/blog/claude-code-headless-mode-cicd-automation-playbook
- https://github.com/anthropics/claude-code-action
- https://www.digitalapplied.com/blog/ai-share-of-voice-tracking-brand-citations-framework-2026
- https://www.semrush.com/blog/ai-mode-comparison-study/

> **Provenance note on vendor statistics.** The figures "~81% third-party listicle citations," "40-60% monthly citation churn," "~11% Perplexity↔ChatGPT overlap," and Perplexity's "45-point freshness premium" originate in single vendor/blog studies (Similarweb, BuzzStream, AthenaHQ, insightscout, and AEO-platform marketing). They are unreplicated and vendor-marketing-adjacent. This doc uses them as **directional priors only** and never as keep/revert thresholds or hypothesis targets. The Princeton lift percentages are from one 2023 study (arXiv 2311.09735), Perplexity-measured and query-averaged — treat them the same way.

## Phase 0 — implementation status (wired vs deferred)

Phase 0 was built and adversarially reviewed (see the closed defects below). This section is the **source of truth for current status** — read §2/§3/§7 above as the design, not as a claim that everything is implemented. The honest split:

**Wired and verified (runs today, no credentials):**
- **Standings store** — `seo-snapshots/` with three append-only ledgers + JSON schemas + README. Writers: `crawler-report` (`crawler_hits`), `seo-check --write` (`seo_check`), `indexnow-submit --submit` (`indexnow`). They append **unconditionally** — the §3 "duplicate scheduler fire is a no-op" dedupe and pending-resumption are a Phase 1 scheduler step, NOT yet implemented ("latest per key wins" holds; the no-op safety does not).
- **Deterministic SEO gate** — `scripts/seo/seo-check.mjs` + `.github/workflows/seo-checks.yml`. Per priority page: title/description/canonical/indexable (meta-robots), JSON-LD validity + light name parity + **no-misframing on curated nodes** (rejects a "centralized SaaS" drift) + **SoftwareApplication fact allowlist** (MIT license, Python, free); robots real `Allow:/` + **no blanket `Disallow`** + **Content-Signal=yes** (values, not presence); sitemap smoke test. 71 checks green. Hardened against the false-negatives an adversarial pass found (value-blind robots checks, one-word parity, whole-HTML noindex grep).
- **Crawler report** — `scripts/seo/crawler-report.mjs`. Under `--verify`, the load-bearing signal (coverage/total) counts **only rDNS-verified** hits, so a forged `User-Agent: GPTBot` from a random IP can neither inflate presence nor mask a zero-fetch regression; unverified counts are reported separately as "claimed". Parser handles NDJSON + plain access logs (incl. trailing fields) + array-valued fields + IPv6 forward-confirm. The PARSER is done and tested; a real **log SOURCE is NOT configured** (it reads a file/stdin) — see deferred.
- **AI-referral classifier** — `src/lib/ai-referrers.ts`. Exact/suffix host matching (spoof-safe; `myopenai.com.evil.net` ≠ AI), http(s)-only, ccTLD-family boundary match. 11/11 unit cases. This is a **lib only — imported by nothing yet**; the "AI Search" channel is not lit up (see deferred).
- **IndexNow** — `scripts/seo/indexnow-submit.mjs` + live public key file + config. Dry-run is the default and is genuinely **side-effect-free** (sends nothing, writes nothing); `--submit` verifies the key file **content** (not just reachability) before posting. Built and dry-run-validated; **not fired live**, and **not auto-wired** into deploy.
- **Sitemap freshness fix** — shipped (Cycle 8 in `DISCOVERABILITY-LOOP.md`): real git-derived per-URL `lastmod`.

**Deferred (needs infra / scheduler / credentials — NOT done despite §2/§3/§7 phrasing):**
- **Scheduled monitor** (the 2–3 day cron) — Phase 0 ships the scripts, not the scheduler. GitHub Actions cron + Claude Code headless executor is Phase 1 (§5).
- **§3 dedupe/no-op idempotency** + pending-resumption — Phase 1.
- **Content-hash sitemap `lastmod` honesty** — the gate currently checks only "no `new Date()` in source + differentiated dates" (a weaker smoke test), NOT "`lastmod` advanced only when the content hash changed". The real content-hash check (§2/§3) is deferred.
- **Crawler log source** — a Vercel Log Drain (or scheduled `vercel logs --json` feed) must be configured; until then the crawler signal has no data.
- **IndexNow auto-fire on deploy** — the site deploys via Vercel git-integration (no GH Actions deploy job), so a post-deploy trigger is needed; today it is a manual `npm run seo:indexnow -- --submit` after the key is live.
- **AI-referral channel wiring** — the analytics tracker is an external Cloudflare worker; lighting up the channel needs either editing that worker with `classifyReferrer`, or a consumer that reads its event export and writes a `referrals` standings record.
- **All credentialed signals** (GSC, BigQuery, the GEO citation panel, CrUX) — Phase 1/2.
