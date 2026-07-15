"""Deterministic human projection of an arena atlas."""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .atlas import ATLAS_SCHEMA_VERSION, AtlasIntegrityError, INACTIVE_HOLE_STATES


_ROW_STATES = frozenset(
    {"unbound", "stale-run", "stale-verifier", "surface-drift", "failing", "proven"}
)


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _validated_rows(atlas: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if atlas.get("schema_version") != ATLAS_SCHEMA_VERSION:
        raise AtlasIntegrityError("unsupported atlas schema_version")
    rows = atlas.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise AtlasIntegrityError("atlas rows must be an array of objects")
    for row in rows:
        state = row.get("state")
        if state not in _ROW_STATES:
            raise AtlasIntegrityError(f"atlas row has unsupported state: {state!r}")
        if state == "unbound" and any(
            row.get(key) is not None for key in ("latest_run_id", "evidence_ref", "verdict")
        ):
            raise AtlasIntegrityError("unbound atlas row carries stored-run evidence")
    return rows


def _row_card(row: Mapping[str, Any]) -> str:
    state = str(row["state"])
    evidence = row.get("evidence_ref")
    if state == "unbound":
        evidence_html = '<p class="missing">No stored run is bound to this claim.</p>'
    else:
        evidence_html = f'<p class="evidence"><span>evidence</span> <code>{_h(evidence)}</code></p>'
    return f"""
      <article class="row state-{_h(state)}">
        <div class="row-head">
          <span class="state">{_h(state.upper())}</span>
          <span class="review">{_h(str(row.get("black_box_review") or "unreviewed").upper())}</span>
        </div>
        <h2>{_h(row.get("claim") or "")}</h2>
        <p class="identity"><code>{_h(row.get("id") or "")}</code></p>
        <p class="node"><code>{_h(row.get("nodeid") or "")}</code></p>
        {evidence_html}
      </article>"""


def render_atlas_page(atlas: Mapping[str, Any], output_path: Path) -> Path:
    """Render only the supplied projection; never infer or recompute a row state."""

    rows = _validated_rows(atlas)
    inactive = atlas.get("inactive_hole_states")
    if inactive != list(INACTIVE_HOLE_STATES):
        raise AtlasIntegrityError("atlas inactive_hole_states does not match bench.v0")
    counts = Counter(str(row["state"]) for row in rows)
    facts = "".join(
        f'<div class="fact"><strong>{counts[state]}</strong><span>{_h(state)}</span></div>'
        for state in sorted(counts)
    )
    cards = "".join(_row_card(row) for row in rows)
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenTraces arena atlas</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #0b0d10; color: #f4f0e8; }}
    main {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; padding: 64px 0 96px; }}
    .eyebrow, .fact span {{ color: #9ca3af; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }}
    h1 {{ max-width: 760px; margin: 10px 0 18px; font-size: clamp(36px, 6vw, 72px); line-height: .98; }}
    .lede {{ max-width: 760px; color: #c8c2b8; font-size: 18px; line-height: 1.55; }}
    .facts {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 32px 0; }}
    .fact {{ min-width: 130px; padding: 16px; border: 1px solid #2d333b; border-radius: 12px; background: #11151a; }}
    .fact strong, .fact span {{ display: block; }} .fact strong {{ font-size: 28px; }}
    .reduction {{ margin: 36px 0; padding: 20px; border-left: 3px solid #d8a84e; background: #17140e; }}
    .reduction h2 {{ margin: 0 0 8px; font-size: 16px; }} .reduction p {{ margin: 0; color: #c8c2b8; }}
    .rows {{ display: grid; gap: 16px; }}
    .row {{ padding: 22px; border: 1px solid #2d333b; border-radius: 14px; background: #11151a; }}
    .row-head {{ display: flex; justify-content: space-between; gap: 12px; }}
    .state, .review {{ font-size: 11px; font-weight: 700; letter-spacing: .1em; }}
    .state-proven .state {{ color: #63d99b; }} .state-failing .state {{ color: #ff7b72; }}
    .state-unbound .state, .state-stale-run .state, .state-stale-verifier .state,
    .state-surface-drift .state {{ color: #d8a84e; }}
    .review {{ color: #8b949e; }} .row h2 {{ margin: 18px 0 10px; font-size: 22px; }}
    .identity, .node, .evidence, .missing {{ margin: 8px 0 0; color: #aeb6c0; overflow-wrap: anywhere; }}
    .evidence span {{ color: #8b949e; }} .missing {{ color: #d8a84e; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    footer {{ margin-top: 38px; color: #7d8590; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">OpenTraces arena atlas · {_h(atlas.get("product_commit") or "")}</div>
    <h1>What do we actually know is true?</h1>
    <p class="lede">Every status below is projected from an exact stored run. Missing, stale, or failing evidence remains visible as a hole.</p>
    <section class="facts">{facts}</section>
    <aside class="reduction">
      <h2>Defined but inactive in bench.v0</h2>
      <p><code>no-red-proof</code> and <code>unrepresentative-world</code> are not evaluated or silently treated as proven until the deferred world work activates them.</p>
    </aside>
    <section class="rows">{cards}</section>
    <footer>capabilities {_h(atlas.get("capabilities_digest") or "")}</footer>
  </main>
</body>
</html>
"""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8", newline="\n")
    return destination
