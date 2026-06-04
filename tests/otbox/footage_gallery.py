"""Self-contained gallery renderer for otbox journey footage.

Produces a single standalone ``gallery.html`` — no external assets beyond
a Google Fonts ``<link>`` — showing one card per scenario × harness. Each
card embeds the recorded MP4 (``<video controls preload="metadata">``), the
scenario name + description, an agent badge, the per-turn list, a verdict
chip, and the run duration.

Aesthetic: a blueprint / editorial system. Fraunces (a distinctive
high-contrast serif display) for headings, JetBrains Mono for code/turns,
Inter for body. A faint blueprint grid background, CSS custom properties for
theming, and a ``prefers-color-scheme`` dark variant. No neon, no violet.
"""

from __future__ import annotations

import html
from collections import Counter

__all__ = ["render_gallery_html"]


_VERDICT_CLASS = {
    "PASS": "chip-pass",
    "FAIL": "chip-fail",
    "SKIP": "chip-skip",
}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _verdict_chip(verdict: str) -> str:
    cls = _VERDICT_CLASS.get(verdict, "chip-skip")
    return f'<span class="chip {cls}">{_esc(verdict)}</span>'


def _task_block(turns: list[dict]) -> str:
    """The simulated user's actual instruction(s) — the heart of the journey."""
    prompts = [str(t.get("prompt") or "") for t in turns if t.get("prompt")]
    if not prompts:
        return ""
    steps = "".join(f"<li>{_esc(p)}</li>" for p in prompts)
    suffix = "" if len(prompts) == 1 else f" · {len(prompts)} turns"
    return (
        f'<div class="card-task">'
        f'<span class="task-label">▸ the simulated user asked{suffix}</span>'
        f'<ol class="task-steps">{steps}</ol>'
        f"</div>"
    )


def _outcome_strip(turns: list[dict]) -> str:
    """Compact per-turn outcome (matched ✓/✗ + elapsed); no prompt repeat."""
    if not turns:
        return ""
    chips: list[str] = []
    for t in turns:
        ok = bool(t.get("matched"))
        mark = "✓" if ok else "✗"
        cls = "turn-ok" if ok else "turn-miss"
        elapsed = t.get("elapsed_s", 0.0)
        chips.append(
            f'<li class="turn {cls}"><span class="turn-mark">{mark}</span>'
            f'turn {_esc(t.get("index", "?"))} · {_esc(f"{elapsed:.1f}s")}'
            f"</li>"
        )
    return f'<ol class="card-turns">{"".join(chips)}</ol>'


def _card(card: dict) -> str:
    scenario = _esc(card.get("scenario", ""))
    agent = _esc(card.get("agent", "unknown"))
    description = _esc(card.get("description", ""))
    verdict = str(card.get("verdict", "SKIP"))
    duration = card.get("duration_s", 0.0)
    turn_count = card.get("turn_count", 0)
    binary_version = _esc(card.get("binary_version", ""))
    error_message = _esc(card.get("error_message", ""))
    mp4_rel = card.get("mp4_rel", "")

    if mp4_rel:
        media = (
            f'<video class="card-video" controls preload="metadata" '
            f'src="{_esc(mp4_rel)}"></video>'
        )
    else:
        reason = error_message or "no footage produced"
        media = (
            f'<div class="card-novideo">'
            f'<div class="novideo-mark">▦</div>'
            f'<div class="novideo-reason">{reason}</div>'
            f"</div>"
        )

    meta_bits = [f"{turn_count} turns", f"{duration:.1f}s"]
    if binary_version:
        meta_bits.append(binary_version)
    meta_line = " · ".join(_esc(b) for b in meta_bits)

    error_block = ""
    if error_message and verdict != "PASS":
        error_block = f'<p class="card-error">{error_message}</p>'

    return f"""
    <article class="card card-{_esc(verdict.lower())}">
      <header class="card-head">
        <div class="card-titles">
          <h2 class="card-scenario">{scenario}</h2>
          <span class="badge badge-{agent}">{agent}</span>
        </div>
        {_verdict_chip(verdict)}
      </header>
      <div class="card-media">{media}</div>
      {_task_block(card.get("turns", []))}
      {f'<p class="card-desc"><span class="desc-label">validates</span>{description}</p>' if description else ""}
      <p class="card-meta">{meta_line}</p>
      {error_block}
      {_outcome_strip(card.get("turns", []))}
    </article>
    """


