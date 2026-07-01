"""``opentraces setup llm-review`` + the ``llm-review`` reviewer command.

Extracted from the ``installers`` god module (cli/setup decomposition): the
optional LLM dataset-row reviewer — the ``setup llm-review`` configurator
(``@setup_group.command``) and the ``review_llm_cmd`` runner
(``@main.command("llm-review")``, dropped from the root by cli/__init__'s
legacy-root cleanup, same as before) plus their model-probe / verdict helpers.
Imported by ``cli/__init__`` (BEFORE the _drop_command loop) for the
decorator-registration side effect. One-way dep on ``installers`` (setup_group +
main); installers does not import back.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import click

import opentraces.cli as _cli
from . import main
from .installers import setup_group


# (name, base_url, api_key_env hint, suggested model, blurb)
_REVIEW_LLM_PRESETS: list[tuple[str, str, str, str, str]] = [
    ("ollama",     "http://localhost:11434/v1",       "",                "gemma3n:e4b",                   "local, no API key"),
    ("lm-studio",  "http://localhost:1234/v1",        "",                "",                              "local, no API key"),
    ("llama-cpp",  "http://localhost:8080/v1",        "",                "",                              "local, no API key (llama.cpp server)"),
    ("vllm",       "http://localhost:8000/v1",        "",                "",                              "local, no API key"),
    ("openai",     "https://api.openai.com/v1",       "OPENAI_API_KEY",  "gpt-4o-mini",                   "hosted"),
    ("groq",       "https://api.groq.com/openai/v1",  "GROQ_API_KEY",    "llama-3.3-70b-versatile",       "hosted"),
    ("openrouter", "https://openrouter.ai/api/v1",    "OPENROUTER_API_KEY", "anthropic/claude-3.5-haiku", "hosted"),
    ("together",   "https://api.together.xyz/v1",     "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "hosted"),
    ("anthropic-direct", "",                          "ANTHROPIC_API_KEY", "claude-haiku-4-5-20251001",   "hosted (native SDK, not OpenAI-compat)"),
]


_LOCAL_PRESETS = {"ollama", "lm-studio", "llama-cpp", "vllm"}


def _probe_models(base_url: str, api_key_env: str,
                  timeout: float = 5.0) -> tuple[bool, list[str], str]:
    """Ping ``{base_url}/models`` and return (ok, names, message).

    Used both by the interactive flow (to list pickable models) and by
    ``--test``. Short default timeout so an unreachable endpoint doesn't
    stall the wizard.
    """
    from ..security.llm_provider import OpenAICompatProvider

    try:
        p = OpenAICompatProvider(
            model="_probe_only", base_url=base_url,
            api_key_env=api_key_env, timeout=timeout,
        )
        result = p.ping()
        names = result.get("models") or []
        return True, names, f"{len(names)} models at {base_url}"
    except Exception as exc:
        return False, [], str(exc)


def _maybe_ollama_pull(model: str) -> bool:
    """If ``ollama`` is on PATH, run ``ollama pull <model>`` interactively.

    Returns True on success, False on failure or if the binary is
    missing. Live-streams ollama's progress so the user sees it.
    """
    import subprocess

    if shutil.which("ollama") is None:
        return False
    try:
        _cli.human_echo(f"running: ollama pull {model}")
        result = subprocess.run(["ollama", "pull", model], check=False)
        return result.returncode == 0
    except Exception as exc:
        _cli.human_echo(f"ollama pull failed: {exc}")
        return False


def _pick_model_from_list(
    preset_name: str, base_url: str, api_key_env: str, suggested: str,
) -> str:
    """Probe the endpoint and let the user pick a model.

    - Local preset + reachable: numbered picker over installed models,
      plus a "custom" option.
    - Local preset (Ollama) + chosen model not installed: offer to pull.
    - Unreachable or non-local preset: free-form prompt with the
      suggested default.
    """

    is_local = preset_name in _LOCAL_PRESETS
    if is_local:
        ok, names, message = _probe_models(base_url, api_key_env)
        if ok and names:
            _cli.human_echo("")
            _cli.human_echo(f"{_cli._dim('models available:')}")
            for i, n in enumerate(names, 1):
                marker = _cli._ok(" ← suggested") if n == suggested else ""
                _cli.human_echo(f"  {i}. {n}{marker}")
            _cli.human_echo(f"  {len(names) + 1}. {_cli._bold('custom')} {_cli._dim('(type a tag; will pull if ollama)')}")
            _cli.human_echo("")
            default_idx = str(names.index(suggested) + 1) if suggested in names else "1"
            raw = click.prompt("choose a model", default=default_idx, show_default=True)
            try:
                idx = int(raw)
            except ValueError:
                # User typed a tag directly.
                return _maybe_pull_and_return(preset_name, raw)
            if 1 <= idx <= len(names):
                return names[idx - 1]
            if idx == len(names) + 1:
                tag = click.prompt("model tag", default=suggested or "", show_default=bool(suggested))
                return _maybe_pull_and_return(preset_name, tag)
            raise click.BadParameter(f"choice out of range: {idx}")

        # Unreachable local endpoint — warn clearly, fall through.
        if is_local:
            _cli.human_echo("")
            _cli.human_echo(_cli._err(f"couldn't reach {base_url}") + f": {message}")
            if preset_name == "ollama":
                _cli.human_echo(_cli._dim("is ollama running? try: ollama serve"))
            elif preset_name == "lm-studio":
                _cli.human_echo(_cli._dim("start LM Studio's local server from the app"))
            elif preset_name == "llama-cpp":
                _cli.human_echo(_cli._dim("start llama.cpp: llama-server -m <model.gguf> --port 8080"))
            elif preset_name == "vllm":
                _cli.human_echo(_cli._dim("start vllm: vllm serve <model>"))
            _cli.human_echo("")

    tag = click.prompt("model", default=suggested or "", show_default=bool(suggested))
    return _maybe_pull_and_return(preset_name, tag)


def _maybe_pull_and_return(preset_name: str, tag: str) -> str:
    """For Ollama, offer to pull the tag if it isn't installed yet."""

    if preset_name != "ollama" or not tag:
        return tag
    ok, names, _ = _probe_models("http://localhost:11434/v1", "")
    if ok and tag in names:
        return tag
    if not ok:
        return tag  # ollama isn't up; let save + test surface the error
    # Model not installed — offer pull.
    if not click.confirm(
        f"'{tag}' is not pulled. run 'ollama pull {tag}' now?",
        default=True,
    ):
        _cli.human_echo(_cli._dim("skipped; you can pull later with: ollama pull " + tag))
        return tag
    pulled = _maybe_ollama_pull(tag)
    if pulled:
        _cli.human_echo(_cli._ok(f"pulled {tag}"))
    else:
        _cli.human_echo(_cli._err(f"pull failed; saving '{tag}' anyway"))
    return tag


