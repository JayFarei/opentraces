# Authentication

opentraces publishes to HuggingFace Hub. You need an HF account.

## Token Login (Recommended)

```bash
opentraces login --token
```

Prompts for a HuggingFace access token. Generate one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with **write** scope. This is required for creating datasets and pushing traces.

## Browser Login

```bash
opentraces login
```

Opens a browser-based OAuth device code flow, similar to `gh auth login`. You'll see a short code to enter at huggingface.co.

> **Note:** OAuth tokens can write to existing dataset repos but cannot create new ones. If your dataset repo doesn't exist yet, use `opentraces login --token` with a write-access personal access token to create it.

## Environment Variable

```bash
export HF_TOKEN=hf_...
```

The CLI checks for `HF_TOKEN` automatically. Useful in CI pipelines where interactive login isn't available.

## Auth Precedence

1. `HF_TOKEN` environment variable
2. Stored credentials from `opentraces login`

## Verify

```bash
opentraces status
```

Shows your authenticated username and active configuration.

## Logout

```bash
opentraces logout
```

Clears stored credentials from `~/.opentraces/credentials.json`.
