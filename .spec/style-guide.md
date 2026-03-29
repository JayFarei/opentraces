---
schema_version: "1.0"
title: Style Guide
scope: ui
---

# Style Guide

## Design Philosophy

The visual language is "TUI aesthetic": a terminal-inspired, monospace-heavy, zero-border-radius design system with collapsing borders and minimal decoration. The system uses warm neutrals (light mode) and near-black backgrounds (dark mode) with orange as the primary accent color. No box-shadows are used anywhere; depth is conveyed entirely through border weight and background shade.

## Token Architecture

Three CSS entry points share one conceptual token set:

1. **`packages/opentraces-ui/tokens.css`** - Canonical shared package, imported by the viewer via `@import "@opentraces/ui/tokens.css"`. Defines `--bg`, `--text`, `--accent`, etc. with light-mode defaults and `[data-theme="dark"]` overrides.
2. **`web/site/src/app/globals.css`** - Duplicates the same tokens inline (not imported from the package), adds marketing-specific component styles (hero, terminal, nav, buttons). Also supports `.theme-dark` / `.theme-light` class selectors.
3. **`src/opentraces/clients/web/static/style.css`** - Standalone Flask review UI. Uses `--ot-*` prefixed variables (e.g. `--ot-bg`, `--ot-accent`). Dark-only, no light mode. Adds `--ot-subagent-border: #818cf8` and extra font stacks (`--font-display`, `--font-body`, `--font-code`, `--font-label`).

## Component Patterns

### Viewer (React + Tailwind v4)

- **No utility merge**: Components do not use `cn()`, `clsx()`, or `class-variance-authority`. Class strings are built inline with template literals and ternary expressions.
- **CSS variable access in Tailwind**: Arbitrary value syntax is used pervasively: `text-[var(--text-muted)]`, `bg-[var(--surface)]`, `border-[var(--border)]`. No Tailwind theme extensions.
- **Font references**: `font-[family-name:var(--font-mono)]` (Tailwind arbitrary font-family syntax).
- **Props typing**: Standard TypeScript interfaces. No variant/slot pattern. Components are simple functional components with destructured props.
- **Composition**: Component tree grouped by concern: `trace/`, `detail/`, `layout/`, `sessions/`, `review/`, `icons/`. No compound component or render-prop patterns.
- **State management**: Two React contexts (`SelectionContext`, `ViewPreferencesContext`). No Redux or Zustand. `@tanstack/react-query` for server state. `@tanstack/react-virtual` for list virtualization.

### Layout Pattern (AppLayout)

The viewer is a fixed desktop layout with three resizable panels:
- **Session sidebar** (left, default 240px, range 180-400px)
- **Trace view** (center, flex)
- **Detail panel** (bottom, default 220px, min 80px)

Resize handles are 5px dividers (`cursor-col-resize` / `cursor-row-resize`) that highlight with `--accent` on hover. Dragging uses raw mouse event listeners (no library).

### Button Pattern

Viewer buttons use inline bracket notation `[label]` and are styled as ghost buttons: transparent background, colored border and text, with hover background using the color's `-bg` variant. The Flask review UI uses the same approach with BEM-like class names (`ot-btn-approve`, `ot-btn-reject`).

### Badge Pattern (Flask Review)