def _test_review_llm(api_format: str, base_url: str, model: str, api_key_env: str,
                     timeout: float) -> tuple[bool, str]:
    """Ping the configured LLM endpoint. Returns (ok, message)."""
    from ..security.llm_provider import (
        AnthropicProvider, OpenAICompatProvider,
    )
    try:
        if api_format == "anthropic":
            if not (api_key_env and os.environ.get(api_key_env)):
                return False, f"env var {api_key_env or 'ANTHROPIC_API_KEY'} is not set"
            # Just constructing + importing the SDK is the smoke test —
            # avoids spending a real Anthropic request on every doctor call.
            AnthropicProvider(model=model, api_key=os.environ.get(api_key_env))
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return False, "'anthropic' package not installed (pip install anthropic)"
            return True, f"anthropic SDK ready for {model}"

        p = OpenAICompatProvider(
            model=model, base_url=base_url, api_key_env=api_key_env, timeout=timeout,
        )
        result = p.ping()
        names = result.get("models") or []
        if names:
            present = model in names
            tail = f"{len(names)} models at {base_url}"
            if present:
                return True, f"{tail}; '{model}' is available"
            return True, f"{tail}; WARNING: '{model}' not in list"
        return True, f"reachable at {base_url}"
    except Exception as exc:
        return False, str(exc)


