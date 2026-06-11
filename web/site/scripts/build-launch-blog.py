#!/usr/bin/env python3
"""Build the published launch-announcement page from the kb draft.

Strips the draft-only review-mode tooling (CSS, markup, JS), swaps the draft's
floating theme toggle + minimal footer for the site's nav and footer (theme
synced with the rest of opentraces.ai via localStorage), injects page metadata,
and writes the result into public/blog/ so Next serves it statically.
Re-run after editing kb/blog/2026-06-10-opentraces-launch.html.
"""
import os
import re

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "kb", "blog", "2026-06-10-opentraces-launch.html")
DST = os.path.join(
    os.path.dirname(__file__), "..", "public", "blog", "introducing-opentraces-0-4", "index.html"
)

html = open(SRC).read()

# ── 1) strip review-mode draft tooling ──────────────────────────────────────
html = re.sub(
    r"\n  /\* ── review mode \(draft tool, strip before publishing\) ── \*/.*?(?=\n</style>)",
    "",
    html,
    flags=re.S,
)
html = re.sub(r"\n<button class=\"review-toggle\".*?</button>", "", html, flags=re.S)
html = re.sub(r"\n<div class=\"rv-panel\" id=\"rvPanel\">.*?\n</div>", "", html, count=1, flags=re.S)
html = re.sub(
    r"\n  // ── review mode: contextual comments, exportable as one paste \(draft tool\) ──\n  \(\(\) => \{.*?\n  \}\)\(\);\n",
    "\n",
    html,
    flags=re.S,
)
html = html.replace(
    "\n    .review-toggle { top: 12px; right: 88px; padding: 5px 10px; }", ""
)

# ── 2) head metadata + pre-paint theme resolver (same contract as the site) ─
meta = """<script>
(function () {
  try {
    var t = localStorage.getItem('theme') ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
</script>
<meta name="description" content="Introducing opentraces 0.4: a local-first evidence layer for what your agents see, do, and change. Captured privately, anchored to Git, turned into search, lineage, evals, and datasets.">
<link rel="canonical" href="https://opentraces.ai/blog/introducing-opentraces-0-4">
<meta property="og:title" content="Traces Are the New Source Code">
<meta property="og:description" content="Introducing opentraces 0.4: a local-first evidence layer for what your agents see, do, and change.">
<meta property="og:type" content="article">
<meta property="og:url" content="https://opentraces.ai/blog/introducing-opentraces-0-4">
<meta property="og:image" content="https://opentraces.ai/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
"""
html = html.replace("<title>", meta + "<title>")

