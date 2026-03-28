# Authentication

opentraces publishes to HuggingFace Hub. You need an HF account.

## Browser Login (Recommended)

```bash
opentraces login
```

Opens a browser-based OAuth device code flow, similar to `gh auth login`. You'll see a short code to enter at huggingface.co.

## Token Paste

For headless or CI environments:

```bash
opentraces login --token
```

Prompts for a HuggingFace token. Generate one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with `write` scope.

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