def _review_llm_config_from_cfg(cfg) -> dict:
    rc = cfg.security.llm_review
    return {
        "enabled": rc.enabled,
        "api_format": rc.api_format,
        "base_url": rc.base_url,
        "model": rc.model,
        "api_key_env": rc.api_key_env,
        "timeout": rc.timeout,
        "prompt_version": rc.prompt_version,
    }


def _setup_review_llm_interactive() -> tuple[str, str, str, str, float]:
    """Walk the user through preset selection. Returns config tuple."""

    _cli.human_echo("")
    _cli.print_banner(tagline="configure review LLM")
    _cli.human_echo("")
    _cli.human_echo("This is the third-party LLM used to independently review")
    _cli.human_echo("staged traces before you push. Runs locally or against a")
    _cli.human_echo("hosted API — this config is global, not per-project.")
    _cli.human_echo("")

    for i, (name, base_url, key_env, sample, blurb) in enumerate(_REVIEW_LLM_PRESETS, 1):
        tag = _cli._dim(f"[{blurb}]")
        _cli.human_echo(f"  {i}. {_cli._bold(name):<26} {tag}")
    _cli.human_echo(f"  {len(_REVIEW_LLM_PRESETS) + 1}. {_cli._bold('custom'):<26} {_cli._dim('[enter URL and model manually]')}")
    _cli.human_echo("")

    choice_str = click.prompt(
        "choose a preset",
        default="1",
        show_default=True,
    )
    try:
        choice = int(choice_str)
    except ValueError:
        raise click.BadParameter(f"expected a number, got {choice_str!r}")

    if 1 <= choice <= len(_REVIEW_LLM_PRESETS):
        name, base_url, api_key_env, sample, _blurb = _REVIEW_LLM_PRESETS[choice - 1]
        api_format = "anthropic" if name == "anthropic-direct" else "openai-compat"
    elif choice == len(_REVIEW_LLM_PRESETS) + 1:
        name = "custom"
        api_format = click.prompt(
            "api format", default="openai-compat", show_default=True,
            type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"]),
        )
        base_url = click.prompt("base URL (empty for anthropic)", default="", show_default=False)
        api_key_env = click.prompt("API key env var name (empty for local)", default="", show_default=False)
        sample = ""
    else:
        raise click.BadParameter(f"choice out of range: {choice}")

    if api_format == "anthropic":
        model = click.prompt("model", default=sample or "claude-haiku-4-5-20251001",
                             show_default=True)
    else:
        model = _pick_model_from_list(name, base_url, api_key_env, sample)

    timeout_str = click.prompt("timeout seconds", default="120", show_default=True)
    try:
        timeout = float(timeout_str)
    except ValueError:
        timeout = 120.0

    return api_format, base_url, model, api_key_env, timeout


@setup_group.command("llm-review")
@click.option("--api-format", "api_format", default=None,
              type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"], case_sensitive=False),
              help="Wire protocol the local client speaks: openai-compat "
                   "(default; vLLM/LM Studio/Ollama-via-/v1/OpenAI proper), "
                   "ollama (native /api/generate), anthropic, fake.")
@click.option("--base-url", default=None,
              help="Base URL including /v1 for openai-compat servers. "
                   "Ignored for anthropic.")
@click.option("--model", default=None, help="Model name/tag.")
@click.option("--api-key-env", default=None,
              help="Env var holding the API key. Empty for local servers.")
@click.option("--timeout", default=None, type=float, help="Request timeout (s).")
@click.option("--disable", is_flag=True, help="Turn llm-review off without changing other fields.")
@click.option("--enable", is_flag=True, help="Turn llm-review on using current config.")
@click.option("--test", "test_only", is_flag=True,
              help="Ping the endpoint; do not write config.")
@click.option("--print", "print_only", is_flag=True,
              help="Print effective config as JSON and exit.")
@click.option("--no-interactive", is_flag=True,
              help="Skip the preset picker even if no flags are given.")
@click.option("--project", "scope_project", is_flag=True,
              help="Scope this change to the project's marker (default: global config).")