Badges (`ot-badge-*`) use colored borders with matching translucent backgrounds at 6-8% opacity. Status badges: pending/staged (yellow), approved (green), rejected (red), flagged (accent/orange), subagent (indigo #818cf8), redacted (yellow).

### Section Header Pattern

Both viewer and Flask use the same label convention: 10px, uppercase, wider letter-spacing, `--font-label` or `--font-mono`, muted color. In the viewer this is a `SectionHeader` component; in Flask it is the `.ot-section-title` class.

## Animation Conventions

### Icon Animations (motion/react)

All 15 custom icons use the `itshover` pattern:
- `forwardRef` + `useImperativeHandle` exposing `{ startAnimation, stopAnimation }`
- `useAnimate` hook from `motion/react` for imperative control
- Trigger: `onMouseEnter` starts, `onMouseLeave` stops
- Common effects: opacity pulse, pathLength draw-in, scale pulse on terminals
- Timing: 0.3s-2s durations, `easeInOut` / `easeOut` easing, staggered delays (0.15s increments)

### CSS Transitions

The codebase uses a single, consistent transition pattern:
- `transition-colors duration-100` (Tailwind classes) on nearly every interactive element
- Exception: `transition-all duration-200` on the ContextFlow column widths
- No CSS `@keyframes` or `animate-*` Tailwind utilities are used
- No page transitions or route animations

### Motion Design Principles

1. Transitions are fast (100ms) and limited to color changes
2. Structural animations (icon SVGs) use `motion` for complex orchestration
3. No spring physics, no layout animations, no scroll-driven effects
4. The aesthetic favors instant feedback over animated reveals

## Chart Theming

### Timeline Strip

Uses `d3-scale` (`scaleLinear`) to map trace durations to horizontal bar widths. Each bar is colored by node type using a dedicated `TYPE_COLORS` map distinct from the role colors used in the tree view:

| Type | Timeline Color | Tree Color |
|------|---------------|------------|
| user | `--blue` | `--blue` |
| agent | `--green` | `--purple` |
| tool | `--purple` | `--text-secondary` |
| system | `--text-dim` | `--text-muted` |
| subagent | `--cyan` | `--cyan` |

Constants: `BAR_HEIGHT = 14px`, `ROW_HEIGHT = 18px`, `HEADER_HEIGHT = 20px`.

### Context Source Classification

Steps are classified into context sources (user, agent, proj, ext) for the ContextFlow visualization:
- **user** = user messages (blue)
- **agent** = LLM responses (purple, distinct from tools)
- **proj** = file operations like Read/Edit/Write/Bash/Grep/Glob (green)
- **ext** = network operations like WebSearch/WebFetch/ToolSearch (accent/orange)

## Responsive Strategy

- **Viewer**: Desktop-only. No responsive breakpoints. Panels are resizable via drag handles.
- **Marketing site**: No Tailwind responsive prefixes detected. Uses `clamp()` for hero typography (`clamp(32px, 4.5vw, 52px)`). Container is max-width 1100px with 24px padding.
- **Flask review**: Single breakpoint at 768px. Collapses multi-column grids to fewer columns, stacks verdict buttons vertically, reduces subagent indent from 32px to 12px.

## Icon System

Custom animated SVG icons based on the itshover library (https://github.com/itshover/itshover). 15 icons exported from `viewer/src/components/icons/index.ts`:

| Icon | Used For |
|------|----------|
| FileDescriptionIcon | Read tool, Glob tool |
| PenIcon | Edit tool |
| CodeIcon | Write tool |
| TerminalIcon | Bash tool, generic tool role |
| MagnifierIcon | Grep tool, ToolSearch tool |
| GlobeIcon | WebSearch, WebFetch tools |
| BrainCircuitIcon | Agent role, subagent role |
| UserIcon | User role, AskUserQuestion tool |
| SparklesIcon | Skill tool, system role |
| CodeXmlIcon | NotebookEdit tool |
| ShieldCheckIcon | Security badges |
| MessageCircleIcon | Messaging UI |
| EyeIcon | Visibility toggles |
| CopyIcon | Clipboard actions |
| CheckedIcon | Approval/completion states |

All icons share the `AnimatedIconProps` interface (`size`, `color`, `strokeWidth`, `className`) and `AnimatedIconHandle` ref type (`startAnimation`, `stopAnimation`).

## Accessibility

- Focus visible: `outline: 2px solid var(--accent); outline-offset: 2px`
- Non-keyboard focus suppressed: `a:focus:not(:focus-visible), button:focus:not(:focus-visible) { outline: none }`
- Minimum touch targets: nav links and theme toggle have `min-height: 44px`
- `::selection` styled with accent color background
- `-webkit-font-smoothing: antialiased` applied globally
