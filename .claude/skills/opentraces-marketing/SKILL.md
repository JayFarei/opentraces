---
name: opentraces-marketing
description: Generate branded marketing screenshots of opentraces CLI commands — wordmark + CLI card + caption + wallpaper, filename-stamped and auto-copied to the clipboard. Use this skill whenever the user wants to create a shareable image, social post, promo graphic, or marketing material featuring an `ot` / `otd` / `opentraces` command, or says "ot-shot", "marketing shot", "take a screenshot of this command", "make this tweetable", "social card for X", "promo graphic", or "dress this up for sharing". Also use proactively after running an interesting opentraces command the user might want to share externally.
---

# opentraces-marketing

Produce brand-consistent, shareable screenshots of opentraces CLI output. The skill wraps a single entrypoint — the `ot-shot` script — that renders real command output via `freeze`, composites it onto a branded canvas (wordmark + tagline + help description + wallpaper), auto-names the file with a timestamp, and copies the result to the macOS clipboard.

The point is **consistent branding with controlled diversity**: the wordmark, font, and text layout never change. What varies is the wallpaper, so a feed of marketing shots feels like a family rather than a single repeated poster.

## When to use this skill

Trigger it whenever the user wants to share opentraces CLI output externally — tweets, LinkedIn posts, blog hero images, launch announcements, Slack brag posts, conference slides. If the user runs an opentraces command and says anything like "let's share this", "make this look nice", or "screenshot this for the blog", reach for this skill.

Do NOT use it for:
- Debugging screenshots (just paste the raw text)
- Internal-only Loom-style captures (termshot is fine)
- Non-opentraces commands — the skill assumes the opentraces CLI for help-text extraction

## The one-liner you almost always want

```bash
ot-shot "<subcommand and args>"
```

That runs the command, renders the card, wraps it with branding and the default `purple-dusk` wallpaper treatment, writes to `~/Desktop/YYYY-MM-DD-HH-MM-<slug>.png`, and copies to the clipboard. Ready to paste into Slack, Mail, X, etc.

## Full usage

```
ot-shot "<subcommand and args>"  [output.png]
```

Positional args:
- `$1` — the opentraces subcommand and its flags, as a single quoted string
- `$2` — optional output path (when omitted, auto-named under `~/Desktop/`)

Env-var knobs (all optional — overrides stack in a single invocation):

| Var | Default | What it does |
|---|---|---|
| `WALLPAPER` | _(unset)_ | Named preset from `assets/wallpapers/` (`obsidian`, `purple-dusk`, `ember`, `ocean`, `meadow`, `void`, `mesh`, `aurora`) or an absolute path to any image file. When unset, falls back to the gradient defined by `GRADIENT_FROM` / `GRADIENT_TO`. |
| `TAGLINE` | `share agent traces to open datasets` | The muted sub-brand line under the wordmark. Customize per-campaign (launch tagline, stat, quote). |
| `OT_BIN` | `$HOME/src/tries/2026-03-27-community-traces-hf/.venv/bin/opentraces` | Path to the opentraces binary to drive. |
| `GRADIENT_FROM` / `GRADIENT_TO` | `#0F0F0F` / `#1A1033` | Gradient endpoints when no wallpaper is set. Use tokens from `references/brand-tokens.md`. |
| `CANVAS_W` / `CANVAS_H` | `1800` / `1200` | Canvas size in px. 3:2 is Twitter/LinkedIn friendly; use `1200x630` for the OG/Twitter card sweet spot. |
| `CARD_WIDTH` | `1280` | Terminal window width in px passed to freeze. Bump this when capturing very wide output. |
| `FONT_SIZE` | `16` | Monospace font size inside the card. Drop to `14` for denser captures. |
| `LINE_HEIGHT` | `1.3` | Line-height multiplier. |
| `BRAND_FONT` / `BRAND_FONT_BOLD` | `JetBrainsMono-NF-Regular` / `JetBrainsMono-NF-Bold` | ImageMagick font names for the wordmark. |

Examples:

```bash
# Default — purple-dusk gradient, safe everyday choice
ot-shot "graph --limit 8"

# Named wallpaper
WALLPAPER=ember ot-shot "doctor"
WALLPAPER=ocean ot-shot "push --dry-run"

# Per-campaign tagline
TAGLINE="now live on HuggingFace Hub" WALLPAPER=aurora ot-shot "version"

# Twitter/OG card aspect
CANVAS_W=1200 CANVAS_H=630 WALLPAPER=mesh ot-shot "graph --limit 5"

# Custom wallpaper (any image — will be cover-fit to canvas)
WALLPAPER=~/Pictures/launch-day.jpg ot-shot "push --dry-run"
```

## Anatomy of a shot

Every shot follows the same six-element template — change what varies, never what anchors:

