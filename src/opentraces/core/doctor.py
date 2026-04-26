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


# --- Trace Trails event-log panel (plan 054 phase 1) ----------------------

def _trail_event_log_status(cwd: Path) -> dict[str, Any]:
    """Report integrity for the canonical local Trace Trails event log."""
    try:
        from .trails import event_log_status

        return event_log_status(cwd)
    except Exception as exc:
        return {
            "ref": "refs/opentraces/local/events/v1",
            "exists": False,
            "head": None,
            "batch_count": 0,
            "event_count": 0,
            "batch_parents_linear": False,
            "content_hashes_valid": False,
            "event_chain_valid": False,
            "state": "error",
            "errors": [str(exc)],
        }


# --- post-commit hook panel (plan 047) ------------------------------------

def _post_commit_hook_status(cwd: Path) -> dict[str, Any]:
    """Report on the post-commit correlator: hook installed, last run
    from the hook log, recent candidate counts, notes-ref reachable."""
    import json as _json
    import subprocess as _sp

    hook_file = cwd / ".git" / "hooks" / "opentraces-post-commit"
    chained = cwd / ".git" / "hooks" / "post-commit"
    log_path = cwd / ".git" / "opentraces-hook.log"

    installed = hook_file.is_file()
    chained_in = False
    if chained.is_file():
        try:
            chained_in = "opentraces-post-commit" in chained.read_text()
        except OSError:
            chained_in = False

    last_entry: dict[str, Any] | None = None
    recent_runs = 0
    if log_path.is_file():
        try:
            # Read tail (last 64 KiB) so huge logs don't blow up doctor.
            raw = log_path.read_bytes()[-65536:]
            for line in reversed(raw.decode("utf-8", "replace").splitlines()):
                if not line.strip():
                    continue
                try:
                    rec = _json.loads(line)
                except ValueError:
                    continue
                if last_entry is None:
                    last_entry = rec
                recent_runs += 1
                if recent_runs >= 20:
                    break
        except OSError:
            pass

    notes_reachable = False
    try:
        res = _sp.run(
            ["git", "show-ref", "--verify", "refs/notes/opentraces"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        notes_reachable = res.returncode == 0
    except (FileNotFoundError, OSError):
        notes_reachable = False

    if installed and chained_in and last_entry is not None:
        state = "ok"
    elif installed and chained_in:
        state = "installed_never_ran"
    elif installed and not chained_in:
        state = "installed_not_chained"
    else:
        state = "missing"

    last_trail_anchors_created = None
    last_trail_anchor_error = None
    if last_entry is not None:
        last_trail_anchors_created = last_entry.get("trail_anchors_created")
        last_trail_anchor_error = last_entry.get("trail_anchor_error")

    return {
        "state": state,
        "installed": installed,
        "chained_in_post_commit": chained_in,
        "log_path": str(log_path) if log_path.is_file() else None,
        "recent_runs": recent_runs,
        "last_run": last_entry,
        "last_trail_anchors_created": last_trail_anchors_created,
        "last_trail_anchor_error": last_trail_anchor_error,
        "notes_ref_reachable": notes_reachable,
    }


# --- attribution panel (plan 043 phase 7) ---------------------------------

def _attribution_status(cwd: Path) -> dict[str, Any]:
    """Report on the attribution cache + last backfill for this project.

    Returns a panel with cached_commits, last_backfilled_commit,
    last_backfill_at, first_run_backfill_decision, and a health label.
    """
    import datetime as _dt

    from .cache import AttributionCache
    from .config import (
        get_first_run_backfill_decision,
        get_project_state_path,
        _project_slug_for,
    )
    from .state import StateManager

    if not project_is_opted_in(cwd):
        return {
            "project_slug": None,
            "project_root_sha": None,
            "attribution_cache_dir": None,
            "cached_commits": 0,
            "last_backfilled_commit": None,
            "last_backfill_at": None,
            "first_run_backfill_decision": None,
            "coverage_estimate": None,
            "health": "no-project",
        }

    try:
        slug = _project_slug_for(cwd)
    except Exception:
        slug = None

    cache = AttributionCache(cwd)
    cached = cache.list_attributed_shas()

    try:
        sm = StateManager(state_path=get_project_state_path(cwd))
        last_sha = sm.get_last_backfilled_commit()
        last_at = sm.get_last_backfill_at()
    except Exception:
        last_sha, last_at = None, None

    try:
        decision = get_first_run_backfill_decision(cwd)
    except Exception:
        decision = None

    try:
        from .repo_identity import root_commit_sha
        root_sha = root_commit_sha(cwd)
    except Exception:
        root_sha = None

    # Watcher-aware staleness: if the watcher is running, missed ticks are
    # the watcher's problem, not ours. If no watcher + last_at > 24h old,
    # warn.
    health = "ok"
    if not cached:
        health = "empty"
    elif last_at:
        try:
            when = _dt.datetime.fromisoformat(last_at.replace("Z", "+00:00"))
            now = _dt.datetime.now(_dt.timezone.utc)
            age = (now - when).total_seconds()
            if age > 86400:
                try:
                    from ..watcher import installer as _wi
                    st = _wi.status()
                    running = bool(st.running)
                except Exception:
                    running = False
                if not running:
                    health = "stale"
        except ValueError:
            pass

    return {
        "project_slug": slug,
        "project_root_sha": root_sha,
        "attribution_cache_dir": str(cache.root),
        "cached_commits": len(cached),
        "last_backfilled_commit": last_sha,
        "last_backfill_at": last_at,
        "first_run_backfill_decision": decision,
        "coverage_estimate": None,
        "health": health,
    }


def _watcher_status() -> dict[str, Any]:
    """Report on watcher installation + running state."""
    try:
        from ..watcher import installer as _wi
    except Exception:
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }

    try:
        st = _wi.status()
    except RuntimeError:
        # current_platform() raises on Windows etc.
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }
    except Exception:
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }

    if not st.installed:
        health = "not-installed"
    elif not st.running:
        health = "not-running"
    else:
        health = "ok"
        # stale-heartbeat check: if last_run_at exists and is older than
        # 3x interval, flag as stale-heartbeat
        if st.last_run_at and st.interval_seconds:
            import datetime as _dt
            try:
                age = (_dt.datetime.now(_dt.timezone.utc) - st.last_run_at).total_seconds()
                if age > 3 * st.interval_seconds:
                    health = "stale-heartbeat"
            except Exception:
                pass

    return {
        "platform": st.platform,
        "installed": bool(st.installed),
        "running": bool(st.running),
        "last_run_at": st.last_run_at.isoformat() if st.last_run_at else None,
        "interval_seconds": st.interval_seconds,
        "unit_path": str(st.unit_path) if st.unit_path else None,
        "health": health,
    }


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
    - ``blocks``: does this tier hard-block upload on finding
    """
    # TruffleHog
    th_enabled = cfg.security.trufflehog.enabled
    if not th_enabled:
        th_state, th_detail = "disabled", None
    elif trufflehog_version is None:
        th_state = "missing"
        th_detail = "binary not found; run 'opentraces setup trufflehog --enable'"
    else:
        th_state = "enabled"
        th_detail = f"{trufflehog_version} — redacts findings and forces review"

    # LLM trace review
    rl_enabled = bool(review_llm.get("enabled"))
    rl_reachable = review_llm.get("reachable")
    if not rl_enabled:
        rl_state, rl_detail = "disabled", None
    elif rl_reachable is False:
        rl_state = "unreachable"
        rl_detail = review_llm.get("status") or "endpoint unreachable"
    else:
        rl_state = "on-demand"
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
            "blocks": False,
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
            "base_url": review_llm.get("base_url"),
            "api_key_env": review_llm.get("api_key_env"),
            "probe_status": review_llm.get("status"),
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
        return "ENABLED-BUT-MISSING — run 'opentraces setup trufflehog --enable'"
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
        "attribution": _attribution_status(cwd),
        "watcher": _watcher_status(),
        "hooks": _hook_installers(),
        "trail_event_log": _trail_event_log_status(cwd),
        "post_commit_hook": _post_commit_hook_status(cwd),
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
    if (report_data.get("trail_event_log") or {}).get("state") in ("invalid", "error"):
        return 3
    return 0
