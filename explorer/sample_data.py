"""
Sample data generator for the opentraces Explorer demo.

Produces 25 realistic traces that exercise the full opentraces schema,
including conversation turns, tool calls, sub-agent steps, reasoning
blocks, attribution, and outcome signals.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

_RNG = random.Random(42)

# ---------------------------------------------------------------------------
# Reference pools
# ---------------------------------------------------------------------------

MODELS = [
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250716",
    "claude-opus-4-20250716",
    "claude-haiku-3.5-20241022",
]

AGENTS = ["claude-code", "claude-code", "claude-code", "aider", "cursor"]

ECOSYSTEMS = ["typescript", "python", "python", "ruby", "go", "rust"]

TOOLS = ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "Agent"]

TASK_TEMPLATES: list[dict[str, Any]] = [
    {"kind": "fix-bug", "desc": "Fix {component} crashing on {trigger}", "steps_range": (4, 12)},
    {"kind": "add-feature", "desc": "Add {feature} to {component}", "steps_range": (8, 20)},
    {"kind": "refactor", "desc": "Refactor {component} to use {pattern}", "steps_range": (6, 15)},
    {"kind": "write-tests", "desc": "Write unit tests for {component}", "steps_range": (5, 10)},
    {"kind": "fix-bug", "desc": "Resolve race condition in {component}", "steps_range": (6, 14)},
    {"kind": "add-feature", "desc": "Implement {feature} endpoint", "steps_range": (10, 22)},
    {"kind": "refactor", "desc": "Extract {component} into a shared module", "steps_range": (5, 12)},
    {"kind": "write-tests", "desc": "Add integration tests for {feature}", "steps_range": (7, 14)},
    {"kind": "fix-bug", "desc": "Fix memory leak in {component} worker", "steps_range": (4, 10)},
    {"kind": "add-feature", "desc": "Add pagination to {component} list view", "steps_range": (6, 14)},
]

COMPONENTS = [
    "auth service", "payment processor", "user profile", "notification engine",
    "search index", "cache layer", "API gateway", "webhook handler",
    "job scheduler", "rate limiter", "session store", "config loader",
]

FEATURES = [
    "OAuth2 login", "Stripe checkout", "real-time sync", "export CSV",
    "dark mode", "role-based access", "audit logging", "SSO integration",
    "file upload", "email verification", "two-factor auth", "webhook retry",
]

PATTERNS = [
    "dependency injection", "repository pattern", "event sourcing",
    "CQRS", "strategy pattern", "middleware chain",
]

TRIGGERS = [
    "empty input", "concurrent requests", "large payloads",
    "expired tokens", "unicode characters", "null values",
]

DEPENDENCIES: dict[str, list[str]] = {
    "typescript": ["react", "next", "prisma", "zod", "stripe", "tailwindcss", "typescript", "eslint"],
    "python": ["fastapi", "sqlalchemy", "pydantic", "celery", "redis", "pytest", "httpx"],
    "ruby": ["rails", "sidekiq", "rspec", "devise", "pundit", "pg", "redis"],
    "go": ["gin", "gorm", "cobra", "viper", "zap", "testify", "wire"],
    "rust": ["axum", "tokio", "serde", "sqlx", "tracing", "clap", "reqwest"],
}

USERNAMES = [
    "alice-dev", "bob-builder", "carol-eng", "dave-ml",
    "eve-ops", "frank-data", "grace-sec", "hank-infra",
]

FILE_PATHS: dict[str, list[str]] = {
    "typescript": [
        "src/services/auth.ts", "src/api/routes.ts", "src/models/user.ts",
        "src/middleware/rateLimit.ts", "src/utils/cache.ts", "tests/auth.test.ts",
    ],
    "python": [
        "app/services/auth.py", "app/api/routes.py", "app/models/user.py",
        "app/middleware/rate_limit.py", "app/utils/cache.py", "tests/test_auth.py",
    ],
    "ruby": [
        "app/services/auth_service.rb", "app/controllers/api_controller.rb",
        "app/models/user.rb", "app/middleware/rate_limit.rb", "spec/auth_spec.rb",
    ],
    "go": [
        "internal/auth/service.go", "internal/api/handler.go", "internal/models/user.go",
        "internal/middleware/ratelimit.go", "internal/cache/redis.go", "test/auth_test.go",
    ],
    "rust": [
        "src/services/auth.rs", "src/api/routes.rs", "src/models/user.rs",
        "src/middleware/rate_limit.rs", "src/cache.rs", "tests/auth_test.rs",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trace_id(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def _pick(pool: list) -> Any:
    return _RNG.choice(pool)


def _gen_tool_calls(n_steps: int, ecosystem: str) -> list[dict]:
    """Generate a realistic sequence of tool calls."""
    calls: list[dict] = []
    files = FILE_PATHS.get(ecosystem, FILE_PATHS["python"])
    for _ in range(n_steps):
        tool = _pick(TOOLS)
        if tool == "Bash":
            calls.append({"tool": "Bash", "input": _pick([
                "npm test", "pytest -x", "go test ./...", "cargo test",
                "git diff", "git status", "ls -la src/", "rspec",
            ]), "output_lines": _RNG.randint(2, 40)})
        elif tool == "Read":
            calls.append({"tool": "Read", "input": _pick(files), "output_lines": _RNG.randint(20, 200)})
        elif tool == "Edit":
            calls.append({"tool": "Edit", "input": _pick(files), "lines_changed": _RNG.randint(1, 30)})
        elif tool == "Write":
            calls.append({"tool": "Write", "input": _pick(files), "lines_written": _RNG.randint(10, 80)})
        elif tool == "Grep":
            calls.append({"tool": "Grep", "input": _pick(["TODO", "FIXME", "import", "async", "error"]), "matches": _RNG.randint(1, 25)})
        elif tool == "Glob":
            calls.append({"tool": "Glob", "input": "**/*.ts" if ecosystem == "typescript" else "**/*.py", "matches": _RNG.randint(3, 50)})
        elif tool == "Agent":
            calls.append({"tool": "Agent", "input": _pick([
                "Investigate failing tests", "Find related usages",
                "Check for security issues", "Refactor helper functions",
            ]), "sub_steps": _RNG.randint(3, 8)})
    return calls


def _gen_conversation(task_desc: str, tool_calls: list[dict], has_reasoning: bool) -> list[dict]:
    """Build a conversation with user, assistant, and tool messages."""
    turns: list[dict] = []

    # Initial user message
    turns.append({
        "role": "user",
        "content": task_desc,
    })

    # Reasoning block (optional)
    if has_reasoning:
        turns.append({
            "role": "assistant",
            "type": "reasoning",
            "content": (
                f"Let me think about how to approach this. The task involves {task_desc.lower()}. "
                "I should start by reading the relevant files to understand the current implementation, "
                "then identify the root cause before making changes."
            ),
        })

    # Interleave assistant messages and tool calls
    for i, tc in enumerate(tool_calls):
        if i % 3 == 0:
            turns.append({
                "role": "assistant",
                "content": _pick([
                    "Let me look at the relevant code.",
                    "I'll search for related usages.",
                    "Now I'll make the necessary changes.",
                    "Let me verify this works correctly.",
                    "I'll run the tests to confirm.",
                    "Let me check the current state.",
                ]),
            })
        turns.append({
            "role": "tool",
            "tool_name": tc["tool"],
            "input": tc["input"],
            "is_sub_agent": tc["tool"] == "Agent",
        })

    # Final assistant summary
    turns.append({
        "role": "assistant",
        "content": f"Done. I've completed the task: {task_desc.lower()}. All changes have been applied and verified.",
    })

    return turns


def _gen_attribution(ecosystem: str, n_files: int) -> list[dict]:
    """Generate attribution blocks for modified files."""
    files = FILE_PATHS.get(ecosystem, FILE_PATHS["python"])
    chosen = _RNG.sample(files, min(n_files, len(files)))
    attrs = []
    for f in chosen:
        line_start = _RNG.randint(1, 80)
        line_end = line_start + _RNG.randint(1, 30)
        attrs.append({
            "file": f,
            "lines": [line_start, line_end],
            "action": _pick(["added", "modified", "deleted"]),
        })
    return attrs


def _gen_patch_diff(attribution: list[dict]) -> str:
    """Generate a fake but realistic-looking patch diff."""
    lines = []
    for attr in attribution[:3]:
        lines.append(f"--- a/{attr['file']}")
        lines.append(f"+++ b/{attr['file']}")
        lines.append(f"@@ -{attr['lines'][0]},5 +{attr['lines'][0]},8 @@")
        if attr["action"] == "modified":
            lines.append("-  // old implementation")
            lines.append("+  // updated implementation")
            lines.append("+  // with improved error handling")
        elif attr["action"] == "added":
            lines.append("+  // new code added")
            lines.append("+  // implements the requested feature")
        elif attr["action"] == "deleted":
            lines.append("-  // removed deprecated code")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_sample_traces(n: int = 25) -> list[dict]:
    """Generate *n* realistic sample traces for demo purposes."""
    traces: list[dict] = []
    base_date = datetime(2026, 3, 20, tzinfo=timezone.utc)

    for i in range(n):
        tmpl = _pick(TASK_TEMPLATES)
        component = _pick(COMPONENTS)
        feature = _pick(FEATURES)
        pattern = _pick(PATTERNS)
        trigger = _pick(TRIGGERS)
        ecosystem = _pick(ECOSYSTEMS)
        model = _pick(MODELS)
        agent = _pick(AGENTS)
        username = _pick(USERNAMES)

        task_desc = tmpl["desc"].format(
            component=component, feature=feature, pattern=pattern, trigger=trigger,
        )

        n_steps = _RNG.randint(*tmpl["steps_range"])
        tool_calls = _gen_tool_calls(n_steps, ecosystem)
        has_reasoning = _RNG.random() < 0.6
        conversation = _gen_conversation(task_desc, tool_calls, has_reasoning)
        committed = _RNG.random() < 0.7
        has_attribution = _RNG.random() < 0.55

        input_tokens = _RNG.randint(8_000, 120_000)
        output_tokens = _RNG.randint(1_500, 25_000)
        cache_read = int(input_tokens * _RNG.uniform(0.3, 0.85))
        cache_creation = int(input_tokens * _RNG.uniform(0.05, 0.2))

        attribution = _gen_attribution(ecosystem, _RNG.randint(1, 4)) if has_attribution else []
        patch = _gen_patch_diff(attribution) if committed and attribution else ""

        dep_pool = DEPENDENCIES.get(ecosystem, [])
        deps = _RNG.sample(dep_pool, min(_RNG.randint(2, 5), len(dep_pool)))

        date = base_date + timedelta(
            days=_RNG.randint(-6, 0),
            hours=_RNG.randint(0, 23),
            minutes=_RNG.randint(0, 59),
        )

        tid = _trace_id(f"sample-{i}-{task_desc}")

        traces.append({
            "trace_id": tid,
            "session_id": f"session-{_trace_id(f's-{i}')}",
            "dataset_repo": f"{username}/opentraces-demo",
            "contributor": username,
            "timestamp": date.isoformat(),
            "task": task_desc,
            "task_kind": tmpl["kind"],
            "model": model,
            "agent": agent,
            "ecosystem": ecosystem,
            "dependencies": deps,
            "conversation": conversation,
            "tool_calls": tool_calls,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cache_read": cache_read,
                "cache_creation": cache_creation,
            },
            "outcome": {
                "committed": committed,
                "patch_diff": patch,
            },
            "attribution": attribution,
            "n_steps": n_steps,
        })

    return traces
