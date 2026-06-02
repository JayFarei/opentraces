#!/usr/bin/env python3
"""Render the human-readable standup (standup-<date>.md) as a readable HTML article.

Styling is the opentraces "Capsule" editorial aesthetic: warm paper, dark
warm-brown ink, rust accent, Instrument Serif headings over a sans body. This is
an *article*, not a dashboard: headings plus prose paragraphs. The only structured
element is the four-line standup-ready block at the end.

Input is the markdown standup (## headings, **bold** lead-ins, prose). We keep the
parse deliberately small: H1 title, H2 project sections, the Standup-ready block,
and inline **bold** / `code`.

Usage:
    python render_html.py [--md standup-2026-06-01.md] [--out standup-2026-06-01.html]
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

PAL = {
    "page": "#E4E2DF", "paper": "#F1EAD8", "paper2": "#EFE7D2",
    "ink": "#2E2920", "ink2": "#16120C", "muted": "#8C7F66", "muted2": "#6B6149",
    "line": "#C9BFA3", "rust": "#C8421F", "rust2": "#E26941", "gold": "#D4A64A",
    "green": "#4F7A2E", "teal": "#6FB5B0",
}

# project -> accent (build vs research vs personal get different hues)
ACCENT = {
    "opentraces": PAL["rust"],
    "research": PAL["teal"],
    "research (personal assistant sessions)": PAL["teal"],
    "svc-client-convert / client-humandur": PAL["gold"],
    "kb (knowledge base)": PAL["green"],
}


def esc(s: str) -> str:
    return html.escape(s)


def inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r'<code>\1</code>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


def parse(md: str) -> dict:
    lines = md.splitlines()
    title = "Standup"
    subtitle = ""
    intro = ""
    sections: list[dict] = []
    standup: list[tuple[str, str]] = []
    cur: dict | None = None
    mode = "head"
    i = 0
    # title
    for ln in lines:
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    para: list[str] = []

    def flush(target):
        nonlocal para
        if para:
            target.append(" ".join(para).strip())
            para = []

    for ln in lines:
        s = ln.rstrip()
        if s.startswith("# "):
            continue
        if s.startswith("## "):
            name = s[3:].strip()
            if name.lower() == "standup-ready":
                if cur:
                    flush(cur["paras"])
                cur = None
                mode = "standup"
                continue
            if cur:
                flush(cur["paras"])
            cur = {"name": name, "paras": []}
            sections.append(cur)
            mode = "section"
            continue
        if s.strip() == "---":
            if cur:
                flush(cur["paras"])
            continue
        if mode == "head":
            if s.strip():
                para.append(s.strip())
            else:
                if para and not intro:
                    intro = " ".join(para).strip()
                    para = []
            continue
        if mode == "standup":
            m = re.match(r"\*\*([^:]+):\*\*\s*(.*)", s.strip())
            if m:
                standup.append((m.group(1), m.group(2)))
            elif s.strip() and standup:
                k, v = standup[-1]
                standup[-1] = (k, (v + " " + s.strip()).strip())
            continue
        if mode == "section" and cur is not None:
            if not s.strip():
                flush(cur["paras"])
            else:
                para.append(s.strip())
    if cur:
        flush(cur["paras"])
    if para and not intro:
        intro = " ".join(para).strip()
    return {"title": title, "intro": intro, "sections": sections, "standup": standup}


def render(doc: dict, date_label: str) -> str:
    secs = ""
    for sec in doc["sections"]:
        accent = ACCENT.get(sec["name"].lower(), PAL["muted2"])
        paras = "".join(f"<p>{inline(p)}</p>" for p in sec["paras"])
        secs += f"""
      <section class="proj">
        <h2 style="--accent:{accent}">{esc(sec['name'])}</h2>
        {paras}
      </section>"""

    standup_rows = ""
    kmap = {"yesterday": PAL["gold"], "today": PAL["teal"],
            "blockers": PAL["rust2"], "risks / questions": PAL["muted"]}
    for k, v in doc["standup"]:
        c = kmap.get(k.lower(), PAL["gold"])
        standup_rows += f"""
        <div class="srow"><div class="sk" style="color:{c}">{esc(k)}</div><div class="sv">{inline(v)}</div></div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(doc['title'])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
  :root {{
    --page:{PAL['page']}; --paper:{PAL['paper']}; --paper2:{PAL['paper2']};
    --ink:{PAL['ink']}; --ink2:{PAL['ink2']}; --muted:{PAL['muted']}; --muted2:{PAL['muted2']};
    --line:{PAL['line']}; --rust:{PAL['rust']}; --gold:{PAL['gold']};
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--page); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    line-height:1.62; -webkit-font-smoothing:antialiased; padding:48px 20px 90px; font-size:16.5px; }}
  .wrap {{ max-width:720px; margin:0 auto; }}

  .mast {{ display:flex; align-items:flex-start; justify-content:space-between; gap:24px;
    border-bottom:2px solid var(--ink2); padding-bottom:20px; margin-bottom:26px; }}
  .kicker {{ font-size:12px; letter-spacing:.22em; text-transform:uppercase; color:var(--rust); font-weight:600; }}
  h1 {{ font-family:"Instrument Serif",Georgia,serif; font-weight:400; font-size:64px; line-height:.95;
    color:var(--ink2); letter-spacing:-.01em; margin-top:2px; }}
  .capsule {{ width:46px; height:82px; flex:0 0 auto; margin-top:4px; }}
  .intro {{ font-family:"Instrument Serif",Georgia,serif; font-style:italic; font-size:20px;
    color:var(--muted2); margin-bottom:42px; }}

  .proj {{ margin-bottom:34px; }}
  .proj h2 {{ font-family:"Instrument Serif",Georgia,serif; font-weight:400; font-size:30px;
    color:var(--ink2); margin-bottom:12px; padding-left:14px; position:relative; }}
  .proj h2::before {{ content:""; position:absolute; left:0; top:7px; bottom:7px; width:4px;
    border-radius:2px; background:var(--accent); }}
  .proj p {{ margin-bottom:13px; color:var(--ink); }}
  .proj p strong {{ color:var(--ink2); }}
  code {{ font-family:ui-monospace,SFMono-Regular,monospace; font-size:.86em;
    background:var(--paper2); border:1px solid var(--line); border-radius:4px; padding:.5px 5px; color:var(--muted2); }}

  .standup {{ background:var(--ink2); color:#EDE6D5; border-radius:14px; padding:30px 32px; margin-top:18px; }}
  .standup h2 {{ font-family:"Instrument Serif",Georgia,serif; font-weight:400; font-size:30px; color:var(--paper); margin-bottom:16px; }}
  .srow {{ display:grid; grid-template-columns:120px 1fr; gap:16px; padding:13px 0; border-top:1px solid #ffffff1f; }}
  .srow:first-of-type {{ border-top:none; }}
  .sk {{ font-size:12px; letter-spacing:.14em; text-transform:uppercase; padding-top:3px; }}
  .sv {{ font-size:15px; color:#EDE6D5; line-height:1.55; }}
  .sv strong {{ color:#fff; }}
  .sv code {{ background:#ffffff14; border-color:#ffffff22; color:#E8DFCB; }}

  footer {{ text-align:center; color:var(--muted); font-size:12.5px; margin-top:40px; }}
  footer .cap {{ color:var(--rust); }}

  @media (max-width:640px) {{
    h1 {{ font-size:46px; }}
    .srow {{ grid-template-columns:1fr; gap:3px; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="mast">
      <div>
        <div class="kicker">Daily Standup</div>
        <h1>{esc(date_label)}</h1>
      </div>
      <svg class="capsule" viewBox="0 0 110 200" xmlns="http://www.w3.org/2000/svg">
        <g fill="none" stroke="{PAL['ink2']}" stroke-width="6">
          <rect x="15" y="2" width="80" height="40" rx="4" fill="{PAL['rust']}"></rect>
          <rect x="31" y="44" width="48" height="14" fill="{PAL['paper2']}"></rect>
          <path d="M31 60 Q31 70 12 82 L8 86 L8 176 Q8 196 34 196 L76 196 Q102 196 102 176 L102 86 L98 82 Q79 70 79 60 Z" fill="{PAL['paper2']}"></path>
          <path d="M10 120 L10 176 Q10 192 34 192 L76 192 Q100 192 100 176 L100 120 Z" fill="{PAL['rust']}" fill-opacity="0.18" stroke="none"></path>
        </g>
      </svg>
    </header>

    <p class="intro">{inline(doc['intro'])}</p>

    {secs}

    <div class="standup">
      <h2>Standup-ready</h2>
      {standup_rows}
    </div>

    <footer>Reconstructed by the <span class="cap">standup</span> consumer from the local trace bucket &middot; prose, not telemetry</footer>
  </div>
</body>
</html>"""


def main():
    here = Path(__file__).parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    md_path = Path(args.md) if args.md else sorted(here.glob("standup-2*.md"))[-1]
    md = md_path.read_text()
    doc = parse(md)
    m = re.search(r"#\s*Standup\s*[—-]\s*(.+)", md)
    date_label = m.group(1).strip() if m else doc["title"]
    out = Path(args.out) if args.out else md_path.with_suffix(".html")
    out.write_text(render(doc, date_label))
    print(f"[wrote {out}]  sections={len(doc['sections'])} standup_lines={len(doc['standup'])}")


if __name__ == "__main__":
    main()
