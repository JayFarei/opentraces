"""Static human projection over a finalized bench run."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .._bucket_io import _atomic_write_text


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _href(page_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target, page_dir)).as_posix()


def render_evidence_page(run_path: Path, output_path: Path | None = None) -> Path:
    """Render only frozen bytes; never execute a verifier or recompute a verdict."""

    run_path = Path(run_path).resolve()
    result = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
    if output_path is None:
        output_path = run_path.parent.parent / "pages" / run_path.name / "index.html"
    output_path = Path(output_path)
    page_dir = output_path.parent

    action_cards: list[str] = []
    for action in sorted((run_path / "actions").iterdir()):
        if not action.is_dir():
            continue
        invocation_path = action / "invocation.json"
        action_result_path = action / "result.json"
        if not invocation_path.is_file() or not action_result_path.is_file():
            continue
        invocation = json.loads(invocation_path.read_text(encoding="utf-8"))
        observed = json.loads(action_result_path.read_text(encoding="utf-8"))
        links = []
        for name in ("invocation.json", "result.json", "stdout", "stderr", "timing.json"):
            target = action / name
            if target.is_file():
                relative_label = target.relative_to(run_path).as_posix()
                links.append(f'<a href="{_h(_href(page_dir, target))}">{_h(relative_label)}</a>')
        action_cards.append(
            '<article class="card action">'
            f'<div class="eyebrow">ACTION {_h(invocation.get("ordinal"))}</div>'
            f'<code>{_h(" ".join(invocation.get("argv", [])))}</code>'
            f'<span class="rc">rc={_h(observed.get("returncode"))}</span>'
            f'<nav>{"".join(links)}</nav>'
            "</article>"
        )

    verifier_cards = []
    for verifier in result.get("verifiers", []):
        source = verifier.get("source_ref") or {}
        verifier_cards.append(
            '<article class="card verifier">'
            f'<div class="eyebrow">VERIFIER · {_h(verifier.get("status", "unknown").upper())}</div>'
            f'<strong>{_h(verifier.get("name"))}</strong>'
            f'<div class="source">{_h(source.get("path"))}</div>'
            f'<code>{_h(source.get("digest"))}</code>'
            f'<p>{_h((verifier.get("reason") or {}).get("message", "Observed condition held."))}</p>'
            "</article>"
        )

    players = []
    for index, channel in enumerate(result.get("recordings", {}).get("channels", []), start=1):
        path = channel.get("path")
        if channel.get("complete") and path:
            casts = channel.get("casts") or [{"cast_ref": path, "label": channel["kind"]}]
            for cast_index, cast in enumerate(casts, start=1):
                cast_path = run_path / cast["cast_ref"]
                player_id = f"cast-{index}-{cast_index}"
                players.append(
                    '<article class="card player">'
                    f'<div class="eyebrow">{_h(cast.get("label", "TERMINAL"))}</div>'
                    f'<button data-cast="{_h(_href(page_dir, cast_path))}" '
                    f'data-target="{player_id}">Play terminal recording</button>'
                    f'<pre id="{player_id}"></pre>'
                    "</article>"
                )
        else:
            players.append(
                '<article class="card incomplete">'
                '<div class="eyebrow">RECORDING INCOMPLETE</div>'
                f'<p>{_h(channel.get("reason") or "No recording was produced.")}</p>'
                "</article>"
            )

    player_js = (Path(__file__).with_name("assets") / "asciicast-player.js").read_text(
        encoding="utf-8"
    )
    integrity = json.loads((run_path / ".integrity.json").read_text(encoding="utf-8"))
    exhaust_refs = sorted(
        {
            *integrity.get("files", {}),
            ".integrity.json",
            "result.json",
        }
    )
    exhaust_links = "".join(
        f'<a href="{_h(_href(page_dir, run_path / relative))}">{_h(relative)}</a>'
        for relative in exhaust_refs
    )
    verdict = result.get("verdict") or "error"
    reason = result.get("reason") or {}
    reason_html = (
        '<section class="card outcome">'
        f'<div class="eyebrow">OUTCOME REASON · {_h(str(reason.get("code")).upper())}</div>'
        f'<p>{_h(reason.get("message"))}</p>'
        "</section>"
        if reason
        else ""
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{_h(result['scenario']['claim'])} · bench evidence</title>
<style>
:root{{--ink:#11100e;--paper:#f3efe6;--line:#c9c0ad;--green:#17643d;--red:#9b2c2c;--muted:#6f685c}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
main{{width:min(960px,calc(100% - 32px));margin:48px auto 96px}} h1{{font:700 clamp(30px,6vw,64px)/1.04 system-ui,sans-serif;letter-spacing:-.04em;max-width:16ch}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));border:1px solid var(--line);margin:32px 0}} .fact{{padding:16px;border-right:1px solid var(--line)}}
.eyebrow{{font-size:12px;letter-spacing:.12em;color:var(--muted);margin-bottom:8px}} .verdict{{color:{'var(--green)' if verdict == 'pass' else 'var(--red)'};font-weight:800;font-size:24px}}
h2{{font:700 22px system-ui,sans-serif;margin-top:48px}} .stack{{display:grid;gap:12px}} .card{{min-width:0;border:1px solid var(--line);background:#fffaf0;padding:16px;overflow-wrap:anywhere}}
.action code{{display:block;margin-bottom:10px}} .rc{{display:inline-block;border:1px solid var(--line);padding:2px 7px}} nav{{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}} a{{color:#254d80}}
button{{font:inherit;background:var(--ink);color:white;border:0;padding:10px 14px;cursor:pointer}} pre{{white-space:pre-wrap;min-height:120px;background:#141414;color:#eee;padding:12px;overflow:auto}}
</style></head><body><main>
<div class="eyebrow">OPENTRACES · BENCH.V0 · {_h(result['run_id'])}</div>
<h1>{_h(result['scenario']['claim'])}</h1>
<section class="facts"><div class="fact"><div class="eyebrow">VERDICT</div><div class="verdict">{_h(verdict.upper())}</div></div>
<div class="fact"><div class="eyebrow">EXECUTION</div>{_h(result['execution_status'])}</div>
<div class="fact"><div class="eyebrow">EVIDENCE</div>{'complete' if result['evidence']['complete'] else 'incomplete'}</div>
<div class="fact"><div class="eyebrow">REWATCHABLE</div>{str(result['recordings']['rewatchable']).lower()}</div></section>
{reason_html}
<h2>Recording</h2><section class="stack">{''.join(players)}</section>
<h2>Actions and raw output</h2><section class="stack">{''.join(action_cards)}</section>
<h2>Explicit verifiers</h2><section class="stack">{''.join(verifier_cards)}</section>
<h2>Complete stored exhaust</h2><nav class="card exhaust">{exhaust_links}</nav>
</main><script>{player_js}</script></body></html>"""
    _atomic_write_text(output_path, document)
    return output_path
