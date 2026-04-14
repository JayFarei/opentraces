"""Collect health signals for `opentraces doctor`.

Pure aggregator: reads config + filesystem state, returns a dict. The CLI
layer is responsible for rendering. Keeping the logic here means doctor's
shape is testable without going through Click, and any future surfaces
(web status panel, CI health check) can reuse it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..capture import get_hook_installers
from ..core.config import ProjectConfig, load_project_config, project_is_opted_in
from ..core.processors import probe_processors
from ..enrichment.entities import EntityRunner
from ..enrichment.entities.installer import _safe_platform
from ..enrichment.entities.runner import resolve_binary_path
from ..security.trufflehog import find_trufflehog
from ..security.version import SECURITY_VERSION


def _regex_pattern_count() -> int:
    try:
        from ..security.secrets import _PATTERNS  # type: ignore

        return len(_PATTERNS)
    except Exception:
        return 0


def _security_tiers(
    cfg,
    trufflehog_version: str | None,
    review_llm: dict,
    review_policy: str | None,
) -> list[dict[str, Any]]:
    """Build the ordered tier list shown in `opentraces doctor`.

    Each entry has:
    - ``name``: user-facing label (no numeric IDs — those confuse users)
    - ``state``: machine-readable label (always-on / enabled / disabled /
      missing / unreachable / required / not-required)
    - ``detail``: one-line status
    - ``enable_cmd`` / ``disable_cmd``: what the user can type to flip it
      (``None`` when not applicable, e.g. always-on tiers)
    - ``blocks``: does this tier block upload on finding
    """
    # TruffleHog
    th_enabled = cfg.security.trufflehog.enabled
    if not th_enabled:
        th_state, th_detail = "disabled", None
    elif trufflehog_version is None:
        th_state = "missing"
        th_detail = "binary not found; run 'opentraces setup trufflehog --verify'"
    else:
        th_state = "enabled"
        th_detail = f"{trufflehog_version} — blocks on finding"

    # LLM trace review
    rl_enabled = bool(review_llm.get("enabled"))
    rl_reachable = review_llm.get("reachable")
    if not rl_enabled:
        rl_state, rl_detail = "disabled", None
    elif rl_reachable is False:
        rl_state = "unreachable"
        rl_detail = review_llm.get("status") or "endpoint unreachable"
    else:
        rl_state = "enabled"
        backend = review_llm.get("backend") or review_llm.get("api_format") or "?"
        rl_detail = f"{backend} / {review_llm.get('model') or '?'}"

    # Human review — gated by project review policy, not a global toggle.
    if review_policy == "auto":
        hr_state = "not-required"
        hr_detail = "project policy: auto (safe traces auto-approve)"
    elif review_policy == "review":
        hr_state = "required"
        hr_detail = "project policy: review (every trace needs approval)"
    else:
        hr_state = "not-initialized"
        hr_detail = "run 'opentraces init' to set a project policy"

    return [
        {
            "name": "Regex patterns",
            "state": "always-on",
            "detail": f"{_regex_pattern_count()} built-in detectors",
            "enable_cmd": None,
            "disable_cmd": None,
            "blocks": False,
        },
        {
            "name": "Shannon entropy",
            "state": "always-on",
            "detail": "high-entropy strings flagged",
            "enable_cmd": None,
            "disable_cmd": None,
            "blocks": False,
        },
        {
            "name": "TruffleHog",
            "state": th_state,
            "detail": th_detail,
            "enable_cmd": "opentraces setup trufflehog",
            "disable_cmd": "opentraces setup trufflehog --disable",
            "blocks": True,
            "binary_version": trufflehog_version,
        },
        {
            "name": "LLM trace review",
            "state": rl_state,
            "detail": rl_detail,
            "enable_cmd": "opentraces setup llm-review",
            "disable_cmd": "opentraces setup llm-review --disable",
            "blocks": False,
            "api_format": review_llm.get("api_format"),
            "backend": review_llm.get("backend"),
            "model": review_llm.get("model"),
            "reachable": rl_reachable,
        },
        {
            "name": "Human review",
            "state": hr_state,
            "detail": hr_detail,
            "enable_cmd": "opentraces setup review-policy --review",
            "disable_cmd": "opentraces setup review-policy --auto",
            "blocks": False,
            "review_policy": review_policy,
        },
    ]


def _trufflehog_status(enabled: bool, version: str | None) -> str:
    if not enabled:
        return "disabled (opt in via 'opentraces setup trufflehog')"
    if version is None:
        return "ENABLED-BUT-MISSING — run 'opentraces setup trufflehog --verify'"
    return f"enabled ({version})"


def _infer_backend_label(api_format: str, base_url: str) -> str:
    """Translate (api_format, base_url) into a user-facing backend name.

    ``api_format`` is our internal HTTP-shape dispatch key
    (openai-compat, anthropic, ollama native, fake). ``base_url`` is
    where the bytes actually go. Users think in vendor names (Ollama,
    Groq, LM Studio), not dispatch keys — so we infer one from the URL.
    """
    if api_format == "anthropic":
        return "anthropic"
    if api_format == "fake":
        return "fake"
    if api_format == "ollama":
        return "ollama"
    url = (base_url or "").lower()
    if "localhost:11434" in url or "127.0.0.1:11434" in url:
        return "ollama"
    if "localhost:1234" in url or "127.0.0.1:1234" in url:
        return "lm-studio"
    if "localhost:8080" in url or "127.0.0.1:8080" in url:
        return "llama.cpp"
    if "localhost:8000" in url or "127.0.0.1:8000" in url:
        return "vllm"
    if "localhost" in url or "127.0.0.1" in url:
        return "local"
    if "api.openai.com" in url:
        return "openai"
    if "api.groq.com" in url:
        return "groq"
    if "openrouter.ai" in url:
        return "openrouter"
    if "api.together" in url:
        return "together"
    if "api.anthropic.com" in url:
        return "anthropic"
    return base_url or api_format


def _review_llm_status(rc) -> dict[str, Any]:
    """Probe the configured review-LLM endpoint.

    Never raises: failures become human-readable status strings so the
    surrounding doctor report stays renderable.
    """
    import os

    backend = _infer_backend_label(rc.api_format, rc.base_url)
    if not rc.enabled:
        return {
            "enabled": False,
            "status": "disabled (opt in via 'opentraces setup llm-review')",
            "api_format": rc.api_format, "backend": backend, "model": rc.model,
        }

    result: dict[str, Any] = {
        "enabled": True,
        "api_format": rc.api_format,
        "backend": backend,
        "model": rc.model,
        "base_url": rc.base_url,
        "api_key_env": rc.api_key_env,
    }

    if rc.api_key_env and not os.environ.get(rc.api_key_env):
        result["reachable"] = False
        result["status"] = (
            f"enabled but env var ${rc.api_key_env} is not set"
        )
        return result

    try:
        if rc.api_format == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                result["reachable"] = False
                result["status"] = "anthropic SDK not installed (pip install anthropic)"
                return result
            result["reachable"] = True
            result["status"] = f"enabled ({backend} / {rc.model})"
            return result

        if rc.api_format in ("openai-compat", "ollama"):
            from .. import security as _sec  # noqa: F401 — ensure package importable
            from ..security.llm_provider import OpenAICompatProvider

            p = OpenAICompatProvider(
                model=rc.model,
                base_url=rc.base_url,
                api_key_env=rc.api_key_env,
                timeout=min(10.0, rc.timeout),
            )
            ping = p.ping()
            models = ping.get("models") or []
            note = f"{len(models)} models available" if models else "endpoint reachable"
            if models and rc.model not in models:
                note += f"; model '{rc.model}' not found"
            result["reachable"] = True
            result["status"] = f"enabled ({backend} / {rc.model}) — {note}"
            return result

        result["reachable"] = True
        result["status"] = f"enabled ({backend})"
        return result
    except Exception as exc:
        result["reachable"] = False
        result["status"] = f"UNREACHABLE — {exc}"
        return result


def _schema_version() -> str | None:
    try:
        from opentraces_schema import SCHEMA_VERSION  # type: ignore

        return SCHEMA_VERSION
    except Exception:
        return None


def _post_processors(cwd: Path) -> list[dict[str, Any]]:
    try:
        raw = load_project_config(cwd)
        proj_cfg = ProjectConfig.model_validate(raw) if raw else None
        specs = proj_cfg.post_processors if proj_cfg else []
    except Exception:
        specs = []
    out: list[dict[str, Any]] = []
    for spec, resolved in probe_processors(specs):
        out.append(
            {
                "name": spec.name,
                "command": spec.command,
                "resolved_path": resolved,
                "status": "detected" if resolved else "missing",
            }
        )
    return out


def _project_review_policy(cwd: Path) -> str | None:
    """Return the project's review_policy ("review"/"auto"), or None if no project.

    load_project_config() returns defaults rather than None, so check the
    actual file to distinguish "initialized with default" from "never
    initialized here."
    """
    if not project_is_opted_in(cwd):
        return None
    try:
        raw = load_project_config(cwd)
        proj = ProjectConfig.model_validate(raw)
        return proj.review_policy
    except Exception:
        return None


def _entity_parser_status() -> dict[str, Any]:
    """Report on the entity-parser binary.

    Shape:
      {
        "binary_path": "/abs/path",
        "installed": bool,
        "version": str | None,
        "platform": "darwin-arm64" | "unknown" | …,
        "advice": str | None,   # what to run if missing
      }
    """
    path = resolve_binary_path()
    runner = EntityRunner(binary_path=path)
    installed = runner.available()
    version = runner.version() if installed else None
    return {
        "binary_path": str(path),
        "installed": installed,
        "version": version,
        "platform": _safe_platform(),
        "advice": None if installed else "run 'opentraces setup entity-parser'",
    }


def _hook_installers() -> list[dict[str, Any]]:
    """Call .status() on every registered HookInstaller."""
    out: list[dict[str, Any]] = []
    for name, cls in get_hook_installers().items():
        try:
            st = cls().status()
        except Exception as e:
            st = {"installer": name, "installed": False, "error": str(e)}
        out.append(st)
    return out


def report(cfg, cwd: Path | None = None) -> dict[str, Any]:
    """Build the doctor payload.

    Args:
        cfg: loaded global config (has security.trufflehog.enabled, hf_token)
        cwd: project directory for post-processor probing (defaults to CWD)
    """
    cwd = cwd or Path.cwd()
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled
    llm_review = _review_llm_status(cfg.security.llm_review)
    review_policy = _project_review_policy(cwd)

    opted_in = sorted(getattr(cfg, "projects", {}).keys())

    return {
        "security_version": SECURITY_VERSION,
        "schema_version": _schema_version(),
        "opted_in_projects": {
            "count": len(opted_in),
            "paths": opted_in,
        },
        "security": {
            "version": SECURITY_VERSION,
            "tiers": _security_tiers(cfg, th_version, llm_review, review_policy),
            "classifier_sensitivity": getattr(cfg, "classifier_sensitivity", "medium"),
            "review_policy": review_policy,
            "blocked_reasons": ["parse_error", "trufflehog_finding"],
        },
        # Legacy flat keys — kept for backward compatibility with the
        # existing doctor JSON consumers. Prefer ``security.tiers``.
        "trufflehog": {
            "enabled": th_enabled,
            "binary_version": th_version,
            "status": _trufflehog_status(th_enabled, th_version),
        },
        "llm_review": llm_review,
        "hf_auth": "ok" if cfg.hf_token else "missing",
        "post_processors": _post_processors(cwd),
        "entity_parser": _entity_parser_status(),
        "hooks": _hook_installers(),
    }


def exit_code(report_data: dict[str, Any]) -> int:
    """Non-zero when a configured integration is broken."""
    tiers = (report_data.get("security") or {}).get("tiers") or []
    for t in tiers:
        state = t.get("state")
        if state in ("missing", "unreachable"):
            return 3
    for h in report_data.get("hooks") or []:
        if h.get("installer") == "skill" and h.get("installed") and (
            h.get("drift") or h.get("broken_harnesses")
        ):
            return 3
    return 0
