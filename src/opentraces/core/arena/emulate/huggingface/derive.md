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
6. Drive `HfApi.upload_folder` with a dataset card. The pinned client calls
   `POST /api/validate-yaml` before hashing or committing `README.md`; record
   only the repository type and a content digest in the ledger, never the card
   body itself.
7. Run `pytest -q tests/test_arena_hf_emulator.py`; these are process-level
   contract tests, so `HF_ENDPOINT` is set before the pinned client imports.

The path-scoped `Hugging Face emulator contract` workflow provisions Bun
1.3.6 and installs `huggingface-hub==1.10.2` plus `hf-xet==1.4.3` explicitly.
The real `install-only` materializer installs the complete exact environment in
`client-lock.json` with dependency resolution disabled, probes every locked
distribution inside the lease, and includes both the full observed map and the
lock-file digest in the app-state recipe and digest. Changing any transitive
package therefore changes app-state identity rather than silently reusing it.
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

Every compiled binary has a sibling build-provenance record binding its SHA,
source SHA, Bun version, target, and contract version. That self-description is
necessary but not sufficient: a configured or cached binary is accepted only
when it also matches the repository-controlled `trusted-build.json`, whose
current digest came from the retained real-box build. After a source or
toolchain change, regenerate both from one actual compile by calling
`build_hf_emulator_binary(output, update_trusted_manifest=True)`, review the
manifest diff, and attach a new real-box proof before merge. Normal bench runs
can never update the trusted manifest.

Inside the lease, privileged setup remains on the controller identity. Public
terminal actions run as the dedicated non-sudo `opentraces-product` account
with its own writable home and recording directory; the sidecar separately runs
as `opentraces-hf` and writes its 0600 ledger outside both product locations.
The public drive enters through absolute `/usr/bin/sudo` with no controller
shell before the identity drop. Ordinary scenario variables and credentials
remain supported, but controller-sensitive environment names are refused:
`PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, and every `LD_*`, `DYLD_*`, and
`SUDO_*` name. Shell and language startup variables such as `BASH_ENV` and
`PYTHONPATH` remain supported because their consumer starts only after the
identity drop. Values remain ambient and appear only as hashes in run evidence,
never in command argv or lifecycle diagnostics.
The runner permits live verifier snapshots to refresh, then takes one final
ledger snapshot while the sidecar is still running immediately before stop.
Only post-stop reads use the cached final copy. The complete manifest, baseline
identity, seeded state, endpoint, and capability classes remain under
`world/huggingface.json`.
