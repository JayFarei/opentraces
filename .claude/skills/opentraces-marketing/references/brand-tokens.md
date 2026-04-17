# opentraces brand tokens

Source of truth: `web/viewer/src/tokens.ts`. Keep these in sync if the viewer's palette evolves.

## Wordmark construction

```
open    →  #B0B0B0, regular weight
traces  →  #E0E0E0, bold weight
font    →  JetBrains Mono
size    →  38pt
position →  (72, 90) from top-left, on any 1800×1200 canvas
```

This treatment comes from `NavBar.tsx:19-21` and is the one true brand mark. Do not re-space, re-color, or re-weight it.

## Dark palette (default for marketing)

| Token | Hex | Role |
|---|---|---|
| `bg` | `#111111` | Primary page background |
| `bgAlt` | `#0A0A0A` | Deepest dark, "void" preset |
| `surface` | `#191919` | Card surfaces |
| `surfaceElevated` | `#1E1E1E` | Raised surfaces |
| `text` | `#E0E0E0` | Primary text, "traces" |
| `textSec` | `#B0B0B0` | Secondary text, "open", help descriptions |
| `textMuted` | `#666666` | Muted labels, footers |
| `textDim` | `#444444` | Almost-invisible hints |
| `accent` | `#F97316` | Accent orange — command caption, CTAs |
| `accentBg` | `rgba(249,115,22,0.08)` | Accent tint backgrounds |
| `cyan` | `#22D3EE` | Trace palette |
| `green` | `#22C55E` | Success, trace palette |
| `blue` | `#3B82F6` | Trace palette |
| `yellow` | `#EAB308` | Warning, trace palette |
| `red` | `#EF4444` | Error, trace palette |
| `border` | `#2A2A2A` | Subtle borders |
| `borderStrong` | `#3A3A3A` | Emphasized borders |
| `pageBg` | `#1A1A1A` | Page background in viewer |
| `panelBg` | `#0F0F0F` | Panel background, marketing default gradient top |

## Light palette (rare, for variety)

Used sparingly — most opentraces marketing is dark.

| Token | Hex | Role |
|---|---|---|
| `bg` | `#E4E2DF` | Warm off-white |
| `bgAlt` | `#D8D6D2` | Deeper cream |
| `surface` | `#EEEDEB` | Card surfaces |
| `text` | `#0A0A0A` | Primary text |
| `textSec` | `#2A2A2A` | Secondary text |
| `textMuted` | `#6B6B6B` | Muted labels |
| `accent` | `#C2410C` | Accent orange (darker for contrast) |

## Trace palettes

For when you need multiple data colors in a single shot (rare for marketing — usually one card per image).

**Dark:** `["#F97316", "#22D3EE", "#3B82F6", "#EAB308", "#22C55E", "#A78BFA", "#F43F5E"]`

**Light:** `["#C2410C", "#0E7490", "#1D4ED8", "#92400E", "#15803D", "#6D28D9", "#BE123C"]`

## Fonts

| Role | Stack | Use |
|---|---|---|
| `code` | `'JetBrains Mono', monospace` | Terminal output, wordmark, all captions |
| `body` | `'IBM Plex Mono', monospace` | Longer-form text (not currently used in shots) |
| `display` | `'Space Grotesk', sans-serif` | Display / non-code headings |
| `label` | `'Space Mono', monospace` | Small labels |

For marketing screenshots, **JetBrains Mono is the only font used** — it's what gives the shots their cohesive, terminal-native feel. Resist the urge to mix in a sans-serif "marketing" font. The wordmark being mono is the whole point.

## Wallpaper color rules

Wallpapers should feel like part of the same dark ecosystem. Practical constraints:

1. Upper-left quadrant must stay dark enough to keep the wordmark (`#B0B0B0` + `#E0E0E0`) readable. `#1A1A1A` or darker in that zone is safe.
2. Lower-left quadrant hosts the orange command caption — avoid other oranges/reds there that would clash.
3. The center will be covered by the terminal card, so that zone can be anything.
4. Use blur (`-blur 0x40` or higher) to prevent sharp wallpaper edges competing with the card's crisp geometry.

When generating a new gradient, start from `#0F0F0F` or `#0A0A0A` as the darker endpoint and shift the lighter endpoint toward one of the trace-palette colors, muted to ~15% saturation so it reads as "tint on dark" not "Skittles".
