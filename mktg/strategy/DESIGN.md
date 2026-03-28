# opentraces.ai Design System

Version 1.0 / 2026-03-27

---

## Product Context

opentraces.ai is an open-source CLI tool for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. The target users are ML researchers, training pipeline builders, and open-source developers. The positioning is "The Commons for agent traces," open data on open infrastructure.

Competitors: traces.com (proprietary SaaS), DataClaw (open-source protest art), Langfuse (observability). Key differentiating feature: a contributor analytics dashboard ("Spotify Wrapped for coding agents").

---

## Aesthetic Direction

Two distinct personalities across two modes:

- **Dark mode**: Terminal UI aesthetic. Everything feels like a beautifully rendered TUI app.
- **Light mode**: Brutalist specification document. Concrete gray background, heavy black typography, high contrast, uppercase section labels.

Decoration level is minimal: thin rules, ASCII box-drawing characters, no decoration beyond typography and structure.

Mood: developer-native, confident, technical. Like a tool built by engineers for engineers.

---

## Typography

### Font Stack

| Role | Family | Weights | Source |
|------|--------|---------|--------|
| Display / Hero | Space Grotesk | 300, 400, 500, 600, 700 | Google Fonts CDN |
| Body | IBM Plex Mono | 300, 400, 500, 600 | Google Fonts CDN |
| Code / Data | JetBrains Mono | 400, 500, 600 | Google Fonts CDN |
| Labels / Stats | Space Mono | 400, 700 | Google Fonts CDN |

Space Grotesk is the ONE non-monospace font. It is used only for headlines at large sizes. Geometric with personality.

IBM Plex Mono is the monospace body text. The entire product feels terminal-native.

JetBrains Mono is the universal standard for code. Used for CLI commands, session IDs, data values, nav links, buttons, and form inputs.

Space Mono is used for stat labels, counters, and section rules. Pairs with Space Grotesk.

### Mode-Specific Display Treatment

**Dark mode:**
- Weight: 400
- Letter-spacing: -0.03em
- Style: light and confident (midday.ai reference)

**Light mode:**
- Weight: 700
- Letter-spacing: -0.04em
- Color: #000000
- Section titles: uppercase (brutalist)

### Type Scale

| Token | Size | Family |
|-------|------|--------|
| H1 | clamp(32px, 4.5vw, 52px) | Space Grotesk |
| H2 (section-title) | 28px | Space Grotesk |
| H3 | 18px | Space Grotesk |
| Body | 13px | IBM Plex Mono |
| Small | 12px | IBM Plex Mono |
| Code | 12px | JetBrains Mono |
| Label | 10px, uppercase, letter-spacing 0.1em | Space Mono |
| Nav | 12px | JetBrains Mono |

---

## Color

### Dark Mode (Primary, TUI Aesthetic)

| Token | Value | Usage |
|-------|-------|-------|
| Background | #111111 | Page background, readable dark, not pure black |
| Background Alt | #0A0A0A | Terminal body, inset areas |
| Surface | #191919 | Cards, panels |
| Surface Hover | #222222 | Interactive surface state |
| Text Primary | #E0E0E0 | High contrast without harsh white |
| Text Secondary | #B0B0B0 | Supporting text |
| Text Muted | #666666 | De-emphasized text |
| Text Dim | #444444 | Lowest emphasis |
| Accent | #F97316 | Orange-500, action + identity |
| Accent Dim | #9A3412 | Subdued accent |
| Accent Soft | rgba(249, 115, 22, 0.08) | Background tint |
| Green | #22C55E | Success, published |
| Red | #EF4444 | Error, secret detected |
| Yellow | #EAB308 | Warning, flagged |
| Blue | #3B82F6 | Links |
| Cyan | #22D3EE | Numbers, data values in terminal |
| Border | #2A2A2A | Default borders |
| Border Strong | #3A3A3A | Emphasized borders |

### Light Mode (Brutalist, Concrete Specification Document)

| Token | Value | Usage |
|-------|-------|-------|
| Background | #E4E2DF | Concrete gray, NOT white |
| Background Alt | #D8D6D2 | Recessed areas |
| Surface | #EEEDEB | Cards, panels, slightly lighter than bg |
| Surface Hover | #E4E2DF | Interactive surface state |
| Text Primary | #0A0A0A | Near-black, maximum contrast |
| Text Secondary | #2A2A2A | Supporting text |
| Text Muted | #6B6B6B | De-emphasized text |
| Text Dim | #A0A0A0 | Lowest emphasis |
| Accent | #C2410C | Darker orange for light bg contrast |
| Green | #15803D | Success |
| Red | #B91C1C | Error |
| Yellow | #92400E | Warning |
| Blue | #1D4ED8 | Links |
| Border | #C5C3BF | Default borders |
| Border Strong | #9A9895 | Emphasized borders |

### Light Mode Overrides

