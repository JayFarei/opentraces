"""Patch Trail survival observations for Trace Trails."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import survival_cache_store
from .event_log import (
    EVENT_LOG_REF,
    build_survival_cache_index,
    read_events,
)
from .ids import id_from_payload, normalize_id
from .models import GitObjectID

PHASE4_SURVIVAL_STATES = [
    "alive_on_path",
    "alive_transformed",
    "reverted",
    "lost",
    "unknown",
]
PHASE5_SURVIVAL_STATES = [
    "alive_moved",
    "partially_preserved",
    "repaired",
]
# Cluster C — split unknown into specific causes.
SPLIT_UNKNOWN_SURVIVAL_STATES = [
    "orphan_branch",
    "missing_authored_text",
    "never_committed",
]
ALL_SURVIVAL_STATES = (
    PHASE4_SURVIVAL_STATES
    + PHASE5_SURVIVAL_STATES
    + SPLIT_UNKNOWN_SURVIVAL_STATES
    + ["orphaned"]
)
# Phase 5 reserves "orphaned" for reference-transaction observation
# (deferred beyond Phase 5 — see plan §Phase 5 line 248).
RESERVED_PHASE5_SURVIVAL_STATES = ["orphaned"]
REVERT_SEARCH_LIMIT = 2000
PATCH_TRAIL_COMMIT_LIMIT = 500
OBSERVATION_SCOPE = "anchor_to_head"
SURVIVAL_PRECEDENCE = {
    "alive_on_path": 50,
    "alive_moved": 45,
    "alive_transformed": 40,
    "partially_preserved": 35,
    "repaired": 32,
    "reverted": 30,
    "lost": 20,
    # Below: split-unknown variants are still less informative than concrete
    # observations. Order picks the most-specific cause when two are tied.
    "orphan_branch": 15,
    "missing_authored_text": 12,
    "never_committed": 11,
    "unknown": 10,
}


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _git_ok(repo: Path, *args: str) -> bool:
    return (
        subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


# ---------------------------------------------------------------------------
# Cluster G — P3: BatchSyncContext
# ---------------------------------------------------------------------------
#
# When ``sync_patch`` is called many times in a single batch (e.g. from
# ``trail track --since`` or ``detect_bursts`` per-burst enrichment) the git
# subprocess overhead dominates. Two amortizations:
#
# 1. Ancestry set: one ``git rev-list HEAD`` resolves "is X reachable from
#    HEAD" for every anchor in O(n) memory, instead of one
#    ``git merge-base --is-ancestor`` per anchor.
#
# 2. Cat-file pipe: a long-lived ``git cat-file --batch`` process serves
#    every blob read (``git show <ref>:<path>``) without paying the ~6ms
#    process-start cost per call.


@dataclass
class _CatFilePipe:
    """Long-lived ``git cat-file --batch`` pipe for blob reads.

    The pipe is opened lazily on first request and closed when the parent
    BatchSyncContext exits its ``with``/``__exit__`` lifecycle. Each request
    sends ``<rev>:<path>\\n`` and reads one header line + one blob.
    """

    repo: Path
    proc: subprocess.Popen[bytes] | None = None
    _missing: object = field(default_factory=object)

    def _ensure(self) -> subprocess.Popen[bytes]:
        if self.proc is None:
            # ``stderr=DEVNULL`` so that cat-file's diagnostic output never
            # fills its stderr buffer and dead-locks the pipe. Use the
            # default buffer size for stdout — pipes are buffered streams,
            # but we still need ``read_exactly`` semantics for blob bodies.
            self.proc = subprocess.Popen(
                ["git", "cat-file", "--batch"],
                cwd=str(self.repo),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        return self.proc

    @staticmethod
    def _read_exactly(stream: Any, count: int) -> bytes:
        """Read exactly ``count`` bytes from a pipe stream.

        ``BufferedReader.read(n)`` may return fewer than ``n`` bytes from a
        pipe — common when the producer is slow. ``read_exactly`` loops
        until we have the full payload or the pipe ends.
        """
        if count <= 0:
            return b""
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = stream.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def show(self, ref: str, path: str) -> str | None:
        """Return the blob content for ``ref:path`` or ``None`` when missing.

        Returns ``None`` for any non-blob result (missing, tree, dangling),
        matching ``_show_file`` semantics. On any framing error the pipe
        is closed so the next request gets a fresh process — caller falls
        back to a one-shot ``git show`` until the next batch.
        """
        if not ref or not path:
            return None
        proc = self._ensure()
        assert proc.stdin is not None
        assert proc.stdout is not None
        request = f"{ref}:{path}\n".encode("utf-8", errors="surrogateescape")
        try:
            proc.stdin.write(request)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.close()
            return None
        header = proc.stdout.readline()
        if not header:
            self.close()
            return None
        # Header is either ``<oid> <type> <size>\n`` or ``<key> missing\n``.
        parts = header.strip().split()
        if len(parts) == 2 and parts[1] == b"missing":
            return None
        if len(parts) != 3:
            # Unexpected header — the pipe is now out of sync. Close it
            # so subsequent calls re-open a clean process.
            self.close()
            return None
        _oid, object_type, raw_size = parts
        try:
            size = int(raw_size)
        except ValueError:
            self.close()
            return None
        if object_type != b"blob":
            # Drain content (size bytes + trailing LF) so the next
            # request reads a clean header.
            self._read_exactly(proc.stdout, size + 1)
            return None
        data = self._read_exactly(proc.stdout, size)
        if len(data) != size:
            # Truncated read — pipe died mid-blob.
            self.close()
            return None
        # Consume the trailing newline; tolerate EOF here.
        proc.stdout.read(1)
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


@dataclass
class BatchSyncContext:
    """Amortize git operations across many ``sync_patch`` calls.

    Instantiate once for a batch and pass into ``sync_patch(events=...)`` /
    ``sync_anchor(events=...)`` via the ``batch_ctx=`` keyword. The
    context owns:

    * ``ancestry_from_head`` — a set of commit shas reachable from HEAD,
      computed lazily on first ``is_ancestor_of_head`` call.
    * ``cat_file`` — a long-lived ``git cat-file --batch`` pipe used by
      ``show_file`` to satisfy blob reads without paying process startup
      per call.
    * ``head_sha`` — the cached HEAD sha at batch start, so survival rows
      key against a stable HEAD even if a writer advances HEAD mid-batch.
    * ``pending_store_entries`` — unflushed survival rows destined for the
      local evictable store (issue #116 A). Coalescing the writes amortizes
      the JSON read-modify-write across the batch (one write instead of N).
      Survival rows are no longer appended to the canonical event log.

    The context is best-used inside a ``with`` block; ``close()`` flushes
    queued cache rows then shuts the cat-file pipe down. Forgetting to close
    leaves the cat-file subprocess and unflushed cache rows dangling
    until ``atexit`` cleans them up.
    """

    repo: Path
    head_sha: str | None = None
    _ancestry: set[str] | None = None
    _ancestry_failed: bool = False
    _cat_file: _CatFilePipe | None = None
    pending_store_entries: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    # In-process cache index — populated from the events list at first
    # cache lookup and updated as writes are queued. Lets later sync_patch
    # calls in the same batch hit the cache for rows we just computed.
    _cache_index: dict[tuple[str, str], dict[str, Any]] | None = None
    # ``calls`` counts batched operations. Tests use these to assert
    # amortization (e.g. ancestry_uncached_probes==0 means batching worked).
    calls: dict[str, int] = field(default_factory=lambda: {
        "ancestry_probes": 0,
        "ancestry_uncached_probes": 0,
        "show_file": 0,
        "show_file_uncached": 0,
        "cache_writes_queued": 0,
        "cache_writes_flushed": 0,
        "cache_hits": 0,
    })

    def __post_init__(self) -> None:
        self.repo = Path(self.repo).resolve()
        if self.head_sha is None:
            try:
                self.head_sha = _git(self.repo, "rev-parse", "HEAD", check=False).strip() or None
            except Exception:
                self.head_sha = None

    def __enter__(self) -> "BatchSyncContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        # Flush any queued cache writes before tearing the pipe down.
        try:
            self.flush_cache()
        except Exception:
            pass
        if self._cat_file is not None:
            self._cat_file.close()
            self._cat_file = None

    # -- cache ----------------------------------------------------------
    def cache_index(self, events: list[Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
        """Lazily build (and cache) a survival-cache lookup table.

        ``events`` is the caller-provided event list; we read it once and
        project to ``{(patch_id, head_sha): payload}``, then overlay the local
        evictable store (issue #116 A) so freshly-cached rows that live only in
        the store — plus any legacy on-ref rows the projection surfaces — are
        both visible. The store wins on key collisions (it is the live write
        target; the on-ref index is back-compat only). Subsequent calls return
        the same dict, which we mutate as writes are queued so within-batch
        lookups for freshly-computed rows hit the cache.
        """
        if self._cache_index is None:
            index = build_survival_cache_index(events or [])
            index.update(survival_cache_store.load_index(self.repo))
            self._cache_index = index
        return self._cache_index

    def queue_cache_write(self, entries: dict[tuple[str, str], dict[str, Any]]) -> None:
        """Queue survival rows for the end-of-batch local-store flush.

        ``entries`` maps ``(patch_id, head_sha)`` to the native survival row.
        Each row is also made visible to subsequent in-batch lookups so we
        don't recompute the same row twice in one loop.
        """
        self.calls["cache_writes_queued"] += len(entries)
        if self._cache_index is None:
            self._cache_index = {}
        for key, survival in entries.items():
            self._cache_index[key] = survival
            self.pending_store_entries[key] = survival

    def flush_cache(self) -> None:
        """Persist all queued survival rows to the local evictable store."""
        if not self.pending_store_entries:
            return
        flushed = dict(self.pending_store_entries)
        self.pending_store_entries.clear()
        try:
            survival_cache_store.write_entries(self.repo, flushed)
        except Exception:
            # Best-effort: cache writes never block the caller.
            return
        self.calls["cache_writes_flushed"] += len(flushed)

    # -- ancestry --------------------------------------------------------
    def _ensure_ancestry(self) -> set[str] | None:
        if self._ancestry is not None or self._ancestry_failed:
            return self._ancestry
        try:
            out = _git(self.repo, "rev-list", "HEAD", check=False)
        except Exception:
            self._ancestry_failed = True
            return None
        if not out:
            self._ancestry = set()
            return self._ancestry
        self._ancestry = {line.strip() for line in out.splitlines() if line.strip()}
        return self._ancestry

    def is_ancestor_of_head(self, commit_sha: str) -> bool:
        """Return ``commit_sha in ancestry-from-HEAD``.

        First call materializes the ancestry set with one ``git rev-list``
        and amortizes across the batch. When the ancestry probe failed
        (e.g. corrupt repo), falls back to ``git merge-base --is-ancestor``
        per call so the batch still works correctly.
        """
        self.calls["ancestry_probes"] += 1
        ancestry = self._ensure_ancestry()
        if ancestry is not None:
            return commit_sha in ancestry
        # Ancestry resolution failed; fall back to per-call merge-base.
        self.calls["ancestry_uncached_probes"] += 1
        return _git_ok(self.repo, "merge-base", "--is-ancestor", commit_sha, "HEAD")

    # -- cat-file pipe ---------------------------------------------------
    def show_file(self, ref: str, path: str) -> str | None:
        """Read ``ref:path`` via the long-lived cat-file pipe.

        On any failure mode the pipe closes itself and the call falls back
        to a one-shot ``git show``. This keeps the batch alive when the
        pipe encounters a malformed object.
        """
        self.calls["show_file"] += 1
        if self._cat_file is None:
            self._cat_file = _CatFilePipe(repo=self.repo)
        result = self._cat_file.show(ref, path)
        if result is None and self._cat_file.proc is None:
            # Pipe closed itself due to a transport error; fall back.
            self.calls["show_file_uncached"] += 1
            return _show_file(self.repo, ref, path)
        return result


@contextmanager
def batch_sync(repo: Path) -> Iterator[BatchSyncContext]:
    """Convenience context manager that yields a :class:`BatchSyncContext`."""
    ctx = BatchSyncContext(repo=Path(repo).resolve())
    try:
        yield ctx
    finally:
        ctx.close()


def _oid(hex_value: str | None) -> dict[str, str] | None:
    if not hex_value:
        return None
    try:
        return GitObjectID(hex=hex_value.strip()).model_dump(mode="json")
    except Exception:
        return None


def _source_event(event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
        "capture_method": event.capture_method,
    }


def _head_id(repo: Path) -> dict[str, str] | None:
    return _oid(_git(repo, "rev-parse", "HEAD", check=False).strip())


def _commit_id(repo: Path, ref: str) -> dict[str, str] | None:
    return _oid(_git(repo, "rev-parse", ref, check=False).strip())


def _commit_time(repo: Path, ref: str) -> int | None:
    out = _git(repo, "log", "-1", "--format=%ct", ref, check=False).strip()
    if not out:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def _show_file(repo: Path, ref: str, path: str) -> str | None:
    out = _git(repo, "show", f"{ref}:{path}", check=False)
    return out if out else None


def _show_file_via(
    repo: Path,
    ref: str,
    path: str,
    *,
    batch_ctx: "BatchSyncContext | None" = None,
) -> str | None:
    """Read ``ref:path`` via batch_ctx when available, else per-call git show.

    Centralized so every blob-read site in this module shares one batching
    decision. Callers continue to handle ``None`` as "file missing/empty".
    """
    if batch_ctx is not None:
        return batch_ctx.show_file(ref, path)
    return _show_file(repo, ref, path)


def _parse_log_line(line: str) -> tuple[str, int | None]:
    parts = line.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        return ("", None)
    sha = parts[0]
    if len(parts) == 2:
        try:
            return (sha, int(parts[1]))
        except ValueError:
            return (sha, None)
    return (sha, None)


def _find_revert_commit(
    repo: Path,
    *,
    anchor_commit: str,
    observed_ref: str,
) -> tuple[str | None, bool]:
    out = _git(
        repo,
        "log",
        f"--max-count={REVERT_SEARCH_LIMIT + 1}",
        "--format=%H%x00%B%x00%x00",
        f"{anchor_commit}..{observed_ref}",
        check=False,
    )
    if not out:
        return None, False
    records = [record for record in out.split("\x00\x00") if record.strip()]
    truncated = len(records) > REVERT_SEARCH_LIMIT
    for record in records[:REVERT_SEARCH_LIMIT]:
        commit, _, body = record.partition("\x00")
        if f"This reverts commit {anchor_commit}" in body:
            return commit.strip(), truncated
    return None, truncated


def _authored_lines(patch: dict[str, Any]) -> list[str]:
    authored = patch.get("authored_text")
    if not isinstance(authored, str) or not authored:
        return []
    return authored.splitlines()


def _track_rename_path(
    repo: Path, *, anchor_commit: str, observed_ref: str, original_path: str
) -> tuple[str, int]:
    """Walk forward through ``git log --name-status -M`` to track renames.

    Returns ``(current_path, rename_hops)``. When the file was never
    renamed in the range ``anchor_commit..observed_ref``, returns
    ``(original_path, 0)``.
    """
    out = _git(
        repo,
        "log",
        "-M",
        "--name-status",
        "--reverse",
        "--pretty=format:",
        f"{anchor_commit}..{observed_ref}",
        check=False,
    )
    current = original_path
    hops = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].startswith("R") and parts[1] == current:
            current = parts[2]
            hops += 1
    return current, hops


def _committers_in_range(repo: Path, *, ref: str, path: str, start: int, end: int) -> set[str]:
    """Return the set of committer emails for lines [start..end] at ``ref``."""
    out = _git(
        repo,
        "blame",
        f"-L{start},{end}",
        "--line-porcelain",
        ref,
        "--",
        path,
        check=False,
    )
    emails: set[str] = set()
    for line in out.splitlines():
        if line.startswith("committer-mail "):
            email = line.split(" ", 1)[1].strip().strip("<>")
            if email:
                emails.add(email)
    return emails


def _commit_author_email(repo: Path, commit: str) -> str | None:
    out = _git(repo, "log", "-1", "--format=%ae", commit, check=False).strip()
    return out or None


def _retention_fraction(preserved: int, authored: int) -> float | None:
    """Return ``preserved/authored`` rounded to 3dp, or ``None`` when undefined.

    Cluster C-3: every observation whose state implies *some* survival
    (alive_on_path, alive_transformed, alive_moved, partially_preserved)
    carries this fraction so reviewers can sort by lineage strength.
    """
    if authored <= 0:
        return None
    return round(preserved / authored, 3)


def _resolve_lost_attribution(
    repo: Path,
    *,
    anchor_commit: str,
    path: str,
    observed_ref: str,
    authored_lines: list[str],
    cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: "BatchSyncContext | None" = None,
) -> tuple[str | None, str]:
    """Cluster F D2/D3 — return ``(killer_commit_sha, lost_kind)``.

    ``lost_kind`` is one of ``file_deleted`` or ``hunk_removed``. The
    killer commit is resolved only for ``file_deleted`` (the cheap case:
    one ``git log --diff-filter=D`` lookup). For ``hunk_removed`` we
    deliberately leave the killer as ``None`` — line-level attribution
    via ``git log -L`` is far slower and ambiguous when multiple
    intervening commits touched the range.

    The ``cache`` dict, when provided, is keyed by ``(path, anchor, head)``
    so a batch ``trail track --since`` survey reuses one resolver result
    across every patch on the same file.
    """
    # When a batch_ctx is available and the observed ref is HEAD, the
    # context already cached the head_sha for the whole batch. This lets
    # the lost-attribution cache stay sticky across patches.
    if (
        batch_ctx is not None
        and observed_ref == "HEAD"
        and batch_ctx.head_sha
    ):
        head_sha = batch_ctx.head_sha
    else:
        head_sha = _git(repo, "rev-parse", observed_ref, check=False).strip()
    cache_key = (path, anchor_commit, head_sha)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    # Cheap probe: does the file exist at observed_ref?
    file_text_at_head = _show_file_via(repo, observed_ref, path, batch_ctx=batch_ctx)
    if file_text_at_head is None:
        # File deleted somewhere between anchor and observed_ref.
        # ``git log --diff-filter=D --format=%H -1 -- <path>`` finds
        # the most recent deletion of <path> reachable from observed_ref.
        out = _git(
            repo,
            "log",
            f"{anchor_commit}..{observed_ref}",
            "--diff-filter=D",
            "--format=%H",
            "-1",
            "--",
            path,
            check=False,
        ).strip()
        sha = out.splitlines()[0].strip() if out else None
        result = (sha or None, "file_deleted")
    else:
        # File alive ⇒ classify as hunk_removed and skip line-walk.
        result = (None, "hunk_removed")

    if cache is not None:
        cache[cache_key] = result
    return result


def _count_preserved_lines(authored_lines: list[str], current_text: str) -> int:
    """Count authored lines that survive (whitespace-stripped) anywhere in
    the current file. Empty lines do not contribute to the count.

    The metric is intentionally line-level rather than character-level so
    a refactor that splits one authored line across two surviving lines
    is not double-counted. Phase 5 uses this for partially_preserved /
    alive_moved gating; future tiers may use AST-aware matching.
    """
    if not authored_lines:
        return 0
    current_lines_stripped = {line.strip() for line in current_text.splitlines() if line.strip()}
    return sum(
        1 for line in authored_lines if line.strip() and line.strip() in current_lines_stripped
    )


def _compute_survival(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
    observed_ref: str = "HEAD",
    head_id: dict[str, str] | None = None,
    observed_commit_time: int | None = None,
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: BatchSyncContext | None = None,
    skip_ancestry_check: bool = False,
) -> dict[str, Any]:
    observed_commit_id = _commit_id(repo, observed_ref)
    if head_id is None:
        head_id = _head_id(repo)
    if observed_commit_time is None and observed_commit_id is not None:
        observed_commit_time = _commit_time(repo, observed_ref)
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    path = anchor.get("path") or patch.get("file_path")
    anchor_range = anchor.get("range") or {}
    limitations: list[str] = []

    base = {
        "observation_type": "patch_survival_observed",
        "git_anchor_id": id_from_payload(anchor, "git_anchor"),
        "trace_patch_id": id_from_payload(anchor, "trace_patch")
        or id_from_payload(patch, "trace_patch"),
        "anchor_commit_id": commit_id or None,
        "observed_ref": observed_ref,
        "observed_commit_id": observed_commit_id,
        "observed_commit_time": observed_commit_time,
        "path": path,
        "range": anchor_range or None,
        "evidence_tier": anchor.get("evidence_tier") or "unknown",
        "evidence_firmness": anchor.get("evidence_firmness") or "unknown",
        "limitations": limitations,
    }
    if head_id is not None and observed_commit_id == head_id:
        base["observed_head_id"] = head_id

    if not anchor_commit or not path or observed_commit_id is None:
        limitations.append("missing_git_survival_inputs")
        # Cluster C-2: keep ``unknown`` as the fallback for the genuinely
        # indeterminate case (no anchor commit, no path, no observed ref).
        return {**base, "survival_state": "unknown", "retention_fraction": None}
    # Cluster G — P3: when batch_ctx is available *and* the observed ref
    # resolves to the cached HEAD (either the literal ``"HEAD"`` or the
    # exact head sha that matches), resolve ``is-ancestor`` against the
    # cached ancestry set (one ``git rev-list`` for the whole batch).
    # For non-HEAD refs we still fall back to ``git merge-base
    # --is-ancestor`` per call. ``skip_ancestry_check`` is the third
    # short-circuit: ``_anchor_observations`` only walks commits already
    # known to be in ``anchor..HEAD``, so the per-observation ancestor
    # probe is redundant and would dominate the wall-clock budget.
    observed_ref_is_head = (
        observed_ref == "HEAD"
        or (
            batch_ctx is not None
            and batch_ctx.head_sha
            and observed_ref == batch_ctx.head_sha
        )
    )
    if skip_ancestry_check:
        ancestor_ok = True
    elif batch_ctx is not None and observed_ref_is_head:
        ancestor_ok = batch_ctx.is_ancestor_of_head(anchor_commit)
    else:
        ancestor_ok = _git_ok(
            repo, "merge-base", "--is-ancestor", anchor_commit, observed_ref
        )
    if not ancestor_ok:
        limitations.append(
            "anchor_commit_not_reachable_from_head"
            if observed_ref == "HEAD"
            else "anchor_commit_not_reachable_from_observed_ref"
        )
        # Cluster C-2: anchor exists but is on a branch not in HEAD's
        # ancestry. ``orphan_branch`` is the specific signal.
        return {**base, "survival_state": "orphan_branch", "retention_fraction": None}

    # Cluster G — P3: defer the revert search. ``_find_revert_commit``
    # runs ``git log --max-count=2001 anchor..observed_ref`` which is one
    # of the costliest calls in the survival pipeline. We only need the
    # answer when classification reaches the file-deleted or
    # preserved-zero branches (the two ``revert_commit`` consumers
    # below). For the alive cases we never read it. The lazy resolver
    # caches the first call so a single observation pays at most once.
    _revert_cache: dict[str, tuple[str | None, bool]] = {}

    def _lazy_revert() -> str | None:
        cached = _revert_cache.get("result")
        if cached is None:
            commit, truncated = _find_revert_commit(
                repo,
                anchor_commit=anchor_commit,
                observed_ref=observed_ref,
            )
            if truncated:
                limitations.append("revert_search_truncated")
            _revert_cache["result"] = (commit, truncated)
            return commit
        return cached[0]

    authored_lines = _authored_lines(patch)
    if not authored_lines:
        limitations.append("missing_authored_text")
        # Cluster C-2: anchor reachable, but the patch event has no
        # authored content to compare against — call this out explicitly.
        return {
            **base,
            "survival_state": "missing_authored_text",
            "retention_fraction": None,
        }

    current_text = _show_file_via(repo, observed_ref, path, batch_ctx=batch_ctx)
    if current_text is None:
        # File missing at observed_ref. Phase 5 tries rename detection
        # before declaring the patch lost.
        new_path, rename_hops = _track_rename_path(
            repo,
            anchor_commit=anchor_commit,
            observed_ref=observed_ref,
            original_path=path,
        )
        if new_path != path and rename_hops > 0:
            moved_text = _show_file_via(
                repo, observed_ref, new_path, batch_ctx=batch_ctx
            )
            if moved_text is not None:
                preserved = _count_preserved_lines(authored_lines, moved_text)
                if preserved > 0:
                    moved_fraction = _retention_fraction(
                        preserved, len(authored_lines)
                    )
                    return {
                        **base,
                        "survival_state": "alive_moved",
                        "current_path": new_path,
                        "rename_hops": rename_hops,
                        "preserved_line_count": preserved,
                        "authored_line_count": len(authored_lines),
                        "retention_fraction": moved_fraction,
                        "retention_fraction_at_original_range": moved_fraction,
                        "retention_fraction_at_anchor": moved_fraction,
                    }
        revert_commit = _lazy_revert()
        if revert_commit:
            return {
                **base,
                "survival_state": "reverted",
                "revert_commit_id": _oid(revert_commit),
                "retention_fraction": None,
            }
        # Cluster F D2/D3: surface killer commit + lost_kind for the
        # file-deleted branch (the cheap case — one git log call).
        killer, lost_kind = _resolve_lost_attribution(
            repo,
            anchor_commit=anchor_commit,
            path=path,
            observed_ref=observed_ref,
            authored_lines=authored_lines,
            cache=lost_attribution_cache,
            batch_ctx=batch_ctx,
        )
        out_lost = {
            **base,
            "survival_state": "lost",
            "retention_fraction": None,
            "lost_kind": lost_kind,
            "lost_at_commit_sha": killer,
        }
        if killer is None and lost_kind == "file_deleted":
            limitations.append("lost_attribution_failed")
        return out_lost

    current_lines = current_text.splitlines()
    start = anchor_range.get("start_line")
    end = anchor_range.get("end_line")
    preserved = _count_preserved_lines(authored_lines, current_text)
    range_exists = False
    if isinstance(start, int) and isinstance(end, int) and start >= 1 and end >= start:
        current_range = current_lines[start - 1 : end]
        if current_range == authored_lines:
            return {
                **base,
                "survival_state": "alive_on_path",
                "retention_fraction": 1.0,
                "retention_fraction_at_original_range": 1.0,
                "retention_fraction_at_anchor": 1.0,
            }
        if len(current_lines) >= start:
            range_exists = True
            anchor_email = _commit_author_email(repo, anchor_commit)
            range_committers = _committers_in_range(
                repo,
                ref=observed_ref,
                path=path,
                start=start,
                end=end,
            )
            non_anchor_committers = (
                range_committers - {anchor_email} if anchor_email else range_committers
            )
            if non_anchor_committers:
                repaired_fraction = _retention_fraction(
                    preserved, len(authored_lines)
                )
                return {
                    **base,
                    "survival_state": "repaired",
                    "repair_committer_email": sorted(non_anchor_committers)[0],
                    "repair_committers": sorted(non_anchor_committers),
                    "anchor_author_email": anchor_email,
                    "retention_fraction": repaired_fraction,
                    "retention_fraction_at_original_range": repaired_fraction,
                    "retention_fraction_at_anchor": repaired_fraction,
                }

    if preserved == 0:
        revert_commit = _lazy_revert()
        if revert_commit:
            return {
                **base,
                "survival_state": "reverted",
                "revert_commit_id": _oid(revert_commit),
                "retention_fraction": None,
            }
    if 0 < preserved < len(authored_lines):
        # Cluster F D4: split retention into two semantically distinct
        # observations. ``at_original_range`` is the existing literal
        # (preserved-lines / authored-lines) ratio. ``at_anchor`` is the
        # anchor-identity-strength signal: the anchor still resolves to
        # a firm range so the patch's lineage is preserved at *its*
        # tracked position. For partial preservation the two coincide
        # because the literal lines themselves are what survived; for
        # alive_transformed (preserved=0) ``at_anchor`` jumps to 1.0
        # because the anchor's range still exists even though the
        # literal content drifted.
        original_range_fraction = _retention_fraction(
            preserved, len(authored_lines)
        )
        return {
            **base,
            "survival_state": "partially_preserved",
            "preserved_line_count": preserved,
            "authored_line_count": len(authored_lines),
            "retention_fraction": original_range_fraction,
            "retention_fraction_at_original_range": original_range_fraction,
            "retention_fraction_at_anchor": original_range_fraction,
        }
    if range_exists:
        original_range_fraction = _retention_fraction(
            preserved, len(authored_lines)
        )
        # The anchor's current range still resolves — its identity-by-range
        # survived, so ``at_anchor`` is 1.0 even when literal lines drifted.
        at_anchor_fraction: float = 1.0
        return {
            **base,
            "survival_state": "alive_transformed",
            "retention_fraction": at_anchor_fraction,
            "retention_fraction_at_original_range": original_range_fraction,
            "retention_fraction_at_anchor": at_anchor_fraction,
        }

    # File alive at observed_ref but range doesn't exist + preserved == 0:
    # the anchor's identity is gone. D2/D3: classify ``hunk_removed``,
    # leave killer commit None (no cheap line-walk attribution).
    killer, lost_kind = _resolve_lost_attribution(
        repo,
        anchor_commit=anchor_commit,
        path=path,
        observed_ref=observed_ref,
        authored_lines=authored_lines,
        cache=lost_attribution_cache,
    )
    return {
        **base,
        "survival_state": "lost",
        "retention_fraction": None,
        "lost_kind": lost_kind,
        "lost_at_commit_sha": killer,
    }


def _aggregate_current_survival(
    indexed_observations: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    if not indexed_observations:
        return {"survival_state": "unknown", "retention_fraction": None}
    best_index, best = max(
        indexed_observations,
        key=lambda item: (
            SURVIVAL_PRECEDENCE.get(item[1].get("survival_state"), 0),
            item[0],
        ),
    )
    current = dict(best)
    current["aggregation"] = "any_alive_anchor_wins"
    current["selected_observation_index"] = best_index
    return current


def _commits_from_anchor_to_head(
    repo: Path,
    anchor_commit: str,
    *,
    history_limit: int,
) -> tuple[list[tuple[str, int | None]], int | None, list[str]]:
    """Return ``(commits, descendant_count, limitations)`` for one anchor.

    ``commits`` is a chronological ``[(sha, commit_time_unix)]`` path including
    the anchor commit and HEAD. When the anchor is not reachable from HEAD,
    returns a single observation at HEAD and ``descendant_count=None`` so
    callers can distinguish "unreachable, count unknown" from "reachable, zero
    descendants".
    """
    if not _git_ok(repo, "merge-base", "--is-ancestor", anchor_commit, "HEAD"):
        head_sha = _git(repo, "rev-parse", "HEAD", check=False).strip()
        if not head_sha:
            return [], None, []
        return [("HEAD", _commit_time(repo, "HEAD"))], None, []

    total_out = _git(
        repo,
        "rev-list",
        "--count",
        "--ancestry-path",
        f"{anchor_commit}..HEAD",
        check=False,
    ).strip()
    try:
        descendant_count = int(total_out or "0")
    except ValueError:
        descendant_count = 0

    anchor_time = _commit_time(repo, anchor_commit)
    if descendant_count == 0:
        return [(anchor_commit, anchor_time)], 0, []

    limit = max(history_limit, 2)
    if descendant_count <= limit - 1:
        out = _git(
            repo,
            "log",
            "--reverse",
            "--ancestry-path",
            "--format=%H %ct",
            f"{anchor_commit}..HEAD",
            check=False,
        )
        descendants = [_parse_log_line(line) for line in out.splitlines() if line.strip()]
        return [(anchor_commit, anchor_time)] + descendants, descendant_count, []

    keep_oldest = max(limit - 2, 0)
    oldest_descendants: list[tuple[str, int | None]] = []
    if keep_oldest:
        out = _git(
            repo,
            "log",
            "--ancestry-path",
            f"--skip={descendant_count - keep_oldest}",
            f"--max-count={keep_oldest}",
            "--format=%H %ct",
            f"{anchor_commit}..HEAD",
            check=False,
        )
        oldest_descendants = list(
            reversed([_parse_log_line(line) for line in out.splitlines() if line.strip()])
        )
    head_sha = _git(repo, "rev-parse", "HEAD", check=False).strip()
    head_time = _commit_time(repo, head_sha) if head_sha else None
    return (
        [(anchor_commit, anchor_time)] + oldest_descendants + [(head_sha, head_time)],
        descendant_count,
        ["patch_trail_history_truncated"],
    )


def _anchor_observations(
    repo: Path,
    *,
    patch: dict[str, Any],
    anchor: dict[str, Any],
    head_id: dict[str, str] | None,
    history_limit: int,
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: BatchSyncContext | None = None,
) -> tuple[list[dict[str, Any]], int | None, list[str]]:
    commit_id = anchor.get("commit_id") or {}
    anchor_commit = commit_id.get("hex")
    if not anchor_commit:
        observation = _compute_survival(
            repo,
            patch=patch,
            anchor=anchor,
            head_id=head_id,
            lost_attribution_cache=lost_attribution_cache,
            batch_ctx=batch_ctx,
        )
        observation["anchor_trail_index"] = 0
        observation["anchor_descendant_count"] = None
        return [observation], None, []

    commits, descendant_count, limitations = _commits_from_anchor_to_head(
        repo, anchor_commit, history_limit=history_limit
    )
    # Cluster G — P3: when ``descendant_count is not None`` the helper
    # already proved the anchor is reachable from HEAD and the returned
    # commits are exactly that ancestry path. Re-running
    # ``merge-base --is-ancestor`` per observation costs one git
    # invocation per descendant — pure overhead because the answer is
    # always True. Skip it. ``descendant_count is None`` means "anchor
    # unreachable from HEAD"; we keep the per-observation probe so
    # ``orphan_branch`` etc. still classify correctly.
    skip_per_obs_ancestry = descendant_count is not None
    observations: list[dict[str, Any]] = []
    for index, (commit_sha, commit_time) in enumerate(commits):
        observation = _compute_survival(
            repo,
            patch=patch,
            anchor=anchor,
            observed_ref=commit_sha,
            head_id=head_id,
            observed_commit_time=commit_time,
            lost_attribution_cache=lost_attribution_cache,
            batch_ctx=batch_ctx,
            skip_ancestry_check=skip_per_obs_ancestry,
        )
        observation["anchor_trail_index"] = index
        observation["anchor_descendant_count"] = descendant_count
        observations.append(observation)
    return observations, descendant_count, limitations


def _sync(
    repo: Path,
    *,
    trace_patch_id: str | None = None,
    git_anchor_id: str | None = None,
    history_limit: int | None = None,
    events: list[Any] | None = None,
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: BatchSyncContext | None = None,
) -> dict[str, Any]:
    """Sync a Trace Patch against Git history and report survival.

    Limitation field scoping is deliberately three-level:

    * ``response["trail_limitations"]`` carries trail-construction limitations
      (e.g. ``patch_trail_history_truncated`` when an anchor's descendant count
      exceeds ``history_limit``).
    * ``response["observations"][i]["limitations"]`` carries per-observation
      limitations from a single commit lookup (e.g. ``revert_search_truncated``,
      ``missing_authored_text``, ``anchor_commit_not_reachable_from_*``).
    * ``response["current_survival"]["limitations"]`` mirrors the latest
      observation per anchor.

    These fields are deliberately distinct from the Phase 5 ``capture_limitations``
    vocabulary on TrailEvents. Capture limitations describe what the capture
    pipeline observed during a session; trail/observation limitations describe
    what the sync projection could compute at query time over current repo
    state.
    """
    effective_limit = history_limit if history_limit is not None else PATCH_TRAIL_COMMIT_LIMIT
    head_id = _head_id(repo)
    if events is None:
        events = read_events(repo)
    patches: dict[str, tuple[dict[str, Any], Any]] = {}
    anchors: list[tuple[dict[str, Any], Any]] = []
    if trace_patch_id:
        trace_patch_id = normalize_id(trace_patch_id)
    if git_anchor_id:
        git_anchor_id = normalize_id(git_anchor_id)
    for event in events:
        if event.event_type == "trace_patch_created":
            patch_id = id_from_payload(event.payload, "trace_patch")
            if patch_id:
                patches[patch_id] = (event.payload, event)
        elif event.event_type == "git_anchor_created":
            anchors.append((event.payload, event))

    if git_anchor_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if id_from_payload(anchor, "git_anchor") == git_anchor_id
        ]
        if anchors and trace_patch_id is None:
            trace_patch_id = id_from_payload(anchors[0][0], "trace_patch")
    elif trace_patch_id:
        anchors = [
            (anchor, event)
            for anchor, event in anchors
            if id_from_payload(anchor, "trace_patch") == trace_patch_id
        ]

    patch_pair = patches.get(trace_patch_id or "")
    if patch_pair is None:
        return {
            "trace_patch_id": trace_patch_id,
            "git_anchor_id": git_anchor_id,
            "relation": "unknown",
            "current_survival": {"survival_state": "unknown", "retention_fraction": None},
            "current_observations": [],
            "observations": [],
            "observation_scope": OBSERVATION_SCOPE,
            "history_limit": effective_limit,
            "trail_limitations": ["no_trace_patch_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "phase5_survival_states": PHASE5_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [],
        }

    patch, patch_event = patch_pair
    if not anchors:
        # Cluster C-2: a Trace Patch with no Git Anchor is one of two
        # things — ``never_committed`` if the file the patch claimed to
        # touch still exists in HEAD (the patch was authored, then
        # superseded intra-trace before any commit captured it), or
        # ``unknown`` when we cannot tell (no path, no HEAD, file gone).
        patch_path = patch.get("file_path")
        survival_state = "unknown"
        survival_limitations: list[str] = []
        if patch_path and head_id is not None:
            current_text = _show_file_via(
                repo, "HEAD", patch_path, batch_ctx=batch_ctx
            )
            if current_text is not None:
                survival_state = "never_committed"
                survival_limitations.append("no_git_anchor_event")
        current_survival: dict[str, Any] = {
            "survival_state": survival_state,
            "retention_fraction": None,
            "limitations": survival_limitations,
        }
        if patch_path:
            current_survival["path"] = patch_path
        return {
            "trace_patch_id": id_from_payload(patch, "trace_patch"),
            "git_anchor_id": git_anchor_id,
            "relation": "unknown",
            "current_survival": current_survival,
            "current_observations": [],
            "observations": [],
            "observation_scope": OBSERVATION_SCOPE,
            "history_limit": effective_limit,
            "trail_limitations": ["no_git_anchor_event"],
            "event_log_ref": EVENT_LOG_REF,
            "phase4_survival_states": PHASE4_SURVIVAL_STATES,
            "phase5_survival_states": PHASE5_SURVIVAL_STATES,
            "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
            "source_events": [_source_event(patch_event)],
        }

    observations: list[dict[str, Any]] = []
    current_observations: list[tuple[int, dict[str, Any]]] = []
    trail_limitations: list[str] = []
    sorted_anchors = sorted(anchors, key=lambda item: item[1].event_sequence)
    for anchor, event in sorted_anchors:
        anchor_observations, _descendant_count, anchor_limitations = _anchor_observations(
            repo,
            patch=patch,
            anchor=anchor,
            head_id=head_id,
            history_limit=effective_limit,
            lost_attribution_cache=lost_attribution_cache,
            batch_ctx=batch_ctx,
        )
        trail_limitations.extend(anchor_limitations)
        for observation in anchor_observations:
            observation["anchor_event_sequence"] = event.event_sequence
        start_index = len(observations)
        observations.extend(anchor_observations)
        if anchor_observations:
            current_observations.append(
                (start_index + len(anchor_observations) - 1, anchor_observations[-1])
            )

    for sequence, observation in enumerate(observations):
        observation["observation_sequence"] = sequence

    unique_trail_limitations = list(dict.fromkeys(trail_limitations))
    current_observation_values = [observation for _index, observation in current_observations]
    return {
        "trace_patch_id": id_from_payload(patch, "trace_patch"),
        "git_anchor_id": git_anchor_id,
        "relation": "patch_trail_observed",
        "current_survival": (
            current_observation_values[-1]
            if git_anchor_id and current_observation_values
            else _aggregate_current_survival(current_observations)
        ),
        "current_observations": current_observation_values,
        "observations": observations,
        "observation_scope": OBSERVATION_SCOPE,
        "history_limit": effective_limit,
        "trail_limitations": unique_trail_limitations,
        "event_log_ref": EVENT_LOG_REF,
        "phase4_survival_states": PHASE4_SURVIVAL_STATES,
        "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
        "source_events": [_source_event(patch_event)]
        + [_source_event(event) for _anchor, event in sorted_anchors],
    }


# ``_AUTO_BATCH_CTX_BY_EVENTS_ID`` caches a BatchSyncContext per
# ``(id(events), repo, fingerprint)`` so a CLI batch loop that passes
# the same ``events`` list to many ``sync_patch`` calls amortizes the
# cat-file pipe and ancestry probes without having to know about
# :class:`BatchSyncContext`.
#
# We pair ``id(events)`` with a fingerprint built from the list's length
# and (if present) the first event's id so that a recycled object id —
# events list got freed and a new one took the slot — does not return
# a stale context.
_AUTO_BATCH_CTX_BY_EVENTS_ID: dict[
    tuple[int, str, int, str | None],
    BatchSyncContext,
] = {}


def _events_fingerprint(events: list[Any]) -> tuple[int, str | None]:
    """Cheap fingerprint of an events list for cache-key disambiguation."""
    length = len(events)
    if length == 0:
        return (0, None)
    head_id: str | None = None
    head = events[0]
    if hasattr(head, "event_id"):
        head_id = getattr(head, "event_id")
    return (length, head_id)


def _auto_batch_ctx(
    repo: Path,
    events: list[Any] | None,
) -> BatchSyncContext | None:
    """Return a shared :class:`BatchSyncContext` for repeated batch calls.

    The CLI batch path (``cli/trail.py::_emit_batch_track``) reads events
    once and passes that same list to every ``sync_patch`` call. This
    helper caches a context per ``(id(events), repo, fingerprint)`` so
    the cat-file pipe and ancestry set survive across the loop. The
    fingerprint guards against ``id()`` recycling.

    The cache leaks one ``BatchSyncContext`` per unique events list per
    process lifetime. This is bounded in practice (each top-level batch
    creates exactly one events list), and the cat-file pipe is a single
    file descriptor — well within the 256 fd default limit.
    """
    if events is None:
        return None
    fp_len, fp_head = _events_fingerprint(events)
    repo_str = str(repo)
    key = (id(events), repo_str, fp_len, fp_head)
    cached = _AUTO_BATCH_CTX_BY_EVENTS_ID.get(key)
    if cached is not None:
        return cached

    ctx = BatchSyncContext(repo=Path(repo).resolve())
    _AUTO_BATCH_CTX_BY_EVENTS_ID[key] = ctx
    return ctx


def _close_auto_batch_contexts() -> None:
    """Close every auto-created batch context. Called at process exit."""
    for ctx in list(_AUTO_BATCH_CTX_BY_EVENTS_ID.values()):
        try:
            ctx.close()
        except Exception:
            pass
    _AUTO_BATCH_CTX_BY_EVENTS_ID.clear()


import atexit as _atexit
_atexit.register(_close_auto_batch_contexts)


def _resolve_head_sha(
    repo: Path,
    *,
    batch_ctx: BatchSyncContext | None = None,
) -> str | None:
    """Return the current HEAD sha, prefer the batch context's cached value.

    Reading HEAD per call costs one ``git rev-parse``. When a batch context
    is in play it already cached HEAD at batch open, so survival cache
    keys remain stable across the batch even if a writer advances HEAD.
    """
    if batch_ctx is not None and batch_ctx.head_sha:
        return batch_ctx.head_sha
    out = _git(repo, "rev-parse", "HEAD", check=False).strip()
    return out or None


def sync_patch(
    repo: Path,
    trace_patch_id: str,
    *,
    history_limit: int | None = None,
    events: list[Any] | None = None,
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: BatchSyncContext | None = None,
    use_cache: bool = True,
    write_cache: bool = True,
) -> dict[str, Any]:
    """Sync survival for all Git Anchors attached to a Trace Patch.

    ``events`` is an optional pre-loaded TrailEvent list. Batch callers
    that survey many patches in one shot can read events once and pass
    the list in to amortize the read cost (Cluster C-1).

    ``lost_attribution_cache`` is an optional dict that batch callers may
    thread through across patches to avoid repeated ``git log
    --diff-filter=D`` lookups for the same file (Cluster F D2).

    ``batch_ctx`` (Cluster G — P3) is an optional :class:`BatchSyncContext`
    that holds a long-lived ``git cat-file --batch`` pipe and a cached
    ancestry-from-HEAD set so a 100-patch batch pays one git rev-list
    instead of 100 merge-base probes.

    ``use_cache`` / ``write_cache`` (Cluster G — P2) control the
    ``patch_survival_cached`` event keyed by ``(trace_patch_id,
    observed_head_sha)``. ``use_cache=True`` (default) returns the cached
    row immediately when it matches the current HEAD; ``write_cache=True``
    appends a fresh cache event after a miss. Disabling them lets
    callers force a re-compute (e.g. doctor / debug) without poisoning
    the cache.
    """
    # Cluster G — P3: when the CLI batch path passes the same ``events``
    # list across many ``sync_patch`` calls, share a BatchSyncContext so
    # the cat-file pipe and ancestry-from-HEAD set amortize across the
    # whole loop. Explicit ``batch_ctx`` overrides the auto-share.
    if batch_ctx is None:
        batch_ctx = _auto_batch_ctx(repo, events)

    head_sha = _resolve_head_sha(repo, batch_ctx=batch_ctx) if (use_cache or write_cache) else None
    normalized_patch_id = normalize_id(trace_patch_id) if trace_patch_id else trace_patch_id

    # Cluster G — P2: serve from cache when (patch_id, head_sha) matches.
    # Use the batch context's in-process cache index when available so
    # within-batch lookups for rows we just queued for write also hit.
    if (
        use_cache
        and head_sha
        and normalized_patch_id
    ):
        if batch_ctx is not None:
            cache_index = batch_ctx.cache_index(events)
        else:
            cache_index = (
                build_survival_cache_index(events) if events is not None else {}
            )
            # Overlay the local evictable store (issue #116 A): the live cache
            # write target. The on-ref index above stays for legacy rows.
            cache_index = {**cache_index, **survival_cache_store.load_index(repo)}
        cached = cache_index.get((normalized_patch_id, head_sha))
        if cached is not None:
            if batch_ctx is not None:
                batch_ctx.calls["cache_hits"] += 1
            return {
                "trace_patch_id": normalized_patch_id,
                "git_anchor_id": None,
                "relation": "patch_trail_observed_cached",
                "current_survival": cached,
                "current_observations": [cached],
                "observations": [cached],
                "observation_scope": OBSERVATION_SCOPE,
                "history_limit": history_limit if history_limit is not None else PATCH_TRAIL_COMMIT_LIMIT,
                "trail_limitations": [],
                "event_log_ref": EVENT_LOG_REF,
                "phase4_survival_states": PHASE4_SURVIVAL_STATES,
                "reserved_survival_states": RESERVED_PHASE5_SURVIVAL_STATES,
                "source_events": [],
                "cache": {
                    "hit": True,
                    "key": {
                        "trace_patch_id": normalized_patch_id,
                        "observed_head_sha": head_sha,
                    },
                },
            }

    result = _sync(
        repo,
        trace_patch_id=trace_patch_id,
        history_limit=history_limit,
        events=events,
        lost_attribution_cache=lost_attribution_cache,
        batch_ctx=batch_ctx,
    )

    # Cluster G — P2 / issue #116 A: persist the freshly-computed row so the
    # next call against the same HEAD can short-circuit. The row now lands in
    # the LOCAL evictable store (``<git-common-dir>/opentraces/``), NOT the
    # append-only canonical event ref. Inside a batch we queue the row on the
    # context for one coalesced store write at close-time; in the per-call
    # (no-batch) path we write immediately.
    if (
        write_cache
        and head_sha
        and isinstance(result, dict)
    ):
        result_patch_id = result.get("trace_patch_id") or normalized_patch_id
        survival = result.get("current_survival") or {}
        if (
            isinstance(result_patch_id, str)
            and result_patch_id
            and isinstance(survival, dict)
            and survival.get("survival_state") not in (None,)
        ):
            entry = {(result_patch_id, head_sha): survival}
            if batch_ctx is not None:
                batch_ctx.queue_cache_write(entry)
            else:
                survival_cache_store.write_entries(repo, entry)

    if isinstance(result, dict):
        result.setdefault(
            "cache",
            {
                "hit": False,
                "key": {
                    "trace_patch_id": result.get("trace_patch_id"),
                    "observed_head_sha": head_sha,
                },
            },
        )
    return result


def sync_anchor(
    repo: Path,
    git_anchor_id: str,
    *,
    history_limit: int | None = None,
    events: list[Any] | None = None,
    lost_attribution_cache: dict[tuple[str, str, str], tuple[str | None, str]] | None = None,
    batch_ctx: BatchSyncContext | None = None,
) -> dict[str, Any]:
    """Sync survival for one Git Anchor.

    See ``sync_patch`` for the optional ``events``,
    ``lost_attribution_cache``, and ``batch_ctx`` parameters. Anchor-scoped
    sync does not consult the survival cache because the cache is keyed
    by ``trace_patch_id`` (which an anchor implies but is not enforced).
    """
    if batch_ctx is None:
        batch_ctx = _auto_batch_ctx(repo, events)
    return _sync(
        repo,
        git_anchor_id=git_anchor_id,
        history_limit=history_limit,
        events=events,
        lost_attribution_cache=lost_attribution_cache,
        batch_ctx=batch_ctx,
    )
