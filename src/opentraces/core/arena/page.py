"""Static human projection over a finalized bench run."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from .._bucket_io import _atomic_write_text
from .run_store import RunStore


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _href(page_dir: Path, target: Path) -> str:
    return Path(os.path.relpath(target, page_dir)).as_posix()


def _resolve_run_ref(run_path: Path, reference: object) -> Path | None:
    if not isinstance(reference, str) or not reference:
        return None
    try:
        target = (run_path / reference).resolve()
    except (OSError, RuntimeError):
        return None
    if not target.is_relative_to(run_path) or not target.is_file():
        return None
    return target


def _action_label(invocation: dict[str, object]) -> str:
    argv = invocation.get("argv")
    if isinstance(argv, list):
        return " ".join(str(part) for part in argv)
    kind = str(invocation.get("kind") or "browser action")
    subject = invocation.get("selector") or invocation.get("url") or invocation.get("name")
    return f"{kind} {subject}" if subject else kind


def render_evidence_page(run_path: Path, output_path: Path | None = None) -> Path:
    """Render only frozen bytes; never execute a verifier or recompute a verdict."""

    run_path = Path(run_path).resolve()
    RunStore(run_path.parent).verify(run_path)
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
            f'<article class="card action" id="action-{_h(action.name)}" '
            f'data-action-ref="actions/{_h(action.name)}">'
            f'<div class="eyebrow">ACTION {_h(invocation.get("ordinal"))}</div>'
            f"<code>{_h(_action_label(invocation))}</code>"
            f'<span class="rc">rc={_h(observed.get("returncode"))}</span>'
            f"<nav>{''.join(links)}</nav>"
            "</article>"
        )

    verifier_cards = []
    for verifier in result.get("verifiers", []):
        source = verifier.get("source_ref") or {}
        verifier_cards.append(
            '<article class="card verifier">'
            f'<div class="eyebrow">VERIFIER · {_h(verifier.get("status", "unknown").upper())}</div>'
            f"<strong>{_h(verifier.get('name'))}</strong>"
            f'<div class="source">{_h(source.get("path"))}</div>'
            f"<code>{_h(source.get('digest'))}</code>"
            f"<p>{_h((verifier.get('reason') or {}).get('message', 'Observed condition held.'))}</p>"
            "</article>"
        )

    recordings = result.get("recordings", {})
    timeline_status = recordings.get("timeline") or {}
    timeline_ref = recordings.get("timeline_ref")
    timeline_rows: list[dict[str, object]] = []
    if timeline_status.get("complete"):
        timeline_path = _resolve_run_ref(run_path, timeline_ref)
        if timeline_path is not None:
            try:
                timeline_rows = [
                    row
                    for line in timeline_path.read_text(encoding="utf-8").splitlines()
                    if isinstance((row := json.loads(line)), dict)
                ]
            except (OSError, json.JSONDecodeError):
                timeline_rows = []
    media_start_offsets = {
        str(row["action_ref"]): int(row["offset_ms"])
        for row in timeline_rows
        if row.get("event") == "action_started"
        and isinstance(row.get("action_ref"), str)
        and isinstance(row.get("offset_ms"), int)
    }
    browser_start_offset = min(
        (
            int(row["offset_ms"])
            for row in timeline_rows
            if row.get("event") == "action_started"
            and row.get("surface") == "browser"
            and isinstance(row.get("offset_ms"), int)
        ),
        default=0,
    )

    players: list[str] = []
    for index, channel in enumerate(result.get("recordings", {}).get("channels", []), start=1):
        path = channel.get("path")
        kind = channel.get("kind")
        if channel.get("complete") and path and kind == "terminal":
            casts = channel.get("casts") or [{"cast_ref": path, "label": channel["kind"]}]
            for cast_index, cast in enumerate(casts, start=1):
                cast_ref = cast.get("cast_ref")
                cast_path = _resolve_run_ref(run_path, cast_ref)
                if cast_path is None:
                    players.append(
                        '<article class="card incomplete">'
                        '<div class="eyebrow">MISSING RECORDING</div>'
                        f'<p>{_h(cast_ref)}</p>'
                        "</article>"
                    )
                    continue
                player_id = f"cast-{index}-{cast_index}"
                ordinal = int(cast.get("ordinal") or cast_index)
                action_ref = f"actions/{ordinal:04d}"
                media_start_offset = media_start_offsets.get(action_ref, 0)
                players.append(
                    '<article class="card player">'
                    f'<div class="eyebrow">{_h(cast.get("label", "TERMINAL"))}</div>'
                    f'<button data-cast="{_h(_href(page_dir, cast_path))}" '
                    f'data-target="{player_id}" data-action-ref="{action_ref}" '
                    f'data-media-start-offset-ms="{media_start_offset}">'
                    "Play terminal recording</button>"
                    f'<pre id="{player_id}"></pre>'
                    "</article>"
                )
        elif channel.get("complete") and path and kind == "browser_video":
            video_path = _resolve_run_ref(run_path, path)
            if video_path is None:
                players.append(
                    '<article class="card incomplete">'
                    '<div class="eyebrow">MISSING RECORDING</div>'
                    f"<p>{_h(path)}</p>"
                    "</article>"
                )
                continue
            players.append(
                '<article class="card player" data-media-kind="browser_video">'
                '<div class="eyebrow">BROWSER VIDEO</div>'
                f'<video controls preload="metadata" data-media-start-offset-ms="{browser_start_offset}" '
                f'src="{_h(_href(page_dir, video_path))}"></video>'
                f'<nav><a href="{_h(_href(page_dir, video_path))}">{_h(path)}</a></nav>'
                "</article>"
            )
        elif channel.get("complete") and path and kind == "playwright_trace":
            trace_path = _resolve_run_ref(run_path, path)
            if trace_path is None:
                players.append(
                    '<article class="card incomplete">'
                    '<div class="eyebrow">MISSING RECORDING</div>'
                    f"<p>{_h(path)}</p>"
                    "</article>"
                )
                continue
            players.append(
                '<article class="card player" data-media-kind="playwright_trace">'
                '<div class="eyebrow">PLAYWRIGHT TRACE</div>'
                f'<a href="{_h(_href(page_dir, trace_path))}">Open Playwright trace</a>'
                f'<div>{_h(path)}</div>'
                "</article>"
            )
        elif channel.get("complete") and path and kind == "browser_screenshots":
            manifest_path = _resolve_run_ref(run_path, path)
            if manifest_path is None:
                players.append(
                    '<article class="card incomplete">'
                    '<div class="eyebrow">MISSING RECORDING</div>'
                    f"<p>{_h(path)}</p>"
                    "</article>"
                )
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                screenshot_refs = manifest.get("screenshots") or []
            except (OSError, json.JSONDecodeError):
                screenshot_refs = []
            screenshot_items: list[str] = []
            for ref in screenshot_refs:
                screenshot_path = _resolve_run_ref(run_path, ref)
                if screenshot_path is None:
                    screenshot_items.append(
                        '<div class="card incomplete">'
                        '<div class="eyebrow">MISSING RECORDING</div>'
                        f"<p>{_h(ref)}</p>"
                        "</div>"
                    )
                    continue
                screenshot_items.append(
                    '<figure>'
                    f'<img loading="lazy" src="{_h(_href(page_dir, screenshot_path))}" '
                    f'alt="Browser screenshot {_h(ref)}">'
                    f'<figcaption><a href="{_h(_href(page_dir, screenshot_path))}">{_h(ref)}</a></figcaption>'
                    "</figure>"
                )
            players.append(
                '<article class="card player screenshots" data-media-kind="browser_screenshots">'
                '<div class="eyebrow">BROWSER SCREENSHOTS</div>'
                f"{''.join(screenshot_items)}"
                "</article>"
            )
        else:
            players.append(
                '<article class="card incomplete">'
                '<div class="eyebrow">RECORDING INCOMPLETE</div>'
                f"<p>{_h(channel.get('reason') or 'No recording was produced.')}</p>"
                "</article>"
            )

    timeline_cards = "".join(
        '<button class="timeline-row" data-focus-boundary '
        f'data-sequence="{_h(row.get("sequence"))}" '
        f'data-offset-ms="{_h(row.get("offset_ms"))}" '
        f'data-surface="{_h(row.get("surface"))}" '
        f'data-event="{_h(row.get("event"))}" '
        f'data-action-ref="{_h(row.get("action_ref"))}">'
        f'<strong>{_h(row.get("offset_ms"))} ms · {_h(row.get("surface"))}</strong>'
        f'<span>{_h(row.get("event"))} · {_h(row.get("action_ref"))}</span>'
        f'<small>Causal: {_h(", ".join(row.get("causal_refs") or []) or "none")}</small>'
        "</button>"
        for row in timeline_rows
    )
    timeline_html = (
        '<h2>Cross-surface timeline</h2>'
        f'<section class="timeline" data-timeline-ref="{_h(timeline_ref)}">'
        f"{timeline_cards}</section>"
        if timeline_rows
        else '<h2>Cross-surface timeline</h2><section class="card incomplete">'
        f'<p>{_h(timeline_status.get("reason") or f"Stored timeline is unavailable: {timeline_ref}")}</p></section>'
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
    exhaust_items = []
    for relative in exhaust_refs:
        target = _resolve_run_ref(run_path, relative)
        if target is None:
            exhaust_items.append(
                f'<span><strong>MISSING EXHAUST</strong> · {_h(relative)}</span>'
            )
        else:
            exhaust_items.append(
                f'<a href="{_h(_href(page_dir, target))}">{_h(relative)}</a>'
            )
    exhaust_links = "".join(exhaust_items)
    verdict = result.get("verdict") or "error"
    product_pin = result.get("pins", {}).get("product") or {}
    product_commit = product_pin.get("commit") or "unavailable"
    product_worktree = product_pin.get("worktree") or "unavailable"
    reason = result.get("reason") or {}
    reason_html = (
        '<section class="card outcome">'
        f'<div class="eyebrow">OUTCOME REASON · {_h(str(reason.get("code")).upper())}</div>'
        f"<p>{_h(reason.get('message'))}</p>"
        "</section>"
        if reason
        else ""
    )
    world_cards: list[str] = []
    for name, pin in sorted((result.get("pins", {}).get("emulators") or {}).items()):
        ledger_ref = f"ledgers/{name}.jsonl"
        ledger_path = run_path / ledger_ref
        ledger_link = (
            f'<a href="{_h(_href(page_dir, ledger_path))}">{_h(ledger_ref)}</a>'
            if ledger_path.is_file()
            else '<span class="muted">ledger unavailable</span>'
        )
        setup = (pin or {}).get("setup") or {}
        baseline = setup.get("baseline") or {}
        identity = baseline.get("identity") or {}
        capabilities = setup.get("capabilities") or {}
        capability_rows = "".join(
            f"<div>{_h(status)}: {_h(', '.join(map(str, operations)))}</div>"
            for status, operations in sorted(capabilities.items())
        )
        world_ref = str((pin or {}).get("evidence_ref") or f"world/{name}.json")
        world_path = run_path / world_ref
        world_link = (
            f'<a href="{_h(_href(page_dir, world_path))}">{_h(world_ref)}</a>'
            if world_path.is_file()
            else '<span class="muted">world setup unavailable</span>'
        )
        world_cards.append(
            '<article class="card world">'
            f'<div class="eyebrow">WORLD · {_h(str(name).upper())}</div>'
            f"<strong>{_h(name)}</strong>"
            f"<div>sha256: {_h((pin or {}).get('sha256'))}</div>"
            f"<div>toolchain: {_h((pin or {}).get('bun_version'))} · "
            f"{_h((pin or {}).get('target'))}</div>"
            f"<div>endpoint: {_h(setup.get('endpoint'))}</div>"
            f"<div>baseline identity: {_h(identity.get('name'))}</div>"
            f"{capability_rows}"
            f"<nav>{world_link}{ledger_link}</nav>"
            "</article>"
        )
    world_html = (
        f'<h2>World</h2><section class="stack">{"".join(world_cards)}</section>'
        if world_cards
        else ""
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{_h(result["scenario"]["claim"])} · bench evidence</title>
<style>
:root{{--ink:#11100e;--paper:#f3efe6;--line:#c9c0ad;--green:#17643d;--red:#9b2c2c;--muted:#6f685c}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
main{{width:min(960px,calc(100% - 32px));margin:48px auto 96px}} h1{{font:700 clamp(30px,6vw,64px)/1.04 system-ui,sans-serif;letter-spacing:-.04em;max-width:16ch}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));border:1px solid var(--line);margin:32px 0}} .fact{{padding:16px;border-right:1px solid var(--line)}}
.eyebrow{{font-size:12px;letter-spacing:.12em;color:var(--muted);margin-bottom:8px}} .verdict{{color:{"var(--green)" if verdict == "pass" else "var(--red)"};font-weight:800;font-size:24px}}
h2{{font:700 22px system-ui,sans-serif;margin-top:48px}} .stack{{display:grid;gap:12px}} .card{{min-width:0;border:1px solid var(--line);background:#fffaf0;padding:16px;overflow-wrap:anywhere}}
.action code{{display:block;margin-bottom:10px}} .rc{{display:inline-block;border:1px solid var(--line);padding:2px 7px}} nav{{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}} a{{color:#254d80}}
button{{font:inherit;background:var(--ink);color:white;border:0;padding:10px 14px;cursor:pointer}} pre{{white-space:pre-wrap;min-height:120px;background:#141414;color:#eee;padding:12px;overflow:auto}}
.timeline{{display:grid;gap:6px}} .timeline-row{{display:grid;grid-template-columns:180px 1fr auto;text-align:left;gap:12px;align-items:center}}
.timeline-row small{{color:#d8d0c0}} video,img{{display:block;width:100%;max-height:540px;object-fit:contain;background:#141414}} figure{{margin:12px 0}}
</style></head><body><main>
<div class="eyebrow">OPENTRACES · BENCH.V0 · {_h(result["run_id"])}</div>
<h1>{_h(result["scenario"]["claim"])}</h1>
<section class="facts"><div class="fact"><div class="eyebrow">VERDICT</div><div class="verdict">{_h(verdict.upper())}</div></div>
<div class="fact"><div class="eyebrow">MODE</div>{_h(result["execution_mode"])}</div>
<div class="fact"><div class="eyebrow">PRODUCT PIN</div><code>{_h(product_commit)}</code><div>worktree {_h(product_worktree)}</div></div>
<div class="fact"><div class="eyebrow">EXECUTION</div>{_h(result["execution_status"])}</div>
<div class="fact"><div class="eyebrow">EVIDENCE</div>{"complete" if result["evidence"]["complete"] else "incomplete"}</div>
<div class="fact"><div class="eyebrow">REWATCHABLE</div>{str(result["recordings"]["rewatchable"]).lower()}</div></section>
{reason_html}
{world_html}
{timeline_html}
<h2>Recording</h2><section class="stack">{"".join(players)}</section>
<h2>Actions and raw output</h2><section class="stack">{"".join(action_cards)}</section>
<h2>Explicit verifiers</h2><section class="stack">{"".join(verifier_cards)}</section>
<h2>Complete stored exhaust</h2><nav class="card exhaust">{exhaust_links}</nav>
</main><script>{player_js}</script><script>
document.addEventListener("click", (event) => {{
  const boundary = event.target.closest("[data-focus-boundary]");
  if (!boundary) return;
  const actionRef = boundary.dataset.actionRef;
  const action = document.getElementById("action-" + actionRef.split("/").pop());
  const media = boundary.dataset.surface === "terminal"
    ? document.querySelector('[data-cast][data-action-ref="' + actionRef + '"]')
    : document.querySelector('[data-media-kind="browser_video"] video');
  const boundaryOffsetMs = Number(boundary.dataset.offsetMs);
  const mediaStartOffsetMs = Number(media?.dataset.mediaStartOffsetMs || 0);
  const seekOffsetMs = Math.max(0, boundaryOffsetMs - mediaStartOffsetMs);
  if (media instanceof HTMLVideoElement) {{
    media.currentTime = seekOffsetMs / 1000;
    media.play().catch((error) => {{ media.title = String(error); }});
  }} else if (media) {{
    media.dataset.seekOffsetMs = String(seekOffsetMs);
    media.click();
  }}
  (media || action)?.scrollIntoView({{behavior: "smooth", block: "center"}});
}});
</script></body></html>"""
    _atomic_write_text(output_path, document)
    return output_path
