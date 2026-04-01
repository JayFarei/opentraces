"""HuggingFace Features schema for opentraces datasets.

Generates dataset_infos.json so HF's datasets-server never has to infer
the schema from JSONL shards. Without this, HF inference fails on schema
evolution: a field typed as 'null' in shard-1 then 'string' in shard-2
produces a conflict that kills the config-info step.

The schema here is the authoritative 0.2.0 definition. Bump _SCHEMA_VERSION
and update HF_FEATURES when the opentraces-schema package bumps its major version.
"""

from __future__ import annotations

from opentraces_schema.version import SCHEMA_VERSION

# ---------------------------------------------------------------------------
# HF Features definition for TraceRecord (opentraces-schema 0.2.0)
# ---------------------------------------------------------------------------
# Conventions:
#   Scalar, always-present    -> {"dtype": "string"|"int64"|"float64"|"bool", "_type": "Value"}
#   Nullable scalar           -> {"dtype": "string"|..., "_type": "Value"}   (HF accepts nulls)
#   Always-null (not yet impl)-> {"dtype": "null", "_type": "Value"}
#   JSON blob (free-form dict)-> {"_type": "Json"}
#   List of scalars           -> [{"dtype": ..., "_type": "Value"}]
#   List of structs           -> [{ ... struct fields ... }]
# ---------------------------------------------------------------------------

_V = lambda dtype: {"dtype": dtype, "_type": "Value"}  # noqa: E731
_NULL = {"dtype": "null", "_type": "Value"}
_JSON = {"_type": "Json"}

HF_FEATURES: dict = {
    "schema_version": _V("string"),
    "trace_id": _V("string"),
    "session_id": _V("string"),
    "content_hash": _V("string"),
    "timestamp_start": _V("string"),
    "timestamp_end": _V("string"),
    # v0.2.0: session-level discriminator (null for pre-0.2 devtime traces)
    "execution_context": _V("string"),
    "task": {
        "description": _V("string"),
        "source": _V("string"),
        "repository": _V("string"),
        "base_commit": _V("string"),
    },
    "agent": {
        "name": _V("string"),
        "version": _V("string"),
        "model": _V("string"),
    },
    "environment": {
        "os": _V("string"),
        "shell": _V("string"),
        "vcs": {
            "type": _V("string"),
            "base_commit": _V("string"),
            "branch": _V("string"),
            "diff": _V("string"),
        },
        "language_ecosystem": [_V("string")],
    },
    "system_prompts": _JSON,
    "tool_definitions": [_V("string")],
    "steps": [
        {
            "step_index": _V("int64"),
            "role": _V("string"),
            "content": _V("string"),
            "reasoning_content": _V("string"),
            "model": _V("string"),
            "system_prompt_hash": _V("string"),
            "agent_role": _V("string"),
            "parent_step": _V("int64"),
            "call_type": _V("string"),
            "subagent_trajectory_ref": _V("string"),
            "tools_available": [_V("string")],
            "tool_calls": [
                {
                    "tool_call_id": _V("string"),
                    "tool_name": _V("string"),
                    "input": _JSON,
                    "duration_ms": _V("int64"),
                }
            ],
            "observations": [
                {
                    "source_call_id": _V("string"),
                    "content": _V("string"),
                    "output_summary": _V("string"),
                    "error": _V("string"),
                }
            ],
            "snippets": [
                {
                    "file_path": _V("string"),
                    "start_line": _V("int64"),
                    "end_line": _V("int64"),
                    "language": _V("string"),
                    "text": _V("string"),
                    "source_step": _V("int64"),
                }
            ],
            "token_usage": {
                "input_tokens": _V("int64"),
                "output_tokens": _V("int64"),
                "cache_read_tokens": _V("int64"),
                "cache_write_tokens": _V("int64"),
                "prefix_reuse_tokens": _V("int64"),
            },
            "timestamp": _V("string"),
        }
    ],
    "outcome": {
        "success": _V("bool"),
        "signal_source": _V("string"),
        "signal_confidence": _V("string"),
        "description": _V("string"),
        "patch": _V("string"),
        "committed": _V("bool"),
        "commit_sha": _V("string"),
        # v0.2.0: runtime outcome signals
        "terminal_state": _V("string"),
        "reward": _V("float64"),
        "reward_source": _V("string"),
    },
    "dependencies": [_V("string")],
    "metrics": {
        "total_steps": _V("int64"),
        "total_input_tokens": _V("int64"),
        "total_output_tokens": _V("int64"),
        "total_duration_s": _V("float64"),
        "cache_hit_rate": _V("float64"),
        "estimated_cost_usd": _V("float64"),
    },
    "security": {
        "scanned": _V("bool"),
        "flags_reviewed": _V("int64"),
        "redactions_applied": _V("int64"),
        "classifier_version": _V("string"),
    },
    "attribution": {
        "experimental": _V("bool"),
        "files": [
            {
                "path": _V("string"),
                "conversations": [
                    {
                        "contributor": {"type": _V("string")},
                        "url": _V("string"),
                        "ranges": [
                            {
                                "start_line": _V("int64"),
                                "end_line": _V("int64"),
                                "content_hash": _V("string"),
                                "confidence": _V("string"),
                            }
                        ],
                    }
                ],
            }
        ],
    },
    "metadata": _JSON,
}


def build_dataset_infos(repo_name: str) -> dict:
    """Build the dataset_infos.json dict for a given repo.

    Only declares `features` — HF computes splits, num_bytes, and num_examples
    itself after processing the shards. Providing placeholder zero-counts for
    splits causes NonMatchingSplitsSizesError when HF verifies the actual data.

    Args:
        repo_name: short repo name (e.g. "opentraces-runtime"), used as dataset_name.
    """
    return {
        "default": {
            "description": "",
            "citation": "",
            "homepage": "",
            "license": "cc-by-4.0",
            "features": HF_FEATURES,
            "builder_name": "json",
            "dataset_name": repo_name,
            "config_name": "default",
            "version": {"version_str": "0.0.0", "major": 0, "minor": 0, "patch": 0},
        }
    }