def setup_review_llm_cmd(
    api_format: str | None, base_url: str | None, model: str | None,
    api_key_env: str | None, timeout: float | None,
    disable: bool, enable: bool, test_only: bool, print_only: bool,
    no_interactive: bool,
    scope_project: bool = False,
) -> None:
    """Configure the optional LLM dataset-row reviewer for publication gates.

    Points opentraces at an OpenAI-compatible, Ollama, Anthropic, or
    fake backend that can review outgoing dataset rows and flag residual
    sensitive content explicit sanitize tools could miss (semantic PII,
    proprietary context, policy concerns).

    Stored globally in ~/.opentraces/config.json under
    security.llm_review. One config per machine, projects inherit it.

    Interactive picker when run with no flags. Non-interactive for agents:

    \b
        opentraces setup llm-review --api-format openai-compat \\
            --base-url http://localhost:11434/v1 --model gemma3n:e4b
        opentraces setup llm-review --api-format openai-compat \\
            --base-url https://api.groq.com/openai/v1 \\
            --model llama-3.3-70b-versatile --api-key-env GROQ_API_KEY
        opentraces setup llm-review --api-format anthropic \\
            --model claude-haiku-4-5-20251001 --api-key-env ANTHROPIC_API_KEY
    """
    cfg = _cli.load_config()
    rc = cfg.security.llm_review

    if print_only:
        _cli.emit_json({"status": "ok", "llm_review": _review_llm_config_from_cfg(cfg)})
        return

    if disable:
        rc.enabled = False
        _cli.save_config(cfg)
        _cli.human_echo("llm-review disabled.")
        _cli.emit_json({"status": "ok", "action": "disable",
                   "llm_review": _review_llm_config_from_cfg(cfg)})
        return

    # Agent / non-interactive path: any flag provided => skip the wizard.
    any_flag = any(v is not None for v in (api_format, base_url, model, api_key_env, timeout))

    if not any_flag and not enable and not test_only and not no_interactive:
        # ADR-0007 lint L2: the preset picker is interactive. Under --json /
        # non-TTY, refuse with a structured error instead of prompting.
        _cli.require_interactive(
            "setup llm-review",
            "pass --enable with --base-url/--model (see --help), or --no-interactive",
        )
        api_format, base_url, model, api_key_env, timeout = _setup_review_llm_interactive()

    # Layer flag overrides on top of current config.
    eff_api_format = api_format or rc.api_format
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_model = model or rc.model
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = timeout if timeout is not None else rc.timeout

    if test_only:
        ok, message = _test_review_llm(
            eff_api_format, eff_base_url, eff_model, eff_api_key_env, eff_timeout,
        )
        _cli.human_echo(f"llm-review test: {'ok' if ok else 'failed'} — {message}")
        _cli.emit_json({
            "status": "ok" if ok else "error",
            "action": "test",
            "llm_review": {
                "api_format": eff_api_format, "base_url": eff_base_url,
                "model": eff_model, "api_key_env": eff_api_key_env,
            },
            "reachable": ok, "message": message,
        })
        if not ok:
            sys.exit(3)
        return

    rc.api_format = eff_api_format
    rc.base_url = eff_base_url
    rc.model = eff_model
    rc.api_key_env = eff_api_key_env
    rc.timeout = eff_timeout
    rc.enabled = True
    _cli.save_config(cfg)

    ok, message = _test_review_llm(
        rc.api_format, rc.base_url, rc.model, rc.api_key_env, rc.timeout,
    )
    _cli.human_echo("")
    tag = _cli._ok("llm-review configured") if ok else _cli._err("llm-review saved but unreachable")
    _cli.print_banner(tagline=tag)
    _cli.human_echo(f"  {_cli._dim('api format:')} {rc.api_format}")
    if rc.api_format != "anthropic":
        _cli.human_echo(f"  {_cli._dim('base url:  ')} {rc.base_url}")
    _cli.human_echo(f"  {_cli._dim('model:     ')} {rc.model}")
    if rc.api_key_env:
        present = "set" if os.environ.get(rc.api_key_env) else _cli._err("NOT SET")
        _cli.human_echo(f"  {_cli._dim('api key:   ')} ${rc.api_key_env} ({present})")
    _cli.human_echo(f"  {_cli._dim('reachable: ')} {message}")
    _cli.human_echo("")
    _cli.human_echo(f"  {_cli._bold('To run:')} opentraces dataset publish <name> --check-only")
    _cli.human_echo(f"  {_cli._dim('scope:')}         dataset publication gates; upload remains explicit")
    _cli.human_echo(f"  {_cli._dim('disable:')}       opentraces setup llm-review --disable")
    _cli.human_echo(f"  {_cli._dim('health check:')}  opentraces doctor")

    _cli.emit_json({
        "status": "ok", "action": "install",
        "llm_review": _review_llm_config_from_cfg(cfg),
        "reachable": ok, "message": message,
    })


