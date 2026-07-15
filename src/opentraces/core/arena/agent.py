"""Run-scoped real-harness adapter over the bounded agent terminal drive."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .box import (
    Box,
    PRODUCT_ENV_DENY_EXACT,
    PRODUCT_ENV_DENY_PREFIXES,
    PRODUCT_USER,
)
from .diagnostics import sanitize_diagnostic_value
from .drives.actions import RunActionSequence
from .drives.agent import (
    AgentTerminalDrive,
    AgentTerminalSessionFactory,
    TermctrlAgentSession,
)
from .drives.browser_mcp import BrowserMcpBridge, SERVER_NAME
from .harnesses import (
    CLAUDE_HARNESS_EXECUTABLE,
    CLAUDE_HARNESS_NAME,
    CLAUDE_HARNESS_VERSION,
)
from .run_store import RunDraft


ATTEMPT_ARTIFACT_REF = "artifacts/agent-attempt.json"
ATTEMPT_COMPLETE_MARKER = "OPENTRACES_AGENT_ATTEMPT_COMPLETE"
_VERSION_RE = re.compile(r"^OPENTRACES_HARNESS_VERSION=(.+)$", re.MULTILINE)
_CONTROLLER_ENV = frozenset(
    {
        "OPENTRACES_HF_CONTROL_PAYLOAD",
        "OPENTRACES_HF_CONTROL_TOKEN",
        "OPENTRACES_HF_CONTROL_URL",
    }
)


@dataclass(frozen=True)
class HarnessSpec:
    name: str
    executable: str
    version: str
    surface_tools: Mapping[str, tuple[str, ...]]


_HARNESSES = {
    "claude": HarnessSpec(
        name=CLAUDE_HARNESS_NAME,
        executable=CLAUDE_HARNESS_EXECUTABLE,
        version=CLAUDE_HARNESS_VERSION,
        surface_tools={
            "terminal": ("Bash", "Read", "Edit", "Write", "Glob", "Grep"),
            "browser": (f"mcp__{SERVER_NAME}__*",),
        },
    )
}


@dataclass(frozen=True)
class AgentAttempt:
    """Machinery outcome for one harness invocation, never its product verdict."""

    completed: bool
    failure: dict[str, str] | None
    artifact_ref: str
    action_refs: tuple[str, ...]
    recording_refs: tuple[str, ...]
    recording_complete: bool


ProductEnvironment = Callable[[], Mapping[str, str]]
ReplayInference = Callable[[], object | None]


class AgentDrive:
    """Grant run-owned surfaces to one registered real harness attempt."""

    def __init__(
        self,
        *,
        box: Box,
        draft: RunDraft,
        actions: RunActionSequence,
        terminal: object,
        browser: object,
        execution_mode: str,
        product_environment: ProductEnvironment,
        replay_inference: ReplayInference = lambda: None,
        session_factory: AgentTerminalSessionFactory = TermctrlAgentSession,
        poll_interval: float = 0.3,
    ) -> None:
        self.box = box
        self.draft = draft
        self.actions = actions
        self.terminal = terminal
        self.browser = browser
        self.execution_mode = execution_mode
        self.product_environment = product_environment
        self.replay_inference = replay_inference
        self.session_factory = session_factory
        self.poll_interval = poll_interval
        self._attempted = False
        self._attempt: AgentAttempt | None = None
        self._harness_pin: dict[str, Any] | None = None
        self._inference_pin: dict[str, Any] | None = None

    @property
    def has_actions(self) -> bool:
        return self._attempt is not None

    @property
    def harness_pin(self) -> dict[str, Any] | None:
        return self._harness_pin

    @property
    def inference_pin(self) -> dict[str, Any] | None:
        return self._inference_pin

    @property
    def attempt_result(self) -> AgentAttempt | None:
        return self._attempt

    def _grants(self, access: Sequence[object]) -> list[str]:
        if not isinstance(access, (list, tuple)) or not access:
            raise ValueError("agent access requires at least one run-owned surface")
        grants: list[str] = []
        for candidate in access:
            if candidate is self.terminal:
                name = "terminal"
            elif candidate is self.browser:
                name = "browser"
            else:
                raise ValueError(
                    "agent access accepts only this run's run-owned terminal/browser objects"
                )
            if name in grants:
                raise ValueError(f"duplicate access surface: {name}")
            grants.append(name)
        return grants

    def _inference(self, inference: object) -> tuple[dict[str, Any], dict[str, str]]:
        if self.execution_mode == "direct":
            raise ValueError("run.agent requires agent_live or agent_replay execution mode")
        if self.execution_mode == "agent_live":
            if inference != "live":
                raise ValueError("agent_live requires inference='live'")
            return {"mode": "live"}, {}
        if self.execution_mode != "agent_replay":
            raise ValueError("run.agent requires agent_live or agent_replay execution mode")
        if inference == "live" or isinstance(inference, str):
            raise ValueError("agent_replay requires a model-wire inference object")
        if inference is not self.replay_inference():
            raise ValueError("agent_replay requires the exact run-owned Anthropic replay emulator")
        pin = getattr(inference, "pin", None)
        if not isinstance(pin, Mapping):
            raise ValueError("agent_replay inference must expose a model-wire pin")
        environment = getattr(inference, "env", {})
        if not isinstance(environment, Mapping):
            raise ValueError("agent_replay inference environment must be a mapping")
        return (
            {"mode": "replay", **{str(key): value for key, value in pin.items()}},
            {str(key): str(value) for key, value in environment.items()},
        )

    @staticmethod
    def _environment(product: Mapping[str, str], inference: Mapping[str, str]) -> dict[str, str]:
        environment = {str(key): str(value) for key, value in product.items()}
        for key, value in inference.items():
            if key in environment and environment[key] != value:
                raise ValueError(f"agent environment disagrees on {key}")
            environment[key] = value
        invalid = [
            name for name in environment if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
        ]
        if invalid:
            raise ValueError(f"invalid agent environment name: {invalid[0]!r}")
        unsafe = sorted(
            name
            for name in environment
            if name in PRODUCT_ENV_DENY_EXACT or name.startswith(PRODUCT_ENV_DENY_PREFIXES)
        )
        if unsafe:
            raise ValueError(f"unsafe agent product environment: {unsafe[0]!r}")
        forbidden = sorted(_CONTROLLER_ENV.intersection(environment))
        if forbidden:
            raise ValueError(
                "agent harness refuses controller-only environment: " + ", ".join(forbidden)
            )
        return environment

    @staticmethod
    def _harness_argv(
        spec: HarnessSpec,
        grants: Sequence[str],
        environment: Mapping[str, str],
        mcp_config: Mapping[str, Any],
    ) -> list[str]:
        allowed = [tool for grant in grants for tool in spec.surface_tools[grant]]
        denied = [
            tool
            for surface, tools in spec.surface_tools.items()
            if surface not in grants
            for tool in tools
        ]
        sudo = ["/usr/bin/sudo", "-H", "-n", "-u", PRODUCT_USER]
        if environment:
            sudo.append("--preserve-env=" + ",".join(sorted(environment)))
        sudo.append("--")
        encoded_config = base64.b64encode(
            json.dumps(mcp_config, sort_keys=True, separators=(",", ":")).encode()
        ).decode()
        wrapper = (
            'set -eu; executable="$1"; expected="$2"; encoded_config="$3"; '
            'mcp_config="$4"; shift 4; '
            "trap 'rm -f \"$mcp_config\"' EXIT HUP INT TERM; "
            'printf "%s" "$encoded_config" | base64 -d > "$mcp_config"; '
            'version=$("$executable" --version); '
            'printf "OPENTRACES_HARNESS_VERSION=%s\\n" "$version"; '
            'observed=${version%% *}; test "$observed" = "$expected"; '
            '"$executable" "$@"'
        )
        argv = [
            *sudo,
            "/bin/sh",
            "-c",
            wrapper,
            "opentraces-agent",
            spec.executable,
            spec.version,
            encoded_config,
            "/tmp/opentraces-agent-mcp.json",
            "--mcp-config",
            "/tmp/opentraces-agent-mcp.json",
            "--strict-mcp-config",
            "--allowedTools",
            *allowed,
        ]
        if denied:
            argv.extend(["--disallowedTools", *denied])
        argv.extend(["--permission-mode", "bypassPermissions"])
        return argv

    def attempt(
        self,
        *,
        harness: str,
        task: str,
        access: Sequence[object],
        inference: object,
    ) -> AgentAttempt:
        """Run the sole v0 attempt; independent verifiers still decide verdict."""

        if self._attempted:
            raise RuntimeError("bench.v0 permits one agent attempt per run")
        spec = _HARNESSES.get(harness)
        if spec is None:
            raise ValueError(f"unknown agent harness {harness!r}")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("agent task must be a non-empty string")
        grants = self._grants(access)
        inference_pin, inference_environment = self._inference(inference)
        environment = self._environment(self.product_environment(), inference_environment)
        self._attempted = True

        browser_bridge: BrowserMcpBridge | None = None
        reverse_forwards: tuple[tuple[int, int], ...] = ()
        if "browser" in grants:
            browser_bridge = BrowserMcpBridge(self.browser)
            browser_bridge.start()
            mcp_config = browser_bridge.config()
            reverse_forwards = ((browser_bridge.remote_port, browser_bridge.local_port),)
        else:
            mcp_config = {"mcpServers": {}}
        argv = self._harness_argv(spec, grants, environment, mcp_config)

        low_level = AgentTerminalDrive(
            box=self.box,
            draft=self.draft,
            actions=self.actions,
            session_factory=self.session_factory,
            poll_interval=self.poll_interval,
        )
        prompt = (
            f"{task.rstrip()}\n\n"
            "This completion marker is machinery only, not evidence that the task worked. "
            f"When your attempt is finished, print exactly {ATTEMPT_COMPLETE_MARKER}."
        )
        try:
            observed = low_level.run(
                harness_argv=argv,
                prompt=prompt,
                expect_regex=rf"(?m)^{ATTEMPT_COMPLETE_MARKER}$",
                timeout=180,
                env=environment,
                reverse_forwards=reverse_forwards,
            )
        finally:
            if browser_bridge is not None:
                browser_bridge.close()
        transcript = (self.draft.path / observed.transcript_ref).read_text(encoding="utf-8")
        matched_version = _VERSION_RE.search(transcript)
        failure = observed.reason
        if observed.status == "pass" and matched_version is None:
            failure = {
                "code": "agent_harness_version_unobserved",
                "message": "agent harness version was not observed in its retained transcript",
            }
        observed_version = (
            matched_version.group(1).strip().split(maxsplit=1)[0]
            if matched_version is not None
            else None
        )
        if observed_version not in {None, spec.version}:
            failure = {
                "code": "agent_harness_version_mismatch",
                "message": (
                    f"expected Claude Code {spec.version}, observed {observed_version}"
                ),
            }
        completed = observed.status == "pass" and failure is None
        version = (
            str(sanitize_diagnostic_value(observed_version))
            if observed_version is not None
            else None
        )
        harness_pin = {
            "name": spec.name,
            "executable": spec.executable,
            "version": version,
        }
        record = {
            "schema_version": "opentraces.bench.agent-attempt.v0",
            "task": task,
            "granted_surfaces": grants,
            "harness": harness_pin,
            "inference": inference_pin,
            "action_refs": [observed.invocation_ref.rsplit("/", 1)[0]],
            "recording_refs": [observed.recording_ref],
            "completed": completed,
            "failure": failure,
        }
        self.draft.write_json(ATTEMPT_ARTIFACT_REF, record)
        self._harness_pin = harness_pin
        self._inference_pin = inference_pin
        self._attempt = AgentAttempt(
            completed=completed,
            failure=failure,
            artifact_ref=ATTEMPT_ARTIFACT_REF,
            action_refs=tuple(record["action_refs"]),
            recording_refs=tuple(record["recording_refs"]),
            recording_complete=observed.recording_complete,
        )
        return self._attempt

    def recording_summary(self) -> dict[str, Any]:
        if self._attempt is None:
            return {"rewatchable": False, "channels": []}
        recording_ref = self._attempt.recording_refs[0]
        recording_complete = self._attempt.recording_complete
        reason = (
            None
            if recording_complete
            else "agent terminal recording did not finalize cleanly"
        )
        return {
            "rewatchable": recording_complete,
            "channels": [
                {
                    "kind": "agent_terminal",
                    "complete": recording_complete,
                    "path": recording_ref if recording_complete else None,
                    "reason": reason,
                }
            ],
        }
