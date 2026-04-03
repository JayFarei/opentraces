---
name: deploy-site
description: >
  Deploy the opentraces.ai marketing site to Vercel production.
  Use when the user says "deploy site", "deploy to vercel", "push site",
  "ship the site", or "deploy-site". For a full coordinated release that
  includes a version bump and package publish alongside the site deploy,
  use /release-pack instead.
---

# Deploy Site

Deploy the Next.js marketing site (`web/site/`) to Vercel production.

## Context

- **Project**: `opentraces` on Vercel (jayfareis-projects/opentraces)
- **Framework**: Next.js 16 (App Router)
- **Root directory**: Vercel is configured with root at `web/site/`
- **Domain**: opentraces.ai
- **Build**: `next build` (runs from `web/site/`)
- **Version**: Auto-read from `src/opentraces/__init__.py` at build time via `next.config.ts`

## Steps

### 1. Verify build locally

```bash
cd web/site && npm run build
```

If the build fails, fix issues before deploying.

### 2. Commit and push

Ensure all changes are committed and pushed to `main`:

```bash
git status
git push origin main
```

### 3. Deploy to Vercel

Run the deploy from the **repo root** (not `web/site/`), because Vercel resolves the root directory from its project settings:

```bash
cd /path/to/repo/root
npx vercel --prod
```

### 4. Verify

Check the deployment URL in the Vercel output. The production URL is:

```
https://opentraces.ai
```

## .vercelignore

The repo uses a positive-ignore `.vercelignore` (explicitly listing directories to exclude, not a `*` catch-all with negations). This matters because:

- **Negation patterns (`*` then `!web/`) are fragile** and break across Vercel CLI versions. Avoid them.
- The Vercel CLI merges `.gitignore` rules into `.vercelignore` (26 combined rules), which can cause unexpected exclusions with negation patterns.

Files that must NOT be ignored (the build reads them at build time):

| File | Read by | Purpose |
|------|---------|---------|
| `src/opentraces/__init__.py` | `next.config.ts` | Version detection for `version.json` |
| `skill/SKILL.md` | `lib/docs.ts` (`getSkillContent()`) | Machine view content on docs pages |

If you add a new file that the site reads at build time via a relative path like `../../foo`, you must ensure it is not excluded by `.vercelignore`.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "provided path does not exist" | You ran `vercel` from `web/site/`, run from repo root instead |
| "Root Directory does not exist" | `.vercelignore` is excluding `web/site/`. Check ignore patterns, avoid `*` catch-all with negations |
| Build fails on `version.json` | `next.config.ts` generates it at build time, check `src/opentraces/__init__.py` is not in `.vercelignore` |
| Machine view empty on production | `skill/SKILL.md` excluded by `.vercelignore`, `getSkillContent()` returns `""` |
| OG image not showing | Check `metadataBase` in `layout.tsx` is set to `https://opentraces.ai` |
| Theme flash after deploy | Verify `layout.tsx` uses raw `<script>` tag, not `next/script` |
| Cloudflare API 503 on deploy | Transient, retry the deploy |