def _filter_by_scope(records: list[dict], scope: str, state) -> list[dict]:
    """Filter records by visible stage from the StateManager.

    ``scope`` values (display vocabulary):
      - ``all``: every record in the staging directory (default)
      - ``inbox``: pre-add traces still awaiting review
      - ``staged``: post-add traces ready to push (second line of defence
        before push, after human review)
    """
    from ..core.state import TraceStatus

    if scope == "all":
        return records
    target = {
        "inbox": TraceStatus.STAGED.value,
        "staged": TraceStatus.COMMITTED.value,
    }.get(scope)
    if target is None:
        return records
    out: list[dict] = []
    for rec in records:
        entry = state.get_trace(rec.get("trace_id", ""))
        status = None
        if entry is not None:
            status = (
                entry.get("status") if isinstance(entry, dict)
                else getattr(entry, "status", None)
            )
        if status == target:
            out.append(rec)
    return out


def _filter_by_trace_ids(records: list[dict],
                         trace_ids: tuple[str, ...]) -> list[dict]:
    """Select records matching any of ``trace_ids`` (full id or short prefix).

    Prefix matching mirrors ``resume`` / ``blame`` so users can pass
    ``--trace 8a3f1c`` without the full sha.
    """
    if not trace_ids:
        return records
    wanted = [t.strip() for t in trace_ids if t.strip()]
    out: list[dict] = []
    matched: set[str] = set()
    for rec in records:
        tid = rec.get("trace_id", "") or ""
        for prefix in wanted:
            if tid == prefix or tid.startswith(prefix):
                out.append(rec)
                matched.add(prefix)
                break
    unmatched = [p for p in wanted if p not in matched]
    if unmatched:
        _cli.human_hint(f"no matching trace for: {', '.join(unmatched)}")
    return out


def _persist_llm_verdicts(staging_dir: Path, outcome, state) -> None:
    """Write each verdict back into its trace's ``metadata.llm_review``
    so later publish gates can see them.

    Verdicts that flag the trace (``shareable=no`` or
    ``missed_sensitive_data=yes``) also promote the trace to the
    BLOCKED state — the publish flow skips BLOCKED traces entirely so
    the trace does not silently fail the gate on every publish attempt.
    """
    import json as _json

    for result in outcome.results:
        tid = result.get("trace_id")
        verdict = result.get("verdict") or {}
        if not tid or not verdict:
            continue
        jsonl = staging_dir / f"{tid}.jsonl"
        if not jsonl.exists():
            # Fallback: scan the dir for a file whose first-line
            # ``trace_id`` matches. Covers any non-canonical layout.
            for f in staging_dir.glob("*.jsonl"):
                try:
                    head = f.read_text().strip().splitlines()
                    if head and _json.loads(head[0]).get("trace_id") == tid:
                        jsonl = f
                        break
                except Exception:
                    continue
            else:
                continue
        try:
            raw = jsonl.read_text().strip().splitlines()
            if not raw:
                continue
            rec = _json.loads(raw[0])
            meta = rec.setdefault("metadata", {})
            # Merge so any unrelated metadata keys survive untouched.
            meta["llm_review"] = verdict
            jsonl.write_text(_json.dumps(rec) + "\n")
        except Exception as exc:
            _cli.human_hint(f"could not persist verdict for {tid}: {exc}")
            continue

        if verdict.get("shareable") == "no" or \
                verdict.get("missed_sensitive_data") == "yes":
            reason = verdict.get("summary") or "flagged by LLM review"
            try:
                state.block_trace(tid, f"llm-review: {reason}")
            except Exception as exc:
                _cli.human_hint(f"could not mark {tid} blocked: {exc}")


