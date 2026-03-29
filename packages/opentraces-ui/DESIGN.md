# @opentraces/ui Design System

Version 1.1 / 2026-03-29

---

## Product Context

opentraces is an open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Target users: ML researchers, training pipeline builders, open-source developers. Positioning: "The Commons for agent traces," open data on open infrastructure.

---

## Aesthetic Direction

Two distinct personalities across two modes:

- **Dark mode** (primary): Terminal UI aesthetic. Everything feels like a beautifully rendered TUI app.
- **Light mode**: Brutalist specification document. Concrete gray background, heavy black typography, high contrast, uppercase section labels.

Decoration level is minimal: thin rules, ASCII box-drawing characters, no decoration beyond typography and structure. No border-radius anywhere.

Mood: developer-native, confident, technical. A tool built by engineers for engineers.

---

## Implementation

All tokens, base styles, and shared components live in this package (`@opentraces/ui`).

| File | Purpose |
|------|---------|
| `tokens.css` | CSS custom properties (light/dark) + Tailwind v4 `@theme` registration |
| `base.css` | Reset, body defaults, zero-radius global override, accessibility |
| `components.css` | Shared component classes (`.ot-*` prefix) with Tailwind `@apply` |
| `index.css` | Single-import entry point for all three |
| `src/` | React component wrappers (Badge, Box, Button, CodeBlock, Table, Terminal, Typography) |
| `assets/` | Logo SVGs |

### Usage

```css
/* Import everything */
@import "@opentraces/ui/theme.css";

/* Or import individually */
@import "@opentraces/ui/tokens.css";
@import "@opentraces/ui/base.css";
@import "@opentraces/ui/components.css";
```

Tailwind utilities are registered via `@theme`, so `bg-bg`, `text-accent`, `font-mono`, `border-border` all work out of the box.

---

## Typography

### Font Stack

| Role | Family | Weights | Tailwind Utility |
|------|--------|---------|------------------|
| Display / Hero | Space Grotesk | 300-700 | `font-display` |
| Body | IBM Plex Mono | 300-700 | `font-body` |
| Code / Data / Nav / Buttons | JetBrains Mono | 100-800 | `font-mono` |
| Labels / Stats | Space Mono | 400, 700 | `font-label` |

Space Grotesk is the one non-monospace font, used only for headlines at large sizes.

IBM Plex Mono is the monospace body text. The entire product feels terminal-native.

JetBrains Mono is used for code, CLI commands, session IDs, data values, nav links, buttons, and form inputs.

Space Mono is used for stat labels, counters, and section rules.

### Mode-Specific Display Treatment

**Dark mode:** Weight 400, letter-spacing -0.03em, light and confident.

**Light mode:** Weight 700, letter-spacing -0.04em, color #000000, section titles uppercase (brutalist).

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

## Color Tokens

All colors are CSS custom properties on `:root` (light) and `[data-theme="dark"]` (dark). Registered as Tailwind utilities via `@theme`.

### Dark Mode (Primary, TUI Aesthetic)

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | #111111 | Page background |
| `--bg-alt` | #0A0A0A | Terminal body, inset areas |
| `--surface` | #191919 | Cards, panels |
| `--surface-hover` | #222222 | Interactive surface state |
| `--surface-elevated` | #1E1E1E | Elevated panels |
| `--text` | #E0E0E0 | Primary text |
| `--text-secondary` | #B0B0B0 | Supporting text |
| `--text-muted` | #666666 | De-emphasized |
| `--text-dim` | #444444 | Lowest emphasis |
| `--accent` | #F97316 | Orange, action + identity |
| `--accent-dim` | #9A3412 | Subdued accent |
| `--accent-bg` | rgba(249, 115, 22, 0.08) | Background tint |
| `--green` | #22C55E | Success, published |
| `--red` | #EF4444 | Error, secret detected |
| `--yellow` | #EAB308 | Warning, flagged |
| `--blue` | #3B82F6 | Links |
| `--cyan` | #22D3EE | Numbers, data values |
| `--border` | #2A2A2A | Default borders |
| `--border-strong` | #3A3A3A | Emphasized borders |

### Light Mode (Brutalist, Concrete)

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | #E4E2DF | Concrete gray, not white |
| `--bg-alt` | #D8D6D2 | Recessed areas |
| `--surface` | #EEEDEB | Cards, panels |
| `--surface-hover` | #E4E2DF | Interactive surface state |
| `--surface-elevated` | #F2F1EF | Elevated panels |
| `--text` | #0A0A0A | Near-black, maximum contrast |
| `--text-secondary` | #2A2A2A | Supporting text |
| `--text-muted` | #6B6B6B | De-emphasized |
| `--text-dim` | #A0A0A0 | Lowest emphasis |
| `--accent` | #C2410C | Darker orange for light bg |
| `--accent-dim` | #EA580C | Subdued accent |
| `--green` | #15803D | Success |
| `--red` | #B91C1C | Error |
| `--yellow` | #92400E | Warning |
| `--blue` | #1D4ED8 | Links |
| `--cyan` | #0E7490 | Numbers, data values |
| `--border` | #C5C3BF | Default borders |
| `--border-strong` | #9A9895 | Emphasized borders |

