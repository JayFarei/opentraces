# Keeping the marketing-site Hub in sync with the Claude design

The OpenTraces Hub shown on the marketing site (the landing-page teaser and the
`/hub` feature breakdown) is **the real exported Claude design artifact**, embedded
in same-origin iframes. The site does not reimplement the design; it boots slices
of it. The Claude design is therefore the single source of truth.

- **Design project:** `OpenTraces Hub` on claude.ai/design (project id
  `019dd8a2-8252-7bf5-9bdd-83deba8fbe4e`, entry file `OpenTraces Hub.html`).
- **Where it lives on the site:** `public/hub-preview/` (entry `index.html`).
- **How the site renders it:**
  - Landing teaser → `src/components/HubWindow.tsx` iframes the full-chrome app.
  - `/hub` breakdown → `src/lib/hub-features.ts` + `src/components/HubFeatureFrame.tsx`
    iframe `index.html?embed=1&view=…` once per feature card (chromeless).

## The seam (why a re-import needs one step, not zero)

A raw design export renders standalone but lacks three things the site needs.
These are the **embed seam**, all marked `@ot-embed-seam` in the files:

1. **Chromeless embed mode + URL deep-linking** — `?embed=1&view=repo&child=traces…`
   boots one feature panel with the sidebar/topbar suppressed. (`index.html` +
   `_embed.css`)
2. **Per-field initial nav state** — the App boots into the requested view instead
   of always the landing page.
3. **Parent-frame theme bridge** — the one genuinely web-only piece: inside the
   site's full-chrome iframe the Hub's own light/dark toggle is pushed up to the
   site chrome. Guarded so it is a no-op standalone or in any non-iframe host.

`_embed.css` is site-owned and never overwritten by a design pull. Everything else
is re-applied to the freshly imported `index.html` by `scripts/sync-hub.mjs`.

> The seam is structured to lift straight into the design source later (it is inert
> standalone). If/when it is baked into `OpenTraces Hub.html` in the design,
> `sync-hub.mjs apply` simply becomes a verified no-op.

## The proactive sync job (run when the design changes)

### 1. Pull the runtime files (agent + claude_design MCP)

The claude_design MCP (`DesignSync`) is agent-driven, not a CLI. Have the agent:

- `DesignSync list_files` for the project, then `get_file` each **runtime** file
  and write it into `public/hub-preview/`, overwriting in place.
- **Allowlist:** top-level `*.jsx` / `*.css` / `*.json`, the entry
  `OpenTraces Hub.html` (written as `index.html`), plus `data/*.{json,html,svg}`
  and `assets/*.svg`.
- **Exclude:** `screenshots/`, `uploads/`, `src/`, `docs/`, `.thumbnail`,
  `debug-*.png`, and any binary `*.png` (the deployed posters/assets already exist).
- Known cap: `get_file` is limited to 256 KiB. `data/traces.json` exceeds it; it is
  not loaded by the embed views (the app loads `trace-slim.json` + `data/meta.json`),
  so it is skipped. If a future view needs it, transfer it out of band.

### 2. Re-apply the seam

```sh
node scripts/sync-hub.mjs apply
```

Idempotent. Re-applies the embed seam and swaps the exported React **development**
builds for **production** (faster, matches how the site has always shipped). It is
**strict**: if the design's App structure moved an anchor, it throws naming the
anchor — that is the drift alarm; re-review the seam against the new structure
rather than shipping a broken embed.

### 3. Verify the view contract

```sh
node scripts/sync-hub.mjs check
```

Also guards file types: a `*.css` that actually holds JS/JSX, or a `*.css`
byte-identical to its `*.jsx` sibling (a mis-mapped pull — this once blanked the
run-intelligence chip styles), fails the check before deploy. Re-pull the named
file if it fires.

Fails if `src/lib/hub-features.ts` references a `view:` the design no longer handles.
For a deeper visual check, boot each embed URL in a browser:
`/hub-preview/index.html?embed=1&view=<id>&…` (the set lives in `hub-features.ts`).

### 4. Re-shoot the posters (only if visuals moved)

The landing teaser shows `public/hub-poster.png` (dark) / `public/hub-poster-light.png`
(light) while the live iframe boots and as the mobile fallback. If the design's
overview view changed visually, re-capture both at the Hub's logical canvas
(1280×800) and overwrite those two files.

## Performance notes

- The export ships in-browser Babel + React over a CDN. `sync-hub.mjs` already
  swaps React to production. A larger future win (not yet done) is to **transpile
  the JSX to plain JS at sync time and self-host React**, so visitors never download
  Babel or compile in-browser. The `/hub` page already mitigates with windowed,
  lazy, boot-gated iframes (`HubFeatureFrame.tsx`).