def render_gallery_html(cards: list[dict]) -> str:
    """Return the full standalone HTML document string for the gallery."""
    counts = Counter(str(c.get("verdict", "SKIP")) for c in cards)
    total = len(cards)
    legend = (
        f'<span class="legend-item"><span class="chip chip-pass">PASS</span>'
        f' {counts.get("PASS", 0)}</span>'
        f'<span class="legend-item"><span class="chip chip-fail">FAIL</span>'
        f' {counts.get("FAIL", 0)}</span>'
        f'<span class="legend-item"><span class="chip chip-skip">SKIP</span>'
        f' {counts.get("SKIP", 0)}</span>'
        f'<span class="legend-item legend-total">{total} clips</span>'
    )

    # Sort: PASS first, then FAIL, then SKIP; stable within by scenario.
    order = {"PASS": 0, "FAIL": 1, "SKIP": 2}
    cards_sorted = sorted(
        cards,
        key=lambda c: (
            order.get(str(c.get("verdict", "SKIP")), 3),
            str(c.get("scenario", "")),
            str(c.get("agent", "")),
        ),
    )
    card_html = "\n".join(_card(c) for c in cards_sorted)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OpenTraces — otbox journey footage</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root {{
    --bg: #f4f2ec;
    --grid: rgba(28, 49, 87, 0.06);
    --ink: #16243f;
    --ink-soft: #51607a;
    --paper: #fbfaf6;
    --line: #d9d3c5;
    --accent: #1c3157;
    --accent-soft: #d6e0f0;
    --pass: #2f7d52;
    --pass-bg: #dff0e4;
    --fail: #b5742a;
    --fail-bg: #f6e7d2;
    --skip: #7a7468;
    --skip-bg: #e8e4da;
    --radius: 14px;
    --shadow: 0 1px 2px rgba(22, 36, 63, 0.06), 0 8px 24px rgba(22, 36, 63, 0.07);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #11161f;
      --grid: rgba(150, 178, 222, 0.06);
      --ink: #e8edf6;
      --ink-soft: #97a3b8;
      --paper: #182030;
      --line: #2a3650;
      --accent: #9db8e6;
      --accent-soft: #243349;
      --pass: #6fcf97;
      --pass-bg: #1c3326;
      --fail: #e0a45c;
      --fail-bg: #36291a;
      --skip: #9a9486;
      --skip-bg: #24262c;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 10px 30px rgba(0, 0, 0, 0.35);
    }}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background-color: var(--bg);
    background-image:
      linear-gradient(var(--grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--grid) 1px, transparent 1px);
    background-size: 32px 32px;
    color: var(--ink);
    font-family: "Inter", system-ui, sans-serif;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}
  header.masthead {{
    max-width: 1280px;
    margin: 0 auto;
    padding: 56px 32px 24px;
    border-bottom: 1px solid var(--line);
  }}
  .eyebrow {{
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin: 0 0 10px;
  }}
  h1.title {{
    font-family: "Fraunces", Georgia, serif;
    font-weight: 800;
    font-size: clamp(34px, 5vw, 56px);
    line-height: 1.02;
    letter-spacing: -0.015em;
    margin: 0 0 8px;
    color: var(--ink);
  }}
  h1.title .thin {{ font-weight: 400; font-style: italic; color: var(--ink-soft); }}
  .lede {{
    max-width: 64ch;
    color: var(--ink-soft);
    font-size: 16px;
    margin: 0 0 20px;
  }}
  .legend {{
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: center;
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    color: var(--ink-soft);
  }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 7px; }}
  .legend-total {{ margin-left: auto; }}

  main {{
    max-width: 1280px;
    margin: 0 auto;
    padding: 32px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 24px;
  }}
  .card {{
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 18px 18px 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: transform 0.16s ease, box-shadow 0.16s ease;
  }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 8px rgba(22,36,63,.1), 0 16px 40px rgba(22,36,63,.12); }}
  .card-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
  .card-titles {{ display: flex; flex-direction: column; gap: 6px; min-width: 0; }}
  .card-scenario {{
    font-family: "Fraunces", Georgia, serif;
    font-weight: 600;
    font-size: 19px;
    letter-spacing: -0.01em;
    margin: 0;
    word-break: break-word;
  }}
  .badge {{
    align-self: flex-start;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 2px 9px;
    border-radius: 999px;
    border: 1px solid var(--line);
    color: var(--accent);
    background: var(--accent-soft);
  }}
  .badge-echo {{ opacity: 0.8; }}
  .chip {{
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.08em;
    padding: 3px 10px;
    border-radius: 999px;
    white-space: nowrap;
  }}
  .chip-pass {{ color: var(--pass); background: var(--pass-bg); }}
  .chip-fail {{ color: var(--fail); background: var(--fail-bg); }}
  .chip-skip {{ color: var(--skip); background: var(--skip-bg); }}

  .card-media {{ border-radius: 10px; overflow: hidden; border: 1px solid var(--line); background: #000; }}
  .card-video {{ display: block; width: 100%; height: auto; aspect-ratio: 110 / 32; background: #000; }}
  .card-novideo {{
    aspect-ratio: 110 / 32;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: repeating-linear-gradient(45deg, var(--accent-soft), var(--accent-soft) 12px, var(--paper) 12px, var(--paper) 24px);
    color: var(--ink-soft);
    text-align: center;
    padding: 16px;
  }}
  .novideo-mark {{ font-size: 26px; opacity: 0.5; }}
  .novideo-reason {{ font-family: "JetBrains Mono", monospace; font-size: 12px; max-width: 80%; }}

  .card-desc {{ margin: 0; color: var(--ink-soft); font-size: 13px; }}
  .desc-label {{
    display: inline-block; margin-right: 6px; font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent);
    font-weight: 600;
  }}
  .card-task {{
    margin: 0; padding: 11px 13px; border-radius: 9px;
    background: var(--accent-soft); border: 1px solid var(--line);
  }}
  .task-label {{
    display: block; font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.09em; color: var(--accent); font-weight: 600;
    margin-bottom: 6px;
  }}
  .task-steps {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }}
  .task-steps li {{ font-size: 13.5px; line-height: 1.45; color: var(--ink); }}
  .task-steps li + li {{ padding-top: 6px; border-top: 1px dashed var(--line); }}
  .card-meta {{
    margin: 0;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: var(--ink-soft);
  }}
  .card-error {{
    margin: 0;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    color: var(--fail);
    background: var(--fail-bg);
    padding: 8px 10px;
    border-radius: 8px;
    word-break: break-word;
  }}
  .card-turns {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }}
  .turn {{
    display: grid;
    grid-template-columns: 16px 18px 1fr auto;
    align-items: baseline;
    gap: 8px;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    padding: 3px 6px;
    border-radius: 6px;
  }}
  .turn:nth-child(odd) {{ background: rgba(28, 49, 87, 0.03); }}
  @media (prefers-color-scheme: dark) {{ .turn:nth-child(odd) {{ background: rgba(150,178,222,0.04); }} }}
  .turn-mark {{ font-weight: 600; }}
  .turn-ok .turn-mark {{ color: var(--pass); }}
  .turn-miss .turn-mark {{ color: var(--fail); }}
  .turn-idx {{ color: var(--ink-soft); }}
  .turn-prompt {{ color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .turn-time {{ color: var(--ink-soft); }}
  .turn-empty {{ color: var(--ink-soft); font-style: italic; display: block; }}

  footer.colophon {{
    max-width: 1280px;
    margin: 0 auto;
    padding: 24px 32px 64px;
    border-top: 1px solid var(--line);
    color: var(--ink-soft);
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
  }}
  footer.colophon a {{ color: var(--accent); }}
</style>
</head>
<body>
  <header class="masthead">
    <p class="eyebrow">opentraces · otbox</p>
    <h1 class="title">otbox journey <span class="thin">footage</span></h1>
    <p class="lede">
      Each clip is a <strong>simulated user driving a real coding agent</strong>
      (claude / codex / pi) through a scripted task inside an isolated otbox,
      recorded with terminal-control. Every card shows the exact instruction the
      user gave and what the agent did, so you can watch each journey opentraces
      captures. A visual review aid; the tmux capture-refresh path stays the
      assertion-grade gate.
    </p>
    <div class="legend">{legend}</div>
  </header>
  <main>
{card_html}
  </main>
  <footer class="colophon">
    Recorded with <a href="https://github.com/kitlangton/terminal-control">terminal-control</a>.
    Footage is a visual aid, not an assertion artifact. Regenerate with
    <code>make otbox-footage-all</code>.
  </footer>
</body>
</html>
"""
