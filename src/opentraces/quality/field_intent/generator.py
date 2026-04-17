"""Field-intent spec generator: completeness diff + interactive filler.

Maintains `field_intent.yaml` inside the schema package. For every dotted
path in `FIELD_SPECS` that lacks an entry, drafts one via `claude -p`
(optional) and prompts the user to confirm or edit, then appends.

Existing entries are never touched. Runs are idempotent when the spec is
complete.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import click
import yaml

import opentraces_schema
from opentraces.quality.schema_audit import FIELD_SPECS, FieldSpec


SPEC_PATH: Path = Path(opentraces_schema.__file__).parent / "field_intent.yaml"

_MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}


@dataclass
class FieldIntent:
    path: str
    description: str
    good_example: str
    failure_modes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def load_spec(path: Path = SPEC_PATH) -> dict[str, FieldIntent]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    out: dict[str, FieldIntent] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        out[key] = FieldIntent(
            path=key,
            description=str(val.get("description", "")),
            good_example=str(val.get("good_example", "")),
            failure_modes=list(val.get("failure_modes", []) or []),
        )
    return out


def save_spec(entries: dict[str, FieldIntent], path: Path = SPEC_PATH) -> None:
    # Stable order: follow FIELD_SPECS order, then any trailing unknowns alphabetically.
    ordered_keys = [fs.path for fs in FIELD_SPECS if fs.path in entries]
    extra = sorted(k for k in entries if k not in ordered_keys)
    data = {}
    for k in ordered_keys + extra:
        fi = entries[k]
        data[k] = {
            "description": fi.description,
            "good_example": fi.good_example,
            "failure_modes": list(fi.failure_modes),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))


def missing_paths(spec: dict[str, FieldIntent], field_specs: list[FieldSpec] = FIELD_SPECS) -> list[str]:
    return [fs.path for fs in field_specs if fs.path not in spec]


# ---------------------------------------------------------------------------
# claude -p passthrough
# ---------------------------------------------------------------------------

def _run_claude(prompt: str, model: str = "haiku") -> Optional[dict]:
    """Invoke headless Claude Code. Returns parsed JSON payload or None."""
    if shutil.which("claude") is None:
        return None
    model_id = _MODEL_MAP.get(model, model)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--model", model_id, prompt],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    inner = envelope.get("result", "") if isinstance(envelope, dict) else ""
    if not isinstance(inner, str):
        return None
    text = inner.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Drafting + confirmation
# ---------------------------------------------------------------------------

_SEED_TEMPLATE = """You are drafting a field-intent entry for the opentraces TraceRecord schema.

Field: `{path}`
Source pipeline: {source}
Expected when: {expected_when}
Schema description: {description}

Return ONLY a JSON object with keys:
- "description": one sentence describing what a valid value looks like.
- "good_example": a short literal example value.
- "failure_modes": a list of 2-4 realistic ways parsers or enrichment produce wrong/garbage values for this field.

No prose, no markdown fences."""


def draft_entry(path: str, spec: FieldSpec, model: str = "haiku") -> Optional[dict]:
    prompt = _SEED_TEMPLATE.format(
        path=path,
        source=spec.source,
        expected_when=spec.expected_when,
        description=spec.description,
    )
    out = _run_claude(prompt, model=model)
    if not isinstance(out, dict):
        return None
    if "description" not in out:
        return None
    out.setdefault("good_example", "")
    fm = out.get("failure_modes", [])
    if not isinstance(fm, list):
        fm = []
    out["failure_modes"] = [str(x) for x in fm]
    return out


def _confirm_entry(path: str, draft: Optional[dict]) -> FieldIntent:
    """Interactive confirm/edit. Called only when non_interactive=False."""
    click.echo(f"\n→ {path}")
    if draft:
        click.echo(f"  draft description:  {draft.get('description','')}")
        click.echo(f"  draft good_example: {draft.get('good_example','')}")
        click.echo(f"  draft failure_modes: {draft.get('failure_modes', [])}")
        use = click.confirm("Accept draft?", default=True)
        if use:
            return FieldIntent(
                path=path,
                description=str(draft.get("description", "")),
                good_example=str(draft.get("good_example", "")),
                failure_modes=list(draft.get("failure_modes", [])),
            )
    description = click.prompt("description", default=(draft or {}).get("description", ""))
    good_example = click.prompt("good_example", default=(draft or {}).get("good_example", ""))
    failure_raw = click.prompt(
        "failure_modes (comma-separated)",
        default=", ".join((draft or {}).get("failure_modes", [])),
    )
    modes = [m.strip() for m in failure_raw.split(",") if m.strip()]
    return FieldIntent(
        path=path, description=description, good_example=good_example, failure_modes=modes,
    )


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def fill_interactive(spec_path: Path, model: str = "haiku", non_interactive: bool = False) -> int:
    """Drive the completeness-diff + interactive-fill loop.

    Returns a process-style exit code: 0 on success, nonzero if non_interactive
    mode encountered missing entries (writes nothing in that case).
    """
    spec = load_spec(spec_path)
    missing = missing_paths(spec, FIELD_SPECS)
    if not missing:
        click.echo(f"field_intent.yaml complete ({len(spec)} entries).")
        return 0

    if non_interactive:
        click.echo(f"Missing {len(missing)} field(s):", err=True)
        for p in missing:
            click.echo(f"  - {p}", err=True)
        click.echo("Run `opentraces _audit-spec` (without --non-interactive) to fill.", err=True)
        return 1

    spec_by_path = {fs.path: fs for fs in FIELD_SPECS}
    updated = dict(spec)
    click.echo(f"Filling {len(missing)} missing field(s) in {spec_path}")
    for path in missing:
        seed = spec_by_path[path]
        draft = draft_entry(path, seed, model=model)
        entry = _confirm_entry(path, draft)
        updated[path] = entry
        # Persist incrementally so interrupts don't lose work.
        save_spec(updated, spec_path)

    click.echo(f"Wrote {len(updated)} entries to {spec_path}")
    return 0