# ── 3) site chrome CSS (nav + footer, mirrors opentraces.ai globals,
#       remapped onto the article's tokens) ─────────────────────────────────
chrome_css = """
  /* ── site chrome: nav + footer (mirrors opentraces.ai) ── */
  :root { --accent: #F97316; --accent-bg: rgba(249, 115, 22, .12); }
  [data-theme="light"] { --accent: #C2410C; --accent-bg: rgba(194, 65, 12, .08); }
  .site-shell { max-width: 1100px; margin: 0 auto; padding: 0 24px; }
  .nav {
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px 0; border-bottom: 1px solid var(--border);
    font-family: var(--font-mono);
  }
  .nav-logo { font-size: 16px; text-decoration: none; display: inline-flex; align-items: baseline; flex-shrink: 0; }
  .brand-open { font-family: var(--font-display); font-weight: 300; letter-spacing: -0.02em; color: var(--fg-dim); }
  .brand-traces { font-family: var(--font-display); font-weight: 700; letter-spacing: -0.03em; color: var(--fg); }
  [data-theme="light"] .brand-open { color: #6B6B6B; }
  [data-theme="light"] .brand-traces { color: #000000; }
  .nav-links { display: flex; gap: 24px; align-items: center; font-size: 12px; }
  .nav-link {
    color: var(--fg-dim); text-decoration: none; transition: color .1s;
    padding: 12px 0; min-height: 44px; display: inline-flex; align-items: center;
    font-family: var(--font-body); font-size: 13.5px; letter-spacing: -0.005em;
  }
  .nav-link:hover { color: var(--fg); }
  .nav-new-pill {
    margin-left: 6px; font-family: var(--font-mono); font-size: 8.5px; font-weight: 500;
    letter-spacing: .08em; text-transform: uppercase; line-height: 1;
    color: var(--accent); background: var(--accent-bg);
    border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
    border-radius: 999px; padding: 3px 6px;
  }
  .nav-star-badge { display: inline-flex; align-items: center; gap: 2px; color: var(--fg-dim); font-variant-numeric: tabular-nums; }
  .nav-star-badge svg { color: var(--accent); vertical-align: middle; flex-shrink: 0; }
  .nav-github-link:hover .nav-star-badge { color: var(--fg); }
  .nav-theme-btn {
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: 6px;
    padding: 6px 14px; font-family: var(--font-mono); font-size: 11px; cursor: pointer;
    color: var(--fg-dim); transition: all .15s; line-height: 1; min-height: 32px;
  }
  .nav-theme-btn:hover { color: var(--fg); border-color: var(--fg-mute); }
  .nav-hamburger {
    display: none; background: none; border: none; font-size: 22px; line-height: 1;
    padding: 4px 2px; cursor: pointer; color: var(--fg-dim); font-family: var(--font-mono);
  }
  .nav-hamburger:hover { color: var(--fg); }
  .site-footer {
    padding: 24px 0 56px; border-top: 1px solid var(--border); margin-top: 56px;
    font-family: var(--font-mono); font-size: 11px; color: var(--fg-mute);
    display: flex; justify-content: space-between;
  }
  .site-footer a { color: inherit; text-decoration: none; }
  .site-footer a:hover { color: var(--fg); }
  .openmake-mark {
    display: inline-block; width: 8px; height: 8px; background: currentColor;
    margin-right: 6px; vertical-align: baseline; transition: background .15s ease;
  }
  .site-footer a:hover .openmake-mark { background: #E63329; }
  @media (max-width: 680px) {
    .nav { position: relative; }
    .nav-hamburger { display: block; }
    /* bleed past the .site-shell 24px gutters so the menu spans the viewport */
    .nav-links {
      display: none; position: absolute; top: 100%; left: -24px; right: -24px;
      background: var(--bg); border-bottom: 1px solid var(--border);
      flex-direction: column; padding: 12px 24px 16px; gap: 0; z-index: 100;
      align-items: flex-start;
    }
    .nav-links-open { display: flex; }
    .nav-link { padding: 10px 0; min-height: 44px; }
    .nav-divider { display: none; }
    .nav-theme-btn { align-self: flex-start; margin-top: 8px; }
  }
"""
html = html.replace("\n</style>", chrome_css + "</style>")

# Article header breathes less now that the nav sits above it.
html = html.replace(
    "header { padding: 84px 0 36px; }", "header { padding: 48px 0 36px; }"
)
html = html.replace(
    "    header { padding: 64px 0 28px; }", "    header { padding: 36px 0 24px; }"
)