### Light Mode Overrides

- H1 and section titles: #000000 true black, weight 700
- Primary buttons: `--text` background, `--bg` text (inverts)
- Terminal stays light-themed with adapted syntax colors
- Section titles: uppercase + bold
- Stat values: weight 700, pure black

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

- Hero: asymmetric grid (`1fr 1.2fr`), headline left, terminal right
- Content sections: full-width grid, 1px border-separated cells
- Max content width: 1100px
- Feature grid: 3-column, 1px borders, no gaps (TUI table aesthetic)
- Border radius: zero everywhere (`base.css` enforces globally)

---

## Motion

Minimal-functional only.

- Hover transitions (color, border-color, background) at 0.1s to 0.15s
- No scroll animations, no entrance effects, no decorative motion

---

## Component Library

All components use the `.ot-` prefix. Defined in `components.css` with Tailwind `@apply`, wrapped as React components in `src/`.

### Box (`.ot-box`)
Labelled border container. Set `data-label` for the floating label.

### Buttons (`.ot-btn`)
Four variants: `.ot-btn-primary`, `.ot-btn-outline`, `.ot-btn-accent`, `.ot-btn-ghost`. Small size with `.ot-btn-sm`. Bracket notation in copy: `[start contributing]`, `[export]`.

### Terminal (`.ot-terminal`)
Square corners, 1px border, tab bar with underline-active indicator. Syntax coloring classes: `.c` (command), `.f` (flag/orange), `.s` (string/green), `.n` (number/cyan), `.w` (warning/yellow), `.ok` (success), `.er` (error), `.di` (dim).

### Data Table (`.ot-tbl-wrap`)
Monospace throughout, 1px borders, header row with uppercase labels.

### Badges (`.ot-badge`)
Five variants: `.ot-badge-ok`, `.ot-badge-er`, `.ot-badge-wa`, `.ot-badge-ac`, `.ot-badge-bl`. 1px border + subtle background tint.

### Code Block (`.ot-code-wrap`)
With copy button (`.ot-code-copy`).

### Schema (`.ot-schema`)
Syntax-highlighted schema display: `.ot-schema-key`, `.ot-schema-type`, `.ot-schema-str`, `.ot-schema-comment`.

---

## Logo

### Glyph
Interlocking knot: two thick rounded ribbon loops crossing diagonally, one adaptive (dark/light) and one orange.

### Concept
Two traces interlocking, data flowing through each other. The weave represents open exchange, contribution, and interoperability.

### Color
- **Strand 1**: Uses `currentColor`. Dark (#1A1A1A) on light backgrounds, light (#E0E0E0) on dark.
- **Strand 2**: Always accent orange (#F97316 dark, #C2410C light).

### Wordmark
Set in JetBrains Mono 400. The dot in ".ai" is always accent color.

### Files
- `assets/logo.svg` (vector, traced from original design)
- `assets/logo-orange-strand.svg` (individual strand for construction reference)

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-27 | TUI/terminal-native aesthetic | Developer-centric product, stands out from generic SaaS tools |
| 2026-03-27 | Zero border-radius globally | Squares and sharp corners, like ASCII diagrams |
| 2026-03-27 | Dark mode primary, brutalist light | Dark = TUI app. Light = concrete specification document |
| 2026-03-27 | Monospace-dominant typography | IBM Plex Mono body, JetBrains Mono code, Space Mono labels |
| 2026-03-27 | Burnt orange accent (#F97316) | No competitor uses orange. Warmth, contribution, campfire |
| 2026-03-27 | Concrete gray light bg (#E4E2DF) | Not white, not cream. Brutalist concrete |
| 2026-03-27 | Interlocking knot logo | Two woven trace ribbons, dark + orange |
| 2026-03-27 | Bracket button notation | `[start contributing]`, `[export]` |
| 2026-03-27 | ASCII section dividers | `FEATURES ────────────` instead of generic rules |
| 2026-03-29 | Consolidate into @opentraces/ui | Single package for tokens, base, components, assets, design spec |
| 2026-03-29 | `.ot-` component prefix | Namespaced to avoid collisions, works alongside Tailwind utilities |
| 2026-03-29 | Light mode as SSR default | `:root` = light, `[data-theme="dark"]` = dark, avoids flash |
