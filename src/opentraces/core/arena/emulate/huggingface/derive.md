# Hugging Face emulator contract derivation

The contract tests drive the repository lockfile versions directly:

- `huggingface-hub==1.10.2`
- `hf-xet==1.4.3`

Re-run the derivation after either pin changes.

1. Enumerate every `HfApi`, `hf_hub_download`, upload, repository, and
   authentication call site under `src/opentraces`.
2. Confirm `huggingface_hub.constants.ENDPOINT` is read from `HF_ENDPOINT`
   and that `HfApi.endpoint` defaults to it.
3. Inspect `_commit_api._fetch_upload_modes`. The server must remain the
   authority for `uploadMode`; returning `regular` for every file must keep
   both the LFS batch and `hf_xet` paths unreachable.
4. Inspect `file_download` and confirm dataset downloads remain under the
   configured endpoint. At 1.10.2 the real client uses
   `/datasets/<namespace>/<repo>/resolve/<revision>/<path>`.
5. Inspect `hf_raise_for_status`. Keep `RepoNotFound`, `EntryNotFound`,
   `RevisionNotFound`, and `GatedRepo` response headers aligned with its
   exception dispatch.
6. Run `pytest -q tests/test_arena_hf_emulator.py`; these are process-level
   contract tests, so `HF_ENDPOINT` is set before the pinned client imports.

The path-scoped `Hugging Face emulator contract` workflow provisions Bun
1.3.6 and installs `huggingface-hub==1.10.2` plus `hf-xet==1.4.3` explicitly.
The tests assert those installed versions and fail when neither Bun nor a
precompiled emulator is available. Only a leased runtime-free box may opt into
the single, explicit compiled-build skip with `OPENTRACES_RUNTIME_FREE_BOX=1`.

Mutable Hub operations require a token present in emulator state. The baseline
world contains the deterministic `bench` credential; `POST
/_emulate/credentials` deterministically mints other identities. `POST
/_emulate/seed` creates declared empty repositories and `POST /_emulate/reset`
returns repositories, files, revisions, and credentials to that baseline.
Bearer-looking strings that were never minted are not credentials.

The supported operation set is deliberately finite. New client calls must be
declared in the readiness manifest and tested through the real client before
the emulator pin changes.