@main.command(
    "llm-review",
    examples=[
        "opentraces llm-review                      # every trace in staging",
        "opentraces llm-review --scope staged       # 2nd line of defence before push",
        "opentraces llm-review --scope inbox        # pre-add only",
        "opentraces llm-review --trace 8a3f1c       # one trace (short id ok)",
        "opentraces llm-review --dry-run            # estimate token usage only",
    ],
    see_also=[
        ("opentraces setup llm-review", "configure the LLM"),
        ("opentraces dataset publish <name> --check-only", "run publication gates without upload"),
    ],
    option_groups=[
        ("API overrides", ["api_format", "model", "base_url", "api_key_env"]),
        ("Selection", ["scope", "trace_ids", "limit"]),
        ("Run", ["dry_run", "force", "context_file"]),
    ],
)
@click.option("--api-format", "api_format", default=None,
              type=click.Choice(["openai-compat", "ollama", "anthropic", "fake"], case_sensitive=False),
              help="Override the wire-protocol family (openai-compat, ollama, anthropic, fake)")
@click.option("--model", default=None, help="Override model")
@click.option("--base-url", default=None,
              help="Override base URL for openai-compat servers")
@click.option("--api-key-env", default=None,
              help="Override the env var holding the API key")
@click.option("--scope",
              type=click.Choice(["all", "inbox", "staged"], case_sensitive=False),
              default="all",
              help="Which traces to review: 'all' (every trace in staging; default), "
                   "'inbox' (Inbox-stage only, pre-add), "
                   "'staged' (Staged-stage only, second line of defence before push).")
@click.option("--trace", "trace_ids", multiple=True,
              help="Target specific trace(s) by id (full or short prefix). "
                   "Repeatable. Overrides --scope when set.")
@click.option("--dry-run", is_flag=True,
              help="Estimate token usage without calling the provider")
@click.option("--limit", type=int, default=0,
              help="Cap the batch at N traces (0 = no cap). Applied after --scope / --trace filtering.")
@click.option("--force", is_flag=True,
              help="Re-review traces that already have a cached verdict")
@click.option("--context-file", "context_file", type=click.Path(exists=True, dir_okay=False),
              default=None, help="Project README/AGENTS.md passed as context")
