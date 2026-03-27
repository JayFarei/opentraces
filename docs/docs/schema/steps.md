# Steps

The `steps` array contains the conversation as a sequence of LLM API calls (not conversational turns). Each step follows the TAO (Thought-Action-Observation) pattern.

## Step Structure

```json
{
  "step_index": 2,
  "role": "agent",
  "content": "I'll investigate the failing test...",
  "reasoning_content": "The user wants me to...",
  "model": "anthropic/claude-sonnet-4-20250514",
  "system_prompt_hash": "sp_a1b2c3",
  "agent_role": "main",
  "parent_step": null,
  "call_type": "main",
  "tools_available": ["bash", "read", "edit", "glob", "grep", "write", "agent"],
  "tool_calls": [ ... ],
  "observations": [ ... ],
  "snippets": [ ... ],
  "token_usage": { ... },
  "timestamp": "ISO8601"
}
```

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step_index` | integer | yes | Sequential step number |
| `role` | string | yes | `"system"`, `"user"`, or `"agent"` |
| `content` | string | yes | Message content |
| `reasoning_content` | string | no | Chain-of-thought / thinking content |
| `model` | string | no | Model used for this step (`provider/model-name`) |
| `system_prompt_hash` | string | no | Reference to `system_prompts` lookup table |
| `agent_role` | string | no | `"main"`, `"explore"`, `"plan"`, etc. |
| `parent_step` | integer | no | Step index of parent (for sub-agents) |
| `call_type` | string | no | `"main"`, `"subagent"`, or `"warmup"` |
| `tools_available` | string[] | no | Tools available at this step |
| `timestamp` | string | no | ISO8601 timestamp |

### call_type Values

| Value | Description |
|-------|-------------|
| `main` | Primary agent step |
| `subagent` | Sub-agent invocation (e.g. Explore, Plan) |
| `warmup` | KV cache priming call with empty output. Noise for SFT, signal for caching research. |

## Tool Calls

```json
{
  "tool_calls": [
    {
      "tool_call_id": "tc_001",
      "tool_name": "bash",
      "input": {
        "command": "npm test -- --grep parser"
      },
      "duration_ms": 3400
    }
  ]
}
```

Tool calls carry a `tool_call_id`. Observations link back via `source_call_id`. This preserves the call/result separation that training pipelines depend on.

## Observations

```json
{
  "observations": [
    {
      "source_call_id": "tc_001",
      "content": "FAIL src/parser.test.ts...",
      "output_summary": "1 test failed: parser.test.ts line 42 assertion error"
    }
  ]
}
```

`output_summary` is a lightweight preview so consumers can assess relevance without downloading full multi-KB outputs.

## Snippets

Code blocks extracted from tool results and agent responses:

```json
{
  "snippets": [
    {
      "file_path": "src/parser.ts",
      "start_line": 42,
      "end_line": 55,
      "language": "typescript",
      "text": "function parseToken(input: string)..."
    }
  ]
}
```

Useful for code-search training and as raw material for constructing the `attribution` block.

## Token Usage

Per-step token breakdown:

```json
{
  "token_usage": {
    "input_tokens": 12400,
    "output_tokens": 890,
    "cache_read_tokens": 11200,
    "cache_write_tokens": 1200,
    "prefix_reuse_tokens": 11200
  }
}
```

## Sub-Agent Hierarchy

Sub-agent steps use `parent_step` to link back to the invoking step:

```json
{
  "step_index": 5,
  "role": "agent",
  "agent_role": "explore",
  "parent_step": 3,
  "call_type": "subagent",
  "content": "Searching for related parser implementations...",
  "subagent_trajectory_ref": "session_id_of_explore_subagent"
}
```

Sub-agent transcripts are linked by `session_id` reference to separate trajectory records (ATIF pattern), not embedded. This separates storage depth from schema design.
