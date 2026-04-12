"""Model-client backends for intent summarization.

Each backend is a :class:`ModelClient`-compatible callable. The enrichment
module is backend-agnostic; these are the concrete implementations a hook
or CLI can pick from.

Current backends:
    * :func:`claude_cli_client` — shells out to the ``claude`` CLI with
      ``-p`` (headless prompt mode). Zero extra Python deps; reuses the
      same Claude install the user already has on the machine where hooks
      run.
    * :func:`null_client` — always returns empty; useful as an explicit
      no-op when ``intent.mode`` is effectively off but the code path still
      runs.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_CLI_TIMEOUT_S = 60


def claude_cli_client(model_id: str, prompt: str, *, timeout_s: int = DEFAULT_CLAUDE_CLI_TIMEOUT_S) -> str:
    """Call the local ``claude`` CLI in headless (``-p``) mode.

    Returns the CLI's stdout. Raises :class:`FileNotFoundError` if
    ``claude`` is not on ``PATH`` and :class:`subprocess.SubprocessError`
    (or a subclass) on timeout / non-zero exit. Callers should wrap in
    try/except — :func:`opentraces.enrichment.intent.enrich_intent`
    already does.
    """
    if shutil.which("claude") is None:
        raise FileNotFoundError("`claude` CLI not on PATH; cannot run hook-path intent")

    # Claude CLI accepts the prompt on stdin when passed `-p -` or directly
    # as an argv argument. We pass via argv for simplicity and rely on
    # subprocess to quote correctly. The `--model` flag selects the model.
    cmd = ["claude", "-p", prompt, "--model", model_id]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=True,
    )
    return result.stdout


def null_client(model_id: str, prompt: str) -> str:
    """No-op backend. Always returns empty string."""
    return ""


__all__ = ["DEFAULT_CLAUDE_CLI_TIMEOUT_S", "claude_cli_client", "null_client"]