def review_llm_cmd(api_format: str | None, model: str | None, base_url: str | None,
                   api_key_env: str | None, scope: str,
                   trace_ids: tuple[str, ...], dry_run: bool, limit: int,
                   force: bool, context_file: str | None) -> None:
    """Run Tier 2 LLM semantic review.

    Uses the LLM configured by 'opentraces setup llm-review' unless you
    override via --api-format / --model / --base-url / --api-key-env.

    LLM can be slow if using local models. Narrow with --scope (pick
    inbox or staged only) or --trace (one or more specific trace ids),
    and cap with --limit. The typical "second line of defence" flow is
    'llm-review --scope staged' right before 'push --llm-review'.
    """
    from ..core.config import get_project_traces_dir, get_project_state_path
    from ..core.inbox import load_traces
    from ..core.review import estimate_llm_review, run_llm_review
    from ..core.state import StateManager

    cfg = _cli.load_config()
    rc = cfg.security.llm_review
    eff_api_format = api_format or rc.api_format
    eff_model = model or rc.model
    eff_base_url = base_url if base_url is not None else rc.base_url
    eff_api_key_env = api_key_env if api_key_env is not None else rc.api_key_env
    eff_timeout = rc.timeout

    if not rc.enabled and api_format is None and model is None:
        _cli.human_hint(
            "llm-review is not configured. Run 'opentraces setup llm-review' "
            "once, or pass --api-format/--model explicitly."
        )

    staging = get_project_traces_dir(Path.cwd())
    if not staging.exists():
        _cli.human_echo("No staging directory found. Run opentraces init first.")
        _cli.emit_json(_cli.error_response(
            "NO_STAGING", "review", "staging dir missing",
            "Run 'opentraces init'.",
        ))
        sys.exit(2)

    records: list[dict] = list(load_traces(staging))
    total_available = len(records)

    if trace_ids:
        records = _filter_by_trace_ids(records, trace_ids)
        filter_desc = f"--trace {','.join(trace_ids)}"
    else:
        state_mgr = StateManager(get_project_state_path(Path.cwd()))
        records = _filter_by_scope(records, scope, state_mgr)
        filter_desc = f"--scope {scope}"

    if limit > 0:
        records = records[:limit]

    if not records:
        _cli.human_echo(
            f"No traces match {filter_desc}"
            + (f" (limit {limit})" if limit else "")
            + f" — {total_available} trace(s) in staging."
        )
        payload: dict = {
            "status": "ok", "action": "llm-review",
            "scope": scope, "trace_ids": list(trace_ids),
            "matched": 0, "total_available": total_available,
        }
        if dry_run:
            payload.update({
                "dry_run": True, "sessions": 0, "chars": 0,
                "estimate": {"tokens": 0, "cost_usd": 0.0},
                "model": eff_model, "api_format": eff_api_format,
                "base_url": eff_base_url,
            })
        else:
            payload["results"] = []
        _cli.emit_json(payload)
        return

    _cli.human_echo(
        f"{_cli._dim(filter_desc + ':')} "
        f"{len(records)}/{total_available} trace(s) selected"
        + (f" (limit {limit})" if limit else "")
    )

    context = ""
    if context_file:
        try:
            context = Path(context_file).read_text()[:10_000]
        except OSError as exc:
            _cli.human_echo(f"Could not read context file: {exc}")
            sys.exit(2)

    if dry_run:
        est = estimate_llm_review(records, api_format=eff_api_format, model=eff_model)
        _cli.human_echo(
            f"Dry run: {est.sessions} sessions, ~{est.chars:,} chars, "
            f"~{est.tokens:,} tokens, ~${est.cost_usd:.4f}."
        )
        _cli.emit_json({
            "status": "ok",
            "action": "llm-review",
            "dry_run": True,
            "scope": scope,
            "trace_ids": list(trace_ids),
            "matched": len(records),
            "total_available": total_available,
            "sessions": est.sessions,
            "chars": est.chars,
            "estimate": {"tokens": est.tokens, "cost_usd": est.cost_usd},
            "model": eff_model,
            "api_format": eff_api_format,
            "base_url": eff_base_url,
        })
        return

    n = len(records)
    _counter = {"i": 0}

    def _progress(trace_id: str, status: str) -> None:
        _counter["i"] += 1
        _cli.human_echo(f"[{_counter['i']}/{n}] {trace_id}: {status}")

    outcome = run_llm_review(
        records,
        api_format=eff_api_format,
        model=eff_model,
        base_url=eff_base_url,
        api_key_env=eff_api_key_env,
        timeout=eff_timeout,
        prompt_version=rc.prompt_version,
        context=context,
        force=force,
        on_progress=_progress,
    )
    # Persist verdicts so downstream publish gates can see them. Without
    # this the verdict only lives in the JSON payload we emit below and is
    # lost as soon as the command exits. Bad verdicts also mark the trace
    # BLOCKED in state so the publish flow skips them and they stay flagged.
    state_for_block = StateManager(get_project_state_path(Path.cwd()))
    _persist_llm_verdicts(staging, outcome, state_for_block)
    _cli.emit_json({
        "status": "ok",
        "action": "llm-review",
        "dry_run": False,
        "scope": scope,
        "trace_ids": list(trace_ids),
        "matched": len(records),
        "total_available": total_available,
        "api_format": eff_api_format,
        "model": eff_model,
        "base_url": eff_base_url,
        "results": outcome.results,
    })
