# Authentication

opentraces publishes to HuggingFace Hub. You need an HF account.

## Browser Login

```bash
opentraces auth login
```

By default, `opentraces auth login` starts Hugging Face's OAuth device flow in your browser. This is the simplest path on a developer machine and supports the normal dataset workflow.

The CLI requests the scopes it needs to read, create, push, delete, and change visibility on datasets in namespaces you belong to.

## Token Login

```bash
opentraces auth login --token
```

Use a personal access token when you are headless, on CI, or cannot complete the browser flow.

Generate the token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

## Environment Variable

```bash
export HF_TOKEN=hf_...
```

The CLI checks for `HF_TOKEN` automatically. Useful in CI pipelines where interactive login isn't available.

## Auth Precedence

1. `HF_TOKEN` environment variable
2. Stored credentials from `opentraces auth login`

## Verify

```bash
opentraces auth whoami
```

Shows your authenticated HuggingFace username.

## Logout

```bash
opentraces auth logout
```

Clears stored credentials from `~/.opentraces/credentials`.
