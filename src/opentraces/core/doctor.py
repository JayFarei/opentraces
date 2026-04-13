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
from ..core.config import ProjectConfig, load_project_config
from ..core.processors import probe_processors
from ..security.trufflehog import find_trufflehog
from ..security.version import SECURITY_VERSION


def _trufflehog_status(enabled: bool, version: str | None) -> str:
    if not enabled:
        return "disabled (opt in via 'opentraces setup trufflehog')"
    if version is None:
        return "ENABLED-BUT-MISSING — run 'opentraces setup trufflehog --verify'"
    return f"enabled ({version})"


def _infer_backend_label(provider: str, base_url: str) -> str:
    """Translate (provider, base_url) into a user-facing backend name.

    ``provider`` is our internal HTTP-shape dispatch key (openai,
    anthropic, ollama native, fake). ``base_url`` is where the bytes
    actually go. Users think in vendor names (Ollama, Groq, LM Studio),
    not dispatch keys — so we infer one from the URL.
    """
    if provider == "anthropic":
        return "anthropic"
    if provider == "fake":
        return "fake"
    if provider == "ollama":
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
    return base_url or provider


def _review_llm_status(rc) -> dict[str, Any]:
    """Probe the configured review-LLM endpoint.

    Never raises: failures become human-readable status strings so the
    surrounding doctor report stays renderable.
    """
    import os

    backend = _infer_backend_label(rc.provider, rc.base_url)
    if not rc.enabled:
        return {
            "enabled": False,
            "status": "disabled (opt in via 'opentraces setup review-llm')",
            "provider": rc.provider, "backend": backend, "model": rc.model,
        }

    result: dict[str, Any] = {
        "enabled": True,
        "provider": rc.provider,
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
        if rc.provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                result["reachable"] = False
                result["status"] = "anthropic SDK not installed (pip install anthropic)"
                return result
            result["reachable"] = True
            result["status"] = f"enabled ({backend} / {rc.model})"
            return result

        if rc.provider in ("openai", "ollama"):
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
                "when": spec.when,
                "resolved_path": resolved,
                "status": "detected" if resolved else "missing",
            }
        )
    return out


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
        cfg: loaded global config (has security.trufflehog.enabled, hf_token, intent.mode)
        cwd: project directory for post-processor probing (defaults to CWD)
    """
    cwd = cwd or Path.cwd()
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled

    return {
        "security_version": SECURITY_VERSION,
        "schema_version": _schema_version(),
        "trufflehog": {
            "enabled": th_enabled,
            "binary_version": th_version,
            "status": _trufflehog_status(th_enabled, th_version),
        },
        "review_llm": _review_llm_status(cfg.security.review_llm),
        "hf_auth": "ok" if cfg.hf_token else "missing",
        "intent": {"mode": cfg.intent.mode},
        "post_processors": _post_processors(cwd),
        "hooks": _hook_installers(),
    }


def exit_code(report_data: dict[str, Any]) -> int:
    """Non-zero when a configured integration is broken."""
    th = report_data.get("trufflehog") or {}
    if th.get("enabled") and th.get("binary_version") is None:
        return 3
    rl = report_data.get("review_llm") or {}
    if rl.get("enabled") and rl.get("reachable") is False:
        return 3
    return 0