# ── 4) swap the floating theme toggle for the site nav ──────────────────────
nav_html = """<div class="site-shell">
<nav class="nav">
  <a href="/" class="nav-logo"><span class="brand-open">open</span><span class="brand-traces">traces</span></a>
  <button class="nav-hamburger" id="navHamburger" aria-label="Toggle menu" aria-expanded="false">≡</button>
  <div class="nav-links" id="navLinks">
    <a href="/schema" class="nav-link">schema</a>
    <a href="/explorer" class="nav-link">explorer</a>
    <a href="/hub" class="nav-link">hub<span class="nav-new-pill">new</span></a>
    <a href="/docs" class="nav-link">docs</a>
    <a href="/llms.txt" class="nav-link" target="_blank" rel="noopener noreferrer">/llms.txt</a>
    <a href="https://github.com/JayFarei/opentraces" class="nav-link nav-github-link" target="_blank" rel="noopener noreferrer">github<span class="nav-star-badge">&thinsp;[<svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m8 2 1.6 3.3 3.6.5-2.6 2.5.6 3.6L8 10.1 4.8 11.9l.6-3.6L2.8 5.8l3.6-.5Z" fill="currentColor" stroke="currentColor" stroke-width="0.5" stroke-linejoin="miter"/></svg><span id="navStars">—</span>]</span><svg width="9" height="9" viewBox="0 0 9 9" fill="none" aria-hidden="true" style="margin-left:3px;vertical-align:middle;opacity:.55"><path d="M1 8L8 1M8 1H3M8 1V6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg></a>
    <span class="nav-divider" style="color:var(--border)">|</span>
    <button class="nav-theme-btn" id="themeToggle" aria-label="Toggle theme"><span id="themeLabel">light</span></button>
  </div>
</nav>
</div>"""
html = re.sub(
    r"<button class=\"theme-toggle\" id=\"themeToggle\".*?</button>",
    nav_html,
    html,
    count=1,
    flags=re.S,
)

# ── 5) swap the draft footer for the site footer ────────────────────────────
site_footer = """<div class="site-shell">
<footer class="site-footer">
  <span>©2026 <a href="https://openmake.ai" target="_blank" rel="noopener noreferrer"><span class="openmake-mark" aria-hidden="true"></span>OpenMake</a></span>
  <span><span class="brand-open">open</span><span class="brand-traces">traces</span></span>
</footer>
</div>"""
html = re.sub(r"<footer>\n  <div class=\"wrap\">.*?</footer>", site_footer, html, count=1, flags=re.S)

# ── 6) theme JS: persist to localStorage (shared with the site) + nav extras ─
old_theme_js = """  // ── theme toggle (dark default; matches twitter-article reading mode) ──
  const root = document.documentElement;
  const tBtn = document.getElementById('themeToggle');
  const tLbl = document.getElementById('themeLabel');
  tBtn.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    tLbl.textContent = next === 'dark' ? 'light' : 'dark';
  });"""
new_theme_js = """  // ── site chrome: theme (synced with opentraces.ai), menu, star badge ──
  const root = document.documentElement;
  const tBtn = document.getElementById('themeToggle');
  const tLbl = document.getElementById('themeLabel');
  const syncThemeLabel = () => {
    tLbl.textContent = root.dataset.theme === 'dark' ? 'light' : 'dark';
  };
  syncThemeLabel();
  tBtn.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch (e) {}
    syncThemeLabel();
  });
  const burger = document.getElementById('navHamburger');
  const navLinks = document.getElementById('navLinks');
  burger.addEventListener('click', () => {
    const open = navLinks.classList.toggle('nav-links-open');
    burger.textContent = open ? '✕' : '≡';
    burger.setAttribute('aria-expanded', String(open));
  });
  fetch('/api/stars').then((r) => r.json()).then((d) => {
    if (d && typeof d.stars === 'string') {
      document.getElementById('navStars').textContent = d.stars;
    }
  }).catch(() => {});"""
assert old_theme_js in html, "theme toggle JS drifted; update build script"
html = html.replace(old_theme_js, new_theme_js)

# ── 7) sanity ────────────────────────────────────────────────────────────────
assert "rvPanel" not in html, "review panel remnants"
assert "review-toggle" not in html, "review toggle remnants"
assert "rv-pop" not in html, "review css remnants"
assert html.count("nav-logo") == 2, "nav not injected"  # css + markup
assert "openmake-mark" in html, "footer not injected"

os.makedirs(os.path.dirname(os.path.abspath(DST)), exist_ok=True)
open(DST, "w").write(html)
print("written", os.path.abspath(DST), len(html))
