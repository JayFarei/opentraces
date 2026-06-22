# Discoverability loop ledger

Bounded loop improving Search + Agent-engine discoverability for `web/site/`. Each cycle: pick the single highest-impact gap → one bounded change → `next build` + checklist on affected pages → keep only if it passes → record here. Stop when every priority page passes with no high-impact gaps, or a cycle makes no progress. Ask before production deploy.

Priority pages: `/` (home), `/docs/*`, `/explorer`, `/schema`, `llms.txt`.

Per-page checklist: indexable · title · description · canonical · structured data · present in `sitemap.ts` · present in `llms.txt` · answers its target question.

## Baseline (observe)

| Page | title | description | canonical | structured data | sitemap | llms.txt |
|---|---|---|---|---|---|---|
| `/` | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| `/docs/*` | ❌ inherits root | ❌ inherits root | ❌ | ❌ | ✅ | ✅ |
| `/explorer` | ❌ inherits root | ❌ inherits root | ❌ | ❌ | ✅ | ✅ |
| `/schema` | ✅ | ✅ | ❌ | ❌ | ⚠️ redirect target | ✅ |
| `llms.txt` | n/a | ✅ | n/a | n/a | n/a | self |

Strong existing base: `next.config.ts` emits `Link` headers (`service-doc`, `describedby` → llms.txt + schema, `via` → GitHub), `.well-known/agent-skills/index.json` auto-generated from `skill/SKILL.md`, `robots.txt` with `Content-Signal`, `metadataBase` set.

## Cycles

### Cycle 1 — structured data on home (`/`)
- **Gap:** no JSON-LD anywhere; AI/search engines have no machine-readable statement of what opentraces is.
- **Change:** add reusable `JsonLd` component + `@graph` (WebSite + SoftwareApplication + Organization) to `src/app/page.tsx`, grounded in existing metadata + GitHub `sameAs` + verified MIT license.
- **Result:** ✅ PASS. `next build` green; JSON-LD renders in prerendered `index.html` and parses to `@graph` `[Organization, WebSite, SoftwareApplication]`. New files: `src/components/JsonLd.tsx`, `src/lib/structured-data.ts`.

### Cycle 2 — per-doc metadata for `/docs/*`
- **Gap:** every docs page inherits the generic root title/description ("open traces - The Commons for Agent Traces") → ~35 pages with duplicate titles, no canonical, weak answer-readiness.
- **Change:** `getDocMeta(slug)` in `src/lib/docs.ts` (title from DOC_NAV + description extracted from first prose paragraph with generic fallback + canonical) wired into `generateMetadata` on `src/app/docs/[[...slug]]/page.tsx`.
- **Result:** ✅ PASS. `next build` green; spot-checked `/docs`, `/docs/getting-started/installation` (fallback path → real prose past code blocks), `/docs/workflow/context-tree` — each has a distinct title, content-derived description, and correct canonical.

### Cycle 3 — page metadata for `/explorer`
- **Gap:** `/explorer` (top-level priority page) inherits the generic homepage title/description; reads as a duplicate of `/`.
- **Change:** `export const metadata` on `src/app/explorer/page.tsx` (title + description grounded in the live HF dataset browser + canonical).
- **Result:** ✅ PASS. `next build` green; `/explorer` now has its own title, description, and canonical.

### Cycle 4 — `/schema` server component: per-version metadata + canonical + JSON-LD + SSG
- **Gap:** `/schema/[version]` is a client component → no canonical, no structured data, dynamically rendered (`ƒ`) instead of prerendered; sitemap points at the `/schema` redirect.
- **Change:** extract the version dropdown to `src/components/SchemaVersionSelect.tsx` (client); convert `src/app/schema/[version]/page.tsx` to a server component with `generateStaticParams` (all version slugs), `generateMetadata` (per-version title + `schema.summary` description + self-canonical), and a `TechArticle` `JsonLd`; point sitemap at `/schema/latest`.
- **Result:** ✅ PASS. `next build` green; `/schema/[version]` now `●` SSG (was `ƒ` dynamic) for `latest` + every version, each with per-version title, summary description, self-canonical, and parsing `TechArticle` JSON-LD.

### Cycle 5 — explicit canonical on home (`/`)
- **Gap:** `/` had no `alternates.canonical`. (Placed on the page, not the root layout, so it doesn't cascade `/` onto non-priority routes like `/hub`.)
- **Change:** `export const metadata` with `alternates.canonical` on `src/app/page.tsx` (merges with root title/description).
- **Result:** ✅ PASS. `next build` green; `/` keeps its title + JSON-LD and now emits `<link rel="canonical" href="https://opentraces.ai">`.

### Cycle 6 — structured data on `/docs/*`
- **Gap:** docs pages had no structured data (last checklist gap on the docs tree).
- **Change:** `docStructuredData()` (`TechArticle` + `BreadcrumbList`) rendered via `<JsonLd>` in `src/app/docs/[[...slug]]/page.tsx`; `getDocMeta` extended with a clean `heading`.
- **Result:** ✅ PASS. `next build` green; `/docs` → crumbs `[Documentation]`, `/docs/workflow/context-tree` → crumbs `[Documentation, Context Tree]`; both parse.

### Cycle 7 — structured data on `/explorer`
- **Gap:** `/explorer` had no structured data (last remaining checklist gap).
- **Change:** `explorerStructuredData` (`CollectionPage`) rendered via `<JsonLd>` in `src/app/explorer/page.tsx`.
- **Result:** ✅ PASS. `next build` green; `/explorer` → `CollectionPage` parses.

## Final state — STOP (all priority pages pass, no high-impact gaps)

| Page | title | description | canonical | structured data | sitemap | llms.txt |
|---|---|---|---|---|---|---|
| `/` | ✅ | ✅ | ✅ | ✅ `@graph` Org+WebSite+SoftwareApplication | ✅ | ✅ |
| `/docs/*` | ✅ per-doc | ✅ per-doc | ✅ self | ✅ TechArticle+BreadcrumbList | ✅ | ✅ |
| `/explorer` | ✅ | ✅ | ✅ | ✅ CollectionPage | ✅ | ✅ |
| `/schema/*` | ✅ per-version | ✅ summary | ✅ self | ✅ TechArticle | ✅ `/schema/latest` | ✅ |
| `llms.txt` | n/a | ✅ | n/a | n/a | n/a | ✅ |

Final verification: `next build` green, ESLint clean on all touched files, every priority page has a distinct title + description + canonical + ≥1 valid JSON-LD, `noindex=0` everywhere, `/schema/[version]` upgraded from dynamic (`ƒ`) to prerendered (`●`). All facts in structured data verified against the repo (MIT license, GitHub URL, Python, macOS/Linux, free).

**Stop reason:** every priority page passes the checklist with no high-impact gaps remaining. The loop converged.

**Not yet done (requires explicit go-ahead):** production deploy (`/deploy-site`), and the post-deploy engine spot-check (query real AI/search engines for the top questions and fold wrong/missing descriptions back in as new gaps — meaningful only against the deployed site).

**Deferred (low-impact, not blocking):** `FAQPage` JSON-LD on home, per-page OG images, a concise `llms.txt` index split from the full dump.
