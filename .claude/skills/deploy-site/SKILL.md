---
name: deploy-site
description: >
  Land every site-related fix on main, deploy opentraces.ai to Vercel
  production, and verify it works across all pages (desktop + mobile) and the
  email-capture flow, fixing and re-deploying until green. Runs as a goal:
  land -> deploy -> verify -> triage -> repeat until safe. Use when the user
  says "deploy site", "deploy to vercel", "push site", "ship the site",
  "deploy-site", or "land and verify the site". For a full coordinated release
  that also bumps the version and publishes the Python packages, use
  /release-pack instead.
---

# Deploy Site

Deploy the Next.js marketing site (`web/site/`) to Vercel production, but treat
it as a goal, not a single command: land every site-related fix on `main`,
deploy it, prove it works in production across all pages and the email-capture
flow on desktop and mobile, and keep fixing + re-deploying until everything is
green.

## Goal contract

Run this skill as a closed loop. Do not declare success on a clean Vercel
deploy alone; a deploy can succeed while a page 500s, a layout breaks on mobile,
or the waitlist write silently fails.

- **DONE** when: all site-related fixes are committed and pushed to `main`, the
  production deploy succeeded, every route below returns 200 and renders
  correctly on desktop **and** mobile with no console/network errors, and the
  email-capture flow accepts a submission and persists it (proven by admin
  read), with no test rows left behind.
- **NOT DONE** (loop again) when: any route fails to render, a layout breaks at
  a viewport, the console shows errors, or email capture does not round-trip.
  Fix in source, re-land, re-deploy, re-verify.
- **BLOCKED** (stop and report) when: a fix needs a credential/decision you
  don't have (e.g. KV store down, admin token missing, Vercel auth expired), or
  the same failure survives 3 fix attempts. Report the exact failure + what you
  tried; do not loop forever.

Keep a short running log of each iteration (what failed, what you changed,
re-verify result) so the loop is auditable. If the user wants this as a
persistent `/goal`, point the goal at this skill and use the same DONE/BLOCKED
conditions.

## The loop

```
Phase 0  Scope & land     gather site fixes -> local verify -> commit -> push main
Phase 1  Deploy           build -> npx vercel@latest --prod (from repo root)
Phase 2  Verify (prod)    Workflow fan-out: routes x viewports + email capture
Phase 3  Triage           any red? fix in source -> back to Phase 0
                          all green? -> DONE
```

## Context

