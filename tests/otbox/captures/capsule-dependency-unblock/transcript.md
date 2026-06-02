# Conviction artifact — capsule dependency-unblock (plan 089)

**Thesis proven:** a trace capsule freezes the client's user story; the fix lives in a
dependency the client *consumes but does not control*; re-running the same capsule against
the upgraded dependency flips the verdict **reproduces → fixed** with **zero change to the
client's source**.

Run live and in the open on 2026-06-01. Library axis (consumed PyPI-style package). The
consumed-API axis (server-side redeploy) is plan 089 U6.

## The world (real, public)

- Dependency repo: **https://github.com/JayFarei/humanduration**
  - `v0.1.0` — bug: `parse("1h30m")` drops the minutes → **3600**
  - `v0.2.0` — fixed → **5400**
- Client story: `delay_seconds("1h30m") == 5400` (the client just calls `humanduration.parse`).

```
$ pip install "humanduration @ git+https://github.com/JayFarei/humanduration@v0.1.0"  # -> parse('1h30m')=3600
$ pip install "humanduration @ git+https://github.com/JayFarei/humanduration@v0.2.0"  # -> parse('1h30m')=5400
```

## 1. Real captured client session → bucket trace

A Claude Code client session (agent traces `delay('1h30m')==3600` to the consumed library) is
ingested through the real pipeline:

```
ingest action: new
trace_id: ea9e17db-a291-4c8b-b4e9-442d9c683faf
```

## 2. Export → publish → file issue (one command)

```
$ opentraces capsule issue ea9e17db --project <client> \
    --test-command "python -m pytest -q test_delay.py" \
    --consume "package:humanduration=git+https://github.com/JayFarei/humanduration@v0.1.0" \
    --bundle --publish --issue-repo JayFarei/humanduration --yes

created issue · capsule b24bdb49629c51a4 · https://huggingface.co/datasets/Jayfarei/opentraces-capsules/.../capsule.md
https://github.com/JayFarei/humanduration/issues/1
```

## 3. Maintainer agent, anonymously, from the public URL

```
$ curl -fsSL <capsule.json URL>        # resolved: b24bdb49629c51a4 · consumes: …humanduration@v0.1.0
$ opentraces capsule open  <URL> --json   # (severed HOME) OK (validated), zero residue

$ opentraces capsule test  <URL> --from-bundle --yes
🔴 reproduces  · consumed: {'humanduration': 'v0.1.0'}   ($ python -m pytest -q test_delay.py)

$ opentraces capsule test  <URL> --from-bundle --matrix humanduration=v0.1.0,v0.2.0 --yes
🔴 humanduration=v0.1.0 · reproduces
🟢 humanduration=v0.2.0 · fixed
resolved_in: humanduration=v0.2.0
```

The client source never changed. Only the **consumed dependency version** changed.

## 4. The fix unblocks — executed verdict + close + client watch

```
$ opentraces capsule test <URL> --from-bundle --with humanduration=v0.2.0 \
    --verdict-to https://github.com/JayFarei/humanduration/issues/1 --close --yes
🟢 fixed  (reason: pytest: all selected tests passed (exit 0)) · consumed: {'humanduration': 'v0.2.0'}
verdict posted to JayFarei/humanduration#1

$ opentraces capsule watch https://github.com/JayFarei/humanduration/issues/1
✅ resolved — JayFarei/humanduration#1 · CLOSED · verdict=fixed
UNBLOCKED: re-pose your original intent against the new HEAD to pick up the fix.
```

Issue **[JayFarei/humanduration#1](https://github.com/JayFarei/humanduration/issues/1)** carries
the executed `🟢 fixed` verdict for capsule `b24bdb49629c51a4` and is CLOSED.

## Axis B — consumed SERVICE (a deployed API the client never sees)

Same story, same bug, now behind an HTTP API the client only reads via `CONVERT_API_URL`.
Two public Vercel deploys of **github.com/JayFarei/convert-api**:

- deploy-v1 (buggy): `https://convert-api-hazel.vercel.app/api/convert?d=1h30m` → `{"seconds": 3600}`
- deploy-v2 (fixed): `https://convert-api-v2.vercel.app/api/convert?d=1h30m`   → `{"seconds": 5400}`

```
# real captured client session (consumes the live v1 API) -> bucket trace 3ffc1b88
$ opentraces capsule issue 3ffc1b88 --project <client> \
    --test-command "python3 check.py" \
    --consume "service:convert-api=https://convert-api-hazel.vercel.app/api/convert" \
    --bundle --publish --issue-repo JayFarei/convert-api --yes
created issue · capsule 48789fcc33c3a127 · https://github.com/JayFarei/convert-api/issues/1

# maintainer agent, anonymously:
$ opentraces capsule open <URL> --json          # (severed HOME) OK
$ opentraces capsule test <URL> --from-bundle --yes
🔴 reproduces  · consumed: {'convert-api': '…convert-api-hazel.vercel.app/api/convert'}  (live v1 → 3600)

# the SERVER-SIDE REDEPLOY (v1 → v2) the client never sees:
$ opentraces capsule test <URL> --from-bundle --with convert-api=https://convert-api-v2.vercel.app/api/convert \
    --verdict-to https://github.com/JayFarei/convert-api/issues/1 --close --yes
🟢 fixed  · consumed: {'convert-api': '…convert-api-v2.vercel.app/api/convert'}  (live v2 → 5400)
verdict posted to JayFarei/convert-api#1

$ opentraces capsule watch https://github.com/JayFarei/convert-api/issues/1
✅ resolved — JayFarei/convert-api#1 · CLOSED · verdict=fixed   → UNBLOCKED
```

The client source never changed. A server-side redeploy of the consumed API unblocked it.

## Both axes, live, in the open

| axis | dependency | repo / issue (CLOSED, fixed) | the fix |
|------|------------|------------------------------|---------|
| A — library | `humanduration` | [JayFarei/humanduration#1](https://github.com/JayFarei/humanduration/issues/1) | version bump v0.1.0 → v0.2.0 |
| B — service | `convert-api`   | [JayFarei/convert-api#1](https://github.com/JayFarei/convert-api/issues/1)     | redeploy deploy-v1 → deploy-v2 |

## Committed regressions

- `tests/test_capsule_dependency_unblock_integration.py` — library axis: real capture → export →
  upgrade flips reproduces→fixed (hermetic, file:// dep, isolated bucket).
- `tests/test_capsule_dependency_unblock.py::test_service_axis_reproduce_then_fixed_via_redeploy` —
  service axis against two real local convert-api "deploys".
- Runner / CLI / redaction coverage in `tests/test_capsule_dependency_unblock.py` and
  `tests/test_capsule_publish_redaction.py`.
