# Site Redesign — OpenTraces Design System adoption

Move the marketing site from the current mono-bodied warm-paper aesthetic to the
**OpenTraces Hub design system** (`/tmp/ot-redesign/opentraces-hub-design-system`),
recomposed in the spacious, modern register of the inspiration sites
(traces.com, flueframework.com). The design system's README is explicit: the
marketing surface should **match the in-product register, not invent a new one**.

## Hard constraints (do NOT change)
- Hero `<h1>` text stays exactly **"Open data is the new open source."**
- Nav links stay **centered**.
- All existing routes keep working: `/`, `/hub`, `/explorer`, `/schema`, `/docs`.
- The HubWindow stays dark slate (it is "another app"); it already matches.

## Foundation (authored centrally in globals.css + layout.tsx)

### Fonts (layout.tsx, next/font/google)
- **Geist** → `--font-body` (body & UI). Weights 300–700.
- **Geist Mono** → `--font-mono` (metadata, IDs, eyebrows, kbd, pills, code).
- **Space Grotesk** → `--font-display` (h1–h4, big card titles, wordmark).
- Body default is now **sans (Geist), not monospace**. This is the core departure.

### Color ramp — default LIGHT (warm Stacklane), dark via toggle (GitHub slate)
Repoint existing vars to OT values AND add OT aliases so old + new code both work.

| var (kept) | light | dark | OT alias (added) |
|---|---|---|---|
| `--bg` | `#f5f5f4` | `#0d1117` | — |
| `--surface` | `#ffffff` | `#161b22` | — |
| `--surface-hover` / add `--surface-2` | `#fafaf9` | `#1c2128` | `--surface-2` |
| `--surface-elevated` / add `--surface-3` | `#f0f0ef` | `#22272e` | `--surface-3` |
| `--text` | `#18181b` | `#e6edf3` | `--fg` |
| `--text-secondary` | `#3f3f46` | `#c9d1d9` | — |
| `--text-muted` | `#52525b` | `#9ba1a8` | `--fg-dim` |
| `--text-dim` | `#a1a1aa` | `#6e7681` | `--fg-mute` |
| add `--fg-sub` | `#d4d4d8` | `#484f58` | `--fg-sub` |
| `--border` | `#e7e5e4` | `#30363d` | — |
| `--border-strong` | `#d6d3d1` | `#444c56` | — |

Action palette (both themes, oklch — add these):
`--c-user: oklch(78% .16 62)` amber · `--c-plan: oklch(78% .15 95)` ·
`--c-think: oklch(58% .02 260)` (light: `55%`) · `--c-read: oklch(72% .12 200)` teal ·
`--c-exec: oklch(70% .17 290)` purple · `--c-write: oklch(70% .19 355)` magenta ·
`--c-error: oklch(63% .22 25)` · `--c-git: oklch(72% .12 150)` green · `--c-push: oklch(72% .14 220)` blue ·
`--c-hf: #FFD21E`.

`--accent` → keep an amber, align to `--c-user`. Map `--green→--c-git`, `--red→--c-error`,
`--cyan→--c-read`, `--blue→--c-push`, `--yellow→--c-plan` (keep the existing names alive).

### Type scale & shadows
- base body 13.5px Geist, line-height 1.5. h1 `--fs-h1: 38–44px` display (marketing runs
  larger than the 26px in-app); h2 ~26px; section titles Space Grotesk.
- Shadows OFF in dark (`--shadow-card: none`), subtle in light
  (`0 1px 2px rgba(0,0,0,.04)`), per OT.
- radii: `--radius: 8px`, sm 4–6px, lg 12px.
- motion: `--ease-out: cubic-bezier(.2,.8,.2,1)`, durations 120/180/220ms.

### Signature conventions
- **Eyebrow**: uppercase Geist Mono, 9–10px, tracking 0.18–0.22em, `--fg-mute`. Replaces
  the current SectionRule label style (keep the rule line, restyle the label to this).
- **Cards**: `background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  box-shadow: var(--shadow-card)`. Flat. No gradients. Optional colored 2px left-border only
  for "live" emphasis.
- **Eyebrow/meta** always Geist Mono; **names/headings** always Space Grotesk; **prose** Geist.
- Mid-dot `·` separators; `→` affordances; never emoji.
- Subtle **grid/dot background texture** on the page (flue-style), very faint, behind content.

## Layout language (from flue + traces)
- Generous vertical rhythm between sections (more whitespace than current).
- Hero: keep the two-column (copy + terminal/HubWindow) but larger display H1
  (Space Grotesk), Geist subcopy, framed window blocks with the existing traffic-light chrome.
- Feature grids: bordered cells on a `--border` hairline grid (like flue's Features), Space
  Grotesk cell title + Geist description + optional mono eyebrow.
- Keep the existing structural components; restyle, don't rebuild. Preserve content.

## Per-component directives (Phase: implement, disjoint files)
- **Nav.tsx**: centered links (keep), wordmark = `open`(300 dim)`traces`(700) Space Grotesk;
  theme toggle + github stays. Geist Mono nav links → switch to Geist for a cleaner read,
  keep lowercase.
- **Hero.tsx**: bigger Space Grotesk h1 (unchanged text), Geist subcopy, OT pill/eyebrow,
  install tabs + terminal restyle to OT surfaces. Keep the 7-tab terminal panel structure.
- **Features.tsx**: OT bordered feature grid (Space Grotesk titles, Geist descriptions).
- **PrivacyTrust / InfraDiagram / GetStarted / Attribution / SchemaExplorer / Footer /
  StarCallout / HubTeaser**: repoint to new tokens, headings → Space Grotesk, eyebrows →
  Geist Mono, cards → OT card. Keep all content + structure. The pipeline diagram and the
  security registry keep their structure; only typography/surfaces/colors update.

## Verification
- `tsc --noEmit` clean. Homepage + /hub + /schema + /explorer render in both themes.
- Adversarial design review: token coherence, no leftover IBM-Plex mono body, no broken
  contrast in either theme, eyebrows/headings use the right families.