- **Project**: `opentraces` on Vercel (scope `jayfareis-projects`)
- **Framework**: Next.js 16 (App Router)
- **Root directory**: Vercel is configured with root at `web/site/`
- **Domain**: opentraces.ai (production: https://opentraces.ai)
- **Build**: `next build` (runs from `web/site/`)
- **Version**: Auto-read from `src/opentraces/__init__.py` at build time via `next.config.ts`
- **Email capture**: `EarlyAccessForm` posts to `/api/waitlist` (Upstash/KV in prod). Renders on `/` (via `HubTeaser`) and `/hub`. See the Reference section for selectors + admin read.

---

## Phase 0 — Scope & land the site fixes

1. **Find what is site-related and unlanded.** The working tree often holds a
   mix of site + non-site changes. Land only the site fixes here (the rest is a
   separate concern).

   ```bash
   git status --short web/site/
   git status --short -- ':!web/site/'   # non-site changes, usually leave these
   git log --oneline origin/main..HEAD -- web/site/   # site commits not yet pushed
   ```

2. **Local verify before landing (gate).** Run the site against the
   already-running dev server and check the changed surface with agent-browser:
   light + dark, desktop + mobile, the specific interaction you changed, plus
   console/network clean. This is the pre-land gate; do not commit a visual
   regression. (If no dev server is up, `cd web/site && npm run dev` on a spare
   port, verify, then stop it.)

3. **Build locally** (catches type/build errors the dev server hides):

   ```bash
   cd web/site && npm run build
   ```

   Fix any build failure before continuing.

4. **Commit to main and push.** Per project convention, commit straight to
   `main` (do not auto-create a feature branch unless asked). Keep the commit
   scoped to the site fix; co-author the commit per the repo footer rule.

   ```bash
   git add web/site/   # stage only the site fixes you verified
   git commit -m "fix(site): <what changed>"
   git push origin main
   ```

---

## Phase 1 — Build & deploy

Deploy from the **repo root** (not `web/site/`), because Vercel resolves the
root directory from its project settings (`rootDirectory=web/site`). Running
from inside `web/site/` double-nests the path. Always pin to the latest CLI
(`npx vercel@latest`).

```bash
cd /path/to/repo/root
rm -rf web/site/.next kb/app/.next   # stale build output; bloats the upload
npx vercel@latest --prod --yes
```

**Do NOT use `--archive=tgz`** (measured 2026-06-26, CLI 54.x). `--archive=tgz`
tars the whole working tree and **bypasses `.vercelignore`**, so it hauls up
`.git`-minus-nothing-else plus `kb/`, `.otbox/`, `web/site/.next-test/`, etc. —
a **~3 GB / 1998-file** upload that takes minutes and once **hung for ~20 min at
"Extracting deployment files"** on the build machine (a real prod stall we hit).
The default **file-by-file** path on `vercel@latest` instead:

- **respects `.vercelignore`** → ~628 files / ~40 MB,
- uploads in **~2 s** and reaches READY in **~60 s end-to-end**,
- does **not** hit the `Error: Upload aborted` that `--archive=tgz` was
  originally added to dodge (that was a *brew 41.x* endpoint bug; `vercel@latest`
  fixed it, so the workaround is now net-negative).

If a future CLI regresses the file-by-file path and you must fall back to
`--archive=tgz`, expect the ~3 GB archive and a multi-minute (occasionally
stalling) extraction — retry the deploy if it hangs at extraction.

Capture the deployment URL/id from the output; you'll reference it in the verify
report.

> Tip: you can verify a Vercel **preview** deploy first (omit `--prod`) and run
> Phase 2 against the preview URL before promoting. This keeps email-capture
> tests off the production list entirely.

---

## Phase 2 — Verify in production (JIT harness via Workflow / ultracode)

Prove the live site works. This phase is a **just-in-time harness**: you (the
planner) scope the run for *this* deploy, the **Workflow** tool (the
deterministic runtime) executes it, and ephemeral browser **workers** each
return against a typed schema. Intermediate state lives in the harness's
variables, not in your context, so the full matrix runs without filling your
window. This skill explicitly opts into Workflow orchestration (ultracode).

The harness is bundled and frozen next to this skill:
`verify-site-deploy.workflow.js`. Don't paste the script inline; run the file.

### What to check

- **Routes** (each must 200 + render, no console/network errors):
  `/`, `/explorer`, `/hub`, `/schema`, `/docs`, plus a spot-check of deep pages:
  `/schema/0.6.0`, `/docs/getting-started/installation`, `/docs/cli/commands`,
  `/docs/schema/trace-record`.
- **Viewports**: desktop `1440x900` and mobile `390x844` (iPhone-class). Where a
  fix touched theming, also check light + dark.
- **Per page**: HTTP 200, primary content visible, no layout overflow/clipping
  at the viewport, console clean, no failed network requests, nav works.
- **Email capture** (its own agent, see below).

### Email-capture verification (do not pollute the prod list)

`/api/waitlist` writes to a live store and has **no DELETE endpoint**, so be
deliberate:

- **Real round-trip (once, desktop)**: submit a tagged address like
  `deploy-verify-<timestamp>@opentraces.ai`. Expect HTTP 200 `{ok:true}` and the
  button flipping to the success state. Confirm it persisted with the admin read
  (`GET /api/waitlist` + `Authorization: Bearer $WAITLIST_ADMIN_TOKEN`), noting
  count before/after. **Then clean it up** by removing that row from the store
  (Upstash `ZREM`), and re-read to confirm the count is back. Leave the list
  pristine.
- **Dedup path**: submit the same address twice, expect "you're already on the
  list" with the count unchanged.
- **Mobile + repeat UI checks without writing**: fill the hidden honeypot
  (`input[name=company]`) on the submit. The endpoint returns a silent
  fake-success and never stores the row, so you can confirm the success UI on
  mobile without adding junk. (If you see "success" but the count did not move,
  that's the honeypot working, not a bug.)
- If the endpoint returns **503** ("waitlist is not configured"), the KV store
  is not wired on that deployment: that's BLOCKED (env/store issue), not a code
  fix. Report it.

### Run the harness

Invoke the bundled artifact with the Workflow tool. Scope it per deploy with
`args` (this is the JIT "planner" step):

```
Workflow({
  scriptPath: ".claude/skills/deploy-site/verify-site-deploy.workflow.js",
  args: {
    baseUrl: "https://www.opentraces.ai",  // canonical host; or a preview URL to keep email off prod
    changed: ["/hub", "/"],             // routes this deploy touched -> deep-checked; rest are smoked
    pagesOnly: false                    // true = skip the email write (read-only validation runs)
  }
})
```

It returns `{ base, scoped, failures, allGreen }`. `allGreen: true` means Phase 2
passed; otherwise `failures` lists exactly what to fix in Phase 3.

How the harness applies the JIT primitives (see
`kb/docs/concept-jit-harnesses-generic.md`):

- **Classify-and-route**: if you pass `changed`, those routes get a full browser
  check (console, overflow, screenshot); every other route is a cheap `curl`
  status only. Plain JS control flow the runtime owns, no model in the seam, so a
  scoped deploy stays cheap.
- **One deterministic sweep worker per viewport** (2 workers, not ~18): each runs
  a single fixed bash batch (`curl` + a bounded set of agent-browser calls per
  route) on a cheap model (`haiku`) and returns the parsed JSON. No open-ended
  "verify everything" prompt, so the model can't loop; each batch opens one
  browser session and `close --all`s it, so Chrome processes don't pile up.
  (Validated 2026-06-09: ~16s for a full-deep viewport sweep, 1 leftover proc,
  vs. the original per-cell design's 54 procs / 15+ min.)
- **Independent adversarial confirm**: any route a sweep flags as failed is
  re-checked by a *separate* worker with a clean session (default model) that
  tries to reproduce it; a failure survives only if the confirm also fails, so an
  agent-browser fumble doesn't masquerade as a site bug.
- **Typed contracts**: every worker returns against `PAGE_VERDICT` /
  `EMAIL_VERDICT`, so results merge mechanically.
- **Email capture** gets its own worker that proves the write via an independent
  admin read and cleans up the test row (see the no-pollution rules above).

To change the matrix, the depth policy, or the verdict schema, edit the `.js`
file, it is the single source of truth. If a change is one page and you don't
want orchestration, you can still run that page's check sequentially with the
agent-browser skill in the main session.

---

## Phase 3 — Triage & iterate

Read the workflow's `failures`:

- **Empty** -> the goal is met. Report: deployment id/URL, routes verified,
  viewports, email round-trip proven + cleaned up. **DONE.**
- **Non-empty** -> for each failure, find the root cause in `web/site/` source,
  fix it, and go back to **Phase 0** (local verify -> commit -> push -> deploy ->
  re-verify). Re-run verification scoped to the failed routes first, then a full
  pass once they're green.
- After 3 unsuccessful attempts on the same failure, or on a
  credential/store/auth blocker, stop and report as **BLOCKED** with the exact
  error and what you tried.

Record each iteration in the running log so the loop is auditable.

---

## .vercelignore

**Location matters: the effective file is the repo-root `.vercelignore`.** Even
though the project's Root Directory is `web/site`, the whole repo is uploaded
(the build reads `../../src/opentraces/__init__.py` and `../../skill/SKILL.md`),
and the **only** `.vercelignore` the file-by-file upload honors is the one at the
**repo root** — patterns are matched relative to the repo root. A
`web/site/.vercelignore` is **ignored**; do not add one (verified 2026-06-26 via
`npx vercel@latest --debug`).

It is a positive-ignore list (explicitly enumerated directories, **not** a `*`
catch-all with `!` negations — negations are fragile and break across CLI
versions). Without it the upload includes the heavy repo trees. The big ones it
must exclude (each was a real leak we fixed):

- `kb/` (~2.4 G), `.claude/` (~1.5 G), `tests/` (~1.4 G), `.venv*/` (~0.8 G)
- `.otbox/` (~613 M of snapshot tarballs)
- `web/site/.next-test/` (~98 M / 1000+ files — note `**/.next/` does **not**
  match the `-test` suffix, so `.next-test/` needs its own rule)
- `web/viewer/`, `web/coming-soon/`, `web/metrics-worker/`

> Caveat: `.vercelignore` only filters the **file-by-file** upload path. The
> `--archive=tgz` flag bypasses it (see Phase 1 — another reason not to use it).

To sanity-check what would upload after editing `.vercelignore`, run
`npx vercel@latest --debug` from the repo root and read the
`Found N files … M required to upload` line, then Ctrl-C — no full deploy needed.
A healthy site deploy is **~628 files**, not ~2000.

Files that must NOT be ignored (the build reads them at build time):

| File | Read by | Purpose |
|------|---------|---------|
| `src/opentraces/__init__.py` | `next.config.ts` | Version detection for `version.json` |
| `skill/SKILL.md` | `lib/docs.ts` (`getSkillContent()`) | Machine view content on docs pages |

If you add a new file that the site reads at build time via a relative path like
`../../foo`, ensure it is not excluded by `.vercelignore`.

## Reference

**Routes** (Phase 2 matrix): `/`, `/explorer`, `/hub`, `/schema`,
`/schema/[version]`, `/docs`, `/docs/[[...slug]]` (~36 doc slugs from
`lib/doc-nav.ts`). API: `/api/waitlist` (POST add, GET admin read),
`/api/stars`.

**Email form selectors** (`EarlyAccessForm.tsx`): email input
`input[type=email]` / `aria-label="email address"` / `.ea-input`; submit
`button.ea-btn` ("get early access" -> loading -> success); hidden honeypot
`input[name=company]` (filling it -> silent fake-success, never stored).

**Waitlist admin read**: `GET /api/waitlist` with
`Authorization: Bearer $WAITLIST_ADMIN_TOKEN` (set on Vercel Production; pull
locally with `vercel env pull` if needed). No DELETE endpoint: remove test rows
directly from the store (Upstash `ZREM`). Store is Upstash Redis
(`opentraces-waitlist`) in prod, local `.data/waitlist.json` in dev.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Deploy hangs for minutes at `Extracting deployment files` (and upload showed `(…/3GB)`) | You used `--archive=tgz`, which bypasses `.vercelignore` and uploads a ~3 GB / 1998-file archive. Drop the flag: `npx vercel@latest --prod --yes` → ~628 files, ~60 s. If already hung, `vercel remove <stuck-url> --yes` and redeploy without the flag |
| `Error: Upload aborted` repeating across many files | Old brew CLI bug. `npx vercel@latest --prod --yes` (file-by-file on 54.x no longer aborts; do **not** reach for `--archive=tgz`) |
| `Your Vercel CLI version is outdated. This endpoint requires version 47.2.2 or later` | Switch to `npx vercel@latest` |
| Upload is ~3 GB / ~2000 files when it should be ~40 M / ~628 | Either `--archive=tgz` is set (drop it) or a heavy dir leaked into the repo-root `.vercelignore`. Check with `npx vercel@latest --debug` and add the dir (common: `.otbox/`, `.next-test/`) |
| "provided path does not exist" (e.g. `web/site/web/site`) | You ran `vercel` from `web/site/`. Deploy from repo root (the project has `rootDirectory=web/site` server-side) |
| "Root Directory does not exist" | `.vercelignore` is excluding `web/site/`. Check ignore patterns, avoid `*` catch-all with negations |
| Build fails on `version.json` | `next.config.ts` generates it at build time; check `src/opentraces/__init__.py` is not in `.vercelignore` |
| Machine view empty on production | `skill/SKILL.md` excluded by `.vercelignore`, `getSkillContent()` returns `""` |
| OG image not showing | Check `metadataBase` in `layout.tsx` is set to `https://opentraces.ai` |
| Theme flash after deploy | Verify `layout.tsx` uses raw `<script>` tag, not `next/script` |
| Cloudflare API 503 on deploy | Transient, retry the deploy |
| `/api/waitlist` returns 503 in verify | KV/Upstash store not wired on that deployment. BLOCKED (env), not a code fix. Check Vercel env on Production |
| Email "success" but admin count unchanged | The honeypot (`company`) was filled, so the row was intentionally not stored. Resubmit without the honeypot for a real write |
| Mobile form not visible / clipped | Known prior fix: `hub-teaser-foot` must collapse to natural height (`globals.css`). Re-check at 390x844 |
| Verify passes but a test row remains | Clean it up via Upstash `ZREM` and re-read admin count before declaring DONE |
