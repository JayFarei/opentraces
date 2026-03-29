---
schema_version: "1.0"
title: Flask-SPA Bridge for Local Tool UIs
scope: review interfaces
pattern_type: structural
transferable: true
---

# Flask-SPA Bridge for Local Tool UIs

## Overview

The review interface uses a Python Flask backend that serves both a REST API and a pre-built React SPA from disk. This allows a CLI tool to provide a rich browser-based UI without requiring users to run a separate frontend dev server, install Node.js, or manage a build step. The viewer is built once (during development or CI) and bundled as static assets that Flask serves directly.

## How It Works

1. **Flask factory** (`create_app`) accepts `staging_dir`, `state_path`, and `viewer_dist` parameters
2. When `viewer_dist` exists (directory with built React app), Flask serves it as the SPA:
   - `GET /` serves `index.html`
   - `GET /assets/*` serves static JS/CSS bundles
   - All other non-API routes fall through to `index.html` (SPA client-side routing)
3. When `viewer_dist` does not exist, Flask falls back to Jinja2 templates (simpler HTML)
4. **REST API** routes (`/api/*`) provide data for both the SPA and template modes
5. **Vite dev proxy**: During development, `vite.config.ts` proxies `/api/*` to Flask, so the React dev server and Flask backend run simultaneously with hot reload
6. Three review interfaces (CLI, TUI, Web) all operate on the same staging directory and StateManager, so decisions persist across interface switches

## Key Files

- `src/opentraces/clients/web/app.py` - Flask factory with SPA serving and REST API
- `web/viewer/src/App.tsx` - React SPA entry point
- `web/viewer/src/lib/api.ts` - API client (base URL is empty string, works with both Vite proxy and Flask serving)
- `web/viewer/vite.config.ts` - Dev proxy configuration
- `src/opentraces/cli.py` - `review --web` command that starts Flask

## How to Replicate

1. Build your frontend as a standard SPA (React/Vue/Svelte) with Vite or similar
2. Create a Flask app factory that accepts a `dist_dir` parameter
3. Serve the SPA's `index.html` for all non-API routes (catch-all for client-side routing)
4. Serve the SPA's assets from the `assets/` subdirectory
5. Define REST API routes under `/api/` prefix
6. In development, configure Vite to proxy `/api/*` to the Flask dev server
7. In production (CLI usage), Flask serves the pre-built SPA directly
8. Use empty string as the API base URL in the frontend, so it works in both dev and production modes

## When to Use

- CLI tools that need a rich browser-based UI for review, visualization, or configuration
- When you want to ship a single Python package that includes a web UI (no Node.js required at runtime)
- Local-only tools where the Flask server is accessed from localhost only
- When you need fallback functionality (template mode) if the SPA build is not available

## When NOT to Use

- Production web services that need dedicated frontend deployment
- When the UI is simple enough for a TUI or CLI interface
- When Node.js is already a runtime dependency
- When real-time updates are needed (Flask's sync nature may be limiting; consider WebSocket alternatives)