```
┌───────────────────────────────────────────────────┐
│  opentraces                                       │  ← wordmark (never changes)
│  <tagline>                                        │  ← muted, per-campaign
│                                                   │
│              ┌───────────────────────┐            │
│              │  🟢 🟡 🔴   [output]    │            │  ← freeze card, transparent bg,
│              │                       │            │     traffic-light chrome
│              └───────────────────────┘            │
│                                                   │
│  ot <subcommand> <args>                           │  ← orange accent caption
│  <auto-extracted help description>                │  ← muted, authoritative
└───────────────────────────────────────────────────┘
  └─ wallpaper layer (gradient or image) ─┘
```

The help description is pulled from `opentraces <subcmd> --help` automatically — you don't hand-write it. This keeps captions accurate as the CLI evolves.

## Wallpaper catalog

Bundled presets in `assets/wallpapers/`. All are 1800×1200, brand-cohesive (dark base, subtle color tints drawn from the viewer palette).

| Name | Mood | Best for |
|---|---|---|
| `obsidian` | Minimal dark gradient (`#0F0F0F → #1A1A1A`) | Serious/technical posts, release notes |
| `purple-dusk` | Default marketing (`#0F0F0F → #1A1033`) | General promotion, safe everyday choice |
| `ember` | Dark with orange glow (`#0F0F0F → #2A1410`) | Launch, milestone, "it's live" moments |
| `ocean` | Dark with teal/cyan (`#0F0F0F → #0A2028`) | Data/stats posts, analytics screenshots |
| `meadow` | Dark with subtle green (`#0F0F0F → #0A2010`) | Fresh features, success stories |
| `void` | Near-black with film grain | Minimalist editorial vibe |
| `mesh` | Radial multi-color mesh | Abstract/artistic, "different from the feed" |
| `aurora` | Diagonal dark→purple→teal gradient | Flagship announcements, hero images |

**Rotating through them intentionally** is how you get feed diversity without losing brand. A good rhythm for a campaign week:
- Monday (headline announcement): `aurora`
- Tuesday (feature deep-dive): `ocean`
- Wednesday (stat / quote): `void`
- Thursday (community/user post): `meadow`
- Friday (celebration): `ember`

## Adding a new wallpaper

Drop any image into `assets/wallpapers/` (PNG or JPG, any dimensions — `ot-shot` cover-fits it to the canvas). Match the vibe: the wordmark is light on dark, so keep the wallpaper's upper-left region reasonably dark. Test with `WALLPAPER=<name> ot-shot "version"` and check that the wordmark stays readable.

For generated gradients, keep them cohesive with the viewer palette in `references/brand-tokens.md`. Example for a new "rose" preset:

```bash
magick -size 1800x1200 \
  gradient:"#0F0F0F-#2A1020" \
  ~/.claude/skills/opentraces-marketing/assets/wallpapers/rose.png
```

## Brand invariants

These never change — they're what make shots recognizable:

- **Wordmark**: `open` in `#B0B0B0` (muted) + `traces` in `#E0E0E0` (primary, bold), JetBrains Mono, 38pt, top-left at `(72, 90)`. Mirrors `web/viewer/src/components/NavBar.tsx:19-21` exactly.
- **Accent color**: `#F97316` for the command caption at bottom. Matches the viewer's accent.
- **Footer layout**: `ot <command>` on top (orange), `<description>` below (muted gray). Two lines, nothing else.
- **Card chrome**: traffic-light macOS window, 12px border radius, transparent background, centered.

If you find yourself wanting to break these, resist. Diversity comes from wallpaper and tagline, never from moving the wordmark.

## Outputs

Default filename: `~/Desktop/YYYY-MM-DD-HH-MM-<slugified-command>.png`. The slug is the command args lowercased, non-alphanumerics collapsed to dashes — e.g., `graph --limit 8` → `graph-limit-8`.

Every shot is also copied to the macOS clipboard as a PNG (`«class PNGf»`), so you can paste directly into Slack, Mail, Keynote, X, LinkedIn, etc. without touching the file.

## Dependencies & installation

The skill needs three binaries on PATH:
- `freeze` — `brew install charmbracelet/tap/freeze`
- `magick` (ImageMagick 7+) — `brew install imagemagick`
- `opentraces` — installed in a Python venv; point `OT_BIN` at it

The script is installed as a symlink: `~/.local/bin/ot-shot → ~/.claude/skills/opentraces-marketing/scripts/ot-shot`. If the symlink is missing, recreate it with:

```bash
ln -sf ~/.claude/skills/opentraces-marketing/scripts/ot-shot ~/.local/bin/ot-shot
```

## Reference

- `references/brand-tokens.md` — full color + font palette cribbed from the viewer's `tokens.ts`
- `scripts/ot-shot` — the canonical script
- `assets/wallpapers/` — preset backgrounds