- H1 and section titles: #000000 true black, weight 700
- Primary buttons: #000000 background, #E4E2DF text
- Terminal component stays light-themed with adapted syntax colors (darker greens #15803D, deeper oranges #C2410C, etc.)
- Section titles go uppercase + bold
- Feature/use-case card titles: weight 600, pure black
- Stat values: weight 700, pure black
- Section rules use border-strong color with black labels

---

## Spacing

Base unit: 4px. Density: comfortable.

| Token | Value |
|-------|-------|
| 2xs | 2px |
| xs | 4px |
| sm | 8px |
| md | 16px |
| lg | 24px |
| xl | 32px |
| 2xl | 48px |
| 3xl | 64px |

---

## Layout

- Approach: hybrid (asymmetric poster hero, grid-disciplined content)
- Hero: asymmetric grid (`1fr 1.2fr`), headline left, terminal right
- Content sections: full-width grid, 1px border-separated cells
- Max content width: 1100px
- Feature grid: 3-column, 1px borders, no gaps (TUI table aesthetic)

### Border Radius

Zero everywhere. Global override:

```css
*, *::before, *::after {
  border-radius: 0 !important;
}
```

---

## Motion

Approach: minimal-functional.

- Only hover transitions (color, border-color, background) at 0.1s to 0.15s
- No scroll animations
- No entrance effects
- No decorative motion

The product runs in a terminal. Terminals don't animate.

---

## Key Design Patterns

### ASCII / TUI Elements

Section headers use ASCII rule style:

```
FEATURES ────────────────────────────
```

Terminal tree output uses `├─`, `└─`, `↳` characters.

Buttons use bracket notation: `[start contributing]`, `[export]`, `[sign in]`.

Alerts use left-border-only (2px), like terminal log levels.

Bar charts use flat track-and-fill, not rounded bars.

Infrastructure diagrams use box-drawing with `│` connectors.

### Version Pill

- Display: inline-block
- Font: monospace, 11px
- Border: 1px solid
- Padding: 5px 14px
- Light mode: bg #D8D6D2, border #9A9895, text #4A4A4A (high contrast on concrete)

### Terminal Component

- Square corners, 1px border
- Tab bar with underline-active indicator (accent color)
- Syntax coloring:
  - Green: strings, success
  - Orange: flags
  - Cyan: numbers
  - Yellow: warnings
- In light mode: light-themed (surface bg, dark text, adapted syntax colors)

### Data Table

- Monospace font throughout
- 1px borders, no rounded corners
- Header row: 10px uppercase labels, background surface
- Badges: 1px border + subtle background tint, no border-radius

### Stat Cards

- 1px border grid (no gaps)
- Label: 10px mono uppercase
- Value: Space Grotesk 300 weight, 32px (light weight at large size = premium feel)
- Delta: 10px mono, green for up, red for down

---

## Logo

### Glyph

Interlocking knot, two thick rounded ribbon loops crossing diagonally, one dark/white (adaptive to theme) and one orange.

### Concept

Two traces interlocking, data flowing through each other. The weave represents open exchange, contribution, and interoperability.

### Construction

- Orange strand (/ diagonal) wraps top-right to bottom-left
- Dark strand (\ diagonal) wraps top-left to bottom-right
- They weave over/under in alternating pattern

### Color

- **Strand 1 (dark/light)**: Uses `currentColor`. Dark (#1A1A1A) on light backgrounds, light (#E0E0E0) on dark backgrounds. Represents the developer's code/work.
- **Strand 2 (orange)**: Always accent orange (#F97316 dark, #C2410C light). Represents the agent trace.

### Wordmark

Set in JetBrains Mono 400. The dot in ".ai" is always accent color.

### Files

- `mktg/logo.svg` (vector, traced from original design via potrace)
- `mktg/logo-orange-strand.svg` (individual strand for construction reference)

### Sizing and Spacing

- Min size: 16px for glyph-only, 14px font for wordmark
- Clear space: 1x glyph width on all sides

### Note

For production, the logo SVG should be the canonical export from the design tool. The current `mktg/logo.svg` was auto-traced from a raster export.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-27 | TUI/terminal-native aesthetic | Developer-centric product should feel like it runs in a terminal. Stand out from generic SaaS tools. |
| 2026-03-27 | Zero border-radius globally | Squares and sharp corners, like ASCII diagrams. No rounded corners anywhere. |
| 2026-03-27 | Dark mode primary, brutalist light | Dark = TUI app. Light = concrete specification document with heavy black type. Two distinct personalities. |
| 2026-03-27 | Monospace-dominant typography | IBM Plex Mono body, JetBrains Mono code, Space Mono labels. Space Grotesk only for display headlines. |
| 2026-03-27 | Burnt orange accent (#F97316) | No competitor uses orange. Signals warmth, contribution, campfire. Distinctive in screenshots. |
| 2026-03-27 | Concrete gray light bg (#E4E2DF) | Not white, not cream. Brutalist concrete. Every screenshot immediately recognizable. |
| 2026-03-27 | Interlocking knot logo | Two woven trace ribbons. Dark + orange. Represents open data exchange between developers and agents. |
| 2026-03-27 | Bracket button notation | `[start contributing]`, `[export]`. Terminal-native interaction patterns. |
| 2026-03-27 | ASCII section dividers | `FEATURES ────────────` instead of generic horizontal rules. |
| 2026-03-27 | midday.ai as layout reference | Asymmetric hero, feature grid with 1px borders, infrastructure diagram, watermark CTA. |
