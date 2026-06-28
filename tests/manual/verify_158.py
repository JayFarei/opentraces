#!/usr/bin/env python3
"""Grounded verifier for issue #158 — OTel ctx data lands in the bucket, usable.

Re-observes REAL bucket + CLI state (never the agent's own narration) on >=2
distinct, real, flushed OTel sessions. Authored from the issue's Definition of
Done; run against current `main` it goes RED (empty ctx text, dangling
content_hash, git-replay reads, no doctor coverage), and GREEN once the fix
lands.

Isolation: an isolated scratch HOME, so the scratch bucket lives at
``<scratch>/.opentraces/bucket`` and the captain's real bucket is never
touched. Real DATA is read from the real raw-bodies dir + real Claude session
transcripts (read-only, passed by explicit path).

Usage:
    .venv/bin/python tests/manual/verify_158.py            # default 2 sessions
    .venv/bin/python tests/manual/verify_158.py <small_sid> <big_sid>

Exit code 0 iff every REQUIRED check passes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REAL_HOME = Path.home()
REAL_RAW = REAL_HOME / ".opentraces" / "raw-bodies"
REAL_CLAUDE_PROJECTS = REAL_HOME / ".claude" / "projects"

# Default sessions: a small one (fast ingest, spine cross-check) + a big one
# (>200 steps, O(N) storage proof). Override via argv.
SMALL_SID = "aa18af43-9dfb-4b34-8ca9-cb0a3914a94b"
BIG_SID = "249d4d33-2c63-4661-96ed-e6330032d697"

CLI_TIMEOUT = 90  # ctx/flush over a large reconstruction can take a bit


class Verifier:
    def __init__(self, small_sid: str, big_sid: str) -> None:
        self.small_sid = small_sid
        self.big_sid = big_sid
        self.scratch = Path(tempfile.mkdtemp(prefix="ot158-"))
        self.home = self.scratch / "home"
        self.proj = self.scratch / "proj"
        self.home.mkdir(parents=True)
        self.proj.mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.home)}
        self.bucket = self.home / ".opentraces" / "bucket"
        self.results: list[tuple[str, bool, str]] = []
        self.slug: str | None = None
        self._jsonl_ok = False
        self._init_project()

    # -- infra ------------------------------------------------------------- #

    def _init_project(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.proj, env=self.env, check=True)
        subprocess.run(["git", "config", "user.email", "v@x"], cwd=self.proj, env=self.env, check=True)
        subprocess.run(["git", "config", "user.name", "v"], cwd=self.proj, env=self.env, check=True)
        (self.proj / "README.md").write_text("verify-158\n")
        subprocess.run(["git", "add", "-A"], cwd=self.proj, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.proj, env=self.env, check=True)
        # Opt the project into review so ingest + the bucket slug resolve.
        cp = self.cli("init", timeout=60)
        if cp.returncode != 0:
            print(f"  (opentraces init rc={cp.returncode}: {(cp.stderr or cp.stdout)[-200:]})", flush=True)

    def cli(self, *args: str, timeout: int = CLI_TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "opentraces", *args],
            cwd=self.proj, env=self.env, capture_output=True, text=True, timeout=timeout,
        )

    def cli_json(self, *args: str, timeout: int = CLI_TIMEOUT):
        cp = self.cli(*args, timeout=timeout)
        # L3: stdout must be pure JSON (no ---OPENTRACES_JSON--- preamble).
        try:
            return json.loads(cp.stdout), cp, None
        except json.JSONDecodeError as exc:
            return None, cp, f"non-JSON stdout ({exc}): {cp.stdout[:120]!r}"

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.results.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}", flush=True)

    def py(self, code: str, timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", code], cwd=self.proj, env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )

    # -- pipeline ---------------------------------------------------------- #

    def ingest_jsonl(self, sid: str) -> str | None:
        """Ingest a real JSONL session -> returns trace_id (or None)."""
        jsonl = self._find_jsonl(sid)
        if jsonl is None:
            return None
        code = (
            "import json;from pathlib import Path;"
            "from opentraces.core.ingest import ingest_one_session;"
            f"r=ingest_one_session(Path({str(jsonl)!r}),Path({str(self.proj)!r}));"
            "print(json.dumps({'action':r.action,'trace_id':getattr(r,'trace_id',None),'error':r.error}))"
        )
        cp = self.py(code, timeout=600)
        line = cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else ""
        try:
            out = json.loads(line)
            return out.get("trace_id")
        except json.JSONDecodeError:
            print("    ingest stderr:", cp.stderr[-400:])
            return None

    def flush_otel(self, sid: str, trace_id: str) -> dict:
        # Flush of a many-hundred-step session reconstructs from tens of
        # thousands of raw-body files + writes per-message blobs; allow time.
        data, cp, err = self.cli_json(
            "capture-otlp", "flush", "--from-raw-bodies",
            "--session", sid, "--project", str(self.proj),
            "--trace-id", trace_id, "--raw-bodies-dir", str(REAL_RAW), "--json",
            timeout=600,
        )
        if data is None:
            return {"ok": False, "error": err, "stderr": cp.stderr[-300:]}
        return data

    def _find_jsonl(self, sid: str) -> Path | None:
        hits = list(REAL_CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl"))
        return hits[0] if hits else None

    # -- bucket helpers ---------------------------------------------------- #

    def _discover_slug(self) -> str | None:
        croot = self.bucket / "contexts" / "v1"
        if not croot.exists():
            return None
        subs = [p.name for p in croot.iterdir() if p.is_dir()]
        return subs[0] if subs else None

    def _nodes(self, trace_id: str) -> list[dict]:
        slug = self.slug or self._discover_slug()
        if slug is None:
            return []
        p = self.bucket / "contexts" / "v1" / slug / trace_id / "nodes.jsonl"
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]

    def _layer_blob(self, layer_id: str) -> dict | None:
        slug = self.slug or self._discover_slug()
        if slug is None:
            return None
        h = layer_id.split(":", 1)[-1]
        p = self.bucket / "blobs" / "v1" / slug / "context" / h[:2] / f"{h}.json.gz"
        if not p.exists():
            return None
        return json.loads(gzip.decompress(p.read_bytes()))

    def _raw_blob_bytes(self, content_hash: str) -> bytes | None:
        slug = self.slug or self._discover_slug()
        if slug is None:
            return None
        h = content_hash.split(":", 1)[-1]
        p = self.bucket / "blobs" / "v1" / slug / "raw" / h[:2] / f"{h}.json.gz"
        if not p.exists():
            return None
        return gzip.decompress(p.read_bytes())

    def _otel_nodes(self, trace_id: str) -> list[dict]:
        """Nodes whose messages layer was captured via otel (full per-step)."""
        out = []
        for node in self._nodes(trace_id):
            blob = self._layer_blob(node.get("messages_layer_id", ""))
            if blob and blob.get("capture_method") == "otel":
                out.append(node)
        return out

    def _step_text(self, node: dict) -> list[str]:
        """Deref every message in a node's messages layer -> list of text blobs."""
        blob = self._layer_blob(node.get("messages_layer_id", ""))
        if not blob:
            return []
        texts = []
        for entry in (blob.get("content") or {}).get("messages", []):
            ch = entry.get("content_hash")
            raw = self._raw_blob_bytes(ch) if ch else None
            if raw:
                texts.append(raw.decode("utf-8", "replace"))
        return texts

    # -- checks ------------------------------------------------------------ #

    def check_2_roundtrip(self, trace_id: str) -> None:
        nodes = self._otel_nodes(trace_id)
        checked = ok = nonempty = 0
        for node in nodes[:5]:
            blob = self._layer_blob(node["messages_layer_id"])
            for entry in (blob.get("content") or {}).get("messages", [])[:6]:
                ch = entry.get("content_hash")
                raw = self._raw_blob_bytes(ch) if ch else None
                if raw is None:
                    continue
                checked += 1
                if "sha256:" + hashlib.sha256(raw).hexdigest() == ch:
                    ok += 1
                if len(raw.strip()) > 2:
                    nonempty += 1
        passed = checked > 0 and ok == checked and nonempty == checked
        self.record(
            "2_roundtrip_deref",
            passed,
            f"{ok}/{checked} content_hash==sha256(deref), {nonempty} non-empty",
        )

    def check_3_schema_usage(self, trace_id: str) -> None:
        nodes = self._otel_nodes(trace_id)
        schema_ok = usage_ok = 0
        for node in nodes:
            tr = self._layer_blob(node.get("tool_registry_layer_id", ""))
            if tr and any(
                isinstance(t, dict) and t.get("input_schema")
                for t in (tr.get("content") or {}).get("tools", [])
            ):
                schema_ok += 1
            rs = self._layer_blob(node.get("runtime_state_layer_id", ""))
            usage = (rs.get("content") or {}).get("usage") if rs else None
            if usage and any("token" in k for k in usage):
                usage_ok += 1
        passed = schema_ok > 0 and usage_ok > 0
        self.record(
            "3_input_schema_and_usage",
            passed,
            f"{schema_ok}/{len(nodes)} nodes w/ tool input_schema, {usage_ok} w/ usage tokens",
        )

    def check_4_linear_storage(self, trace_id: str) -> None:
        nodes = self._otel_nodes(trace_id)
        if len(nodes) <= 200:
            self.record("4_linear_storage", False, f"only {len(nodes)} steps (<200); use a bigger session")
            return
        # actual raw/ blob bytes
        slug = self.slug or self._discover_slug()
        raw_root = self.bucket / "blobs" / "v1" / slug / "raw"
        raw_total = sum(p.stat().st_size for p in raw_root.rglob("*.json.gz")) if raw_root.exists() else 0
        # cumulative O(N^2) baseline: sum of each step's full manifest length * a
        # nominal per-message size proxy. Use manifest entry counts.
        cumulative_entries = 0
        for node in nodes:
            blob = self._layer_blob(node["messages_layer_id"])
            cumulative_entries += len((blob.get("content") or {}).get("messages", [])) if blob else 0
        # unique content blobs
        unique = sum(1 for _ in raw_root.rglob("*.json.gz")) if raw_root.exists() else 0
        # Linear iff unique << cumulative references (dedup actually happened).
        ratio = cumulative_entries / max(1, unique)
        passed = raw_total > 0 and unique > 0 and ratio > 3.0 and unique < cumulative_entries
        self.record(
            "4_linear_storage",
            passed,
            f"{len(nodes)} steps, {cumulative_entries} manifest refs, {unique} unique raw blobs "
            f"(dedup {ratio:.1f}x), {raw_total:,} raw bytes",
        )

    def check_1_ctx_text(self, trace_id: str) -> None:
        # find two otel nodes at different steps
        nodes = sorted(self._otel_nodes(trace_id), key=lambda n: n.get("step_index") or 0)
        if len(nodes) < 2:
            self.record("1_ctx_step_text", False, f"<2 otel nodes ({len(nodes)})")
            return
        n1, n2 = nodes[len(nodes) // 3], nodes[2 * len(nodes) // 3]
        d1, cp1, e1 = self.cli_json("ctx", "show", n1["node_id"], "--full", "--project", str(self.proj), "--json")
        d2, cp2, e2 = self.cli_json("ctx", "show", n2["node_id"], "--full", "--project", str(self.proj), "--json")
        t1 = self._extract_ctx_text(d1)
        t2 = self._extract_ctx_text(d2)
        passed = bool(t1) and bool(t2) and t1 != t2
        self.record(
            "1_ctx_step_text",
            passed,
            f"ctx show step text len {len(t1)} vs {len(t2)}, differ={t1 != t2}"
            + (f" | err1={e1}" if e1 else "") + (f" | err2={e2}" if e2 else ""),
        )

    @staticmethod
    def _extract_ctx_text(payload) -> str:
        if not payload:
            return ""
        layers = payload.get("layers") or {}
        msgs = layers.get("messages") or {}
        content = msgs.get("content") or {}
        # hydrated text is what we want; accept either resolved text or rendered msgs
        out = []
        for m in content.get("messages", []):
            if isinstance(m, dict):
                txt = m.get("text") or m.get("content_text") or ""
                if txt:
                    out.append(txt if isinstance(txt, str) else json.dumps(txt))
        return "\n".join(out)

    def check_5_bucket_backed(self, trace_id: str) -> None:
        # Hide the git event ref; ctx step must still return non-empty fast.
        ref = "refs/opentraces/local/events/v1"
        tmp = "refs/opentraces/local/events/v1_HIDDEN"
        nodes = sorted(self._otel_nodes(trace_id), key=lambda n: n.get("step_index") or 0)
        if not nodes:
            self.record("5_bucket_backed_read", False, "no otel nodes")
            return
        node = nodes[len(nodes) // 2]
        subprocess.run(["git", "update-ref", tmp, ref], cwd=self.proj, env=self.env, capture_output=True, text=True)
        subprocess.run(["git", "update-ref", "-d", ref], cwd=self.proj, env=self.env, capture_output=True, text=True)
        try:
            t0 = time.time()
            d, cp, e = self.cli_json("ctx", "show", node["node_id"], "--full", "--project", str(self.proj), "--json")
            dt = time.time() - t0
            txt = self._extract_ctx_text(d)
            passed = bool(txt) and dt < 20
            self.record("5_bucket_backed_read", passed, f"git ref hidden -> ctx text len {len(txt)} in {dt:.1f}s" + (f" | err={e}" if e else ""))
        finally:
            subprocess.run(["git", "update-ref", ref, tmp], cwd=self.proj, env=self.env, capture_output=True, text=True)
            subprocess.run(["git", "update-ref", "-d", tmp], cwd=self.proj, env=self.env, capture_output=True, text=True)

    def check_6_spine(self, trace_id: str) -> None:
        cd, cp, ce = self.cli_json("ctx", "step", trace_id, "1", "--project", str(self.proj), "--json")
        td, tp, te = self.cli_json("trace", "get", trace_id, "--json")
        md, mp, me = self.cli_json("trace", "map", trace_id, "--json")
        ctx_tid = (cd or {}).get("node", {}).get("trace_id") or (cd or {}).get("trace_id")
        trace_tid = (td or {}).get("trace_id") or (td or {}).get("id") or (td or {}).get("trace", {}).get("trace_id")
        passed = ctx_tid == trace_id and trace_tid == trace_id and md is not None
        self.record(
            "6_spine_crosscheck",
            passed,
            f"ctx trace_id={ctx_tid}, trace get trace_id={trace_tid}, map_ok={md is not None}"
            + (f" | ctx_err={ce}" if ce else "") + (f" | get_err={te}" if te else ""),
        )

    def check_7_doctor(self, trace_id: str) -> None:
        d, cp, e = self.cli_json("doctor", "--json")
        if d is None:
            self.record("7_doctor_coverage", False, f"doctor --json NOT valid JSON (B6 preamble?): {e}")
            return
        ct = (d.get("doctor") or {}).get("context_tree") or d.get("context_tree") or {}
        cov = ct.get("coverage") or {}
        independent = len(self._nodes(trace_id))
        reported = cov.get("context_nodes_in_bucket")
        passed = (
            isinstance(reported, int)
            and independent > 0
            and reported >= independent
        )
        self.record(
            "7_doctor_coverage",
            passed,
            f"doctor --json valid (pure JSON); coverage.context_nodes_in_bucket={reported} >= independent {independent}",
        )

    def _bucket_digest(self) -> str | None:
        code = (
            "import json;from opentraces.core.bucket_store import bucket_manifest;"
            "m=bucket_manifest(write=False);"
            "print(json.dumps({'d': m.get('bucket_digest') or m.get('digest')}))"
        )
        cp = self.py(code, timeout=300)
        try:
            return json.loads(cp.stdout.strip().splitlines()[-1])["d"]
        except Exception:  # noqa: BLE001
            print("    digest stderr:", cp.stderr[-300:])
            return None

    def check_8_digest_and_jsonl(self, jsonl_trace_id: str | None) -> None:
        # Additive-only invariance: the bucket_digest must be UNCHANGED when
        # the context substrate (contexts/v1 + blobs/v1/*/context) AND the new
        # per-message raw/ blobs (blobs/v1/*/raw) are removed. If true, those
        # writes are digest-invisible -> a fix that only adds them cannot churn
        # any existing trace's digest (DoD#8). MUST run last (destructive).
        d_full = self._bucket_digest()
        removed = 0
        for sub in ("contexts/v1",):
            p = self.bucket / sub
            if p.exists():
                shutil.rmtree(p)
                removed += 1
        for slug_dir in (self.bucket / "blobs" / "v1").glob("*"):
            for sub in ("context", "raw"):
                p = slug_dir / sub
                if p.exists():
                    shutil.rmtree(p)
                    removed += 1
        d_stripped = self._bucket_digest()
        digest_ok = d_full is not None and d_full == d_stripped
        self.record(
            "8_digest_additive_invariant",
            digest_ok,
            f"bucket_digest unchanged when context+raw stripped ({removed} subtrees): "
            f"{str(d_full)[:20]} == {str(d_stripped)[:20]}",
        )
        # JSONL ctx reads still work (the ingested trace's tree) — checked
        # BEFORE the strip via a saved result.
        self.record("8_jsonl_reads_work", self._jsonl_ok, f"ingested-trace ctx tree returns nodes: {self._jsonl_ok}")

    # -- driver ------------------------------------------------------------ #

    def run(self) -> int:
        print(f"scratch HOME: {self.home}")
        print(f"=== Session A (small, ingest + spine): {self.small_sid} ===", flush=True)
        t_a = self.ingest_jsonl(self.small_sid)
        print(f"  ingested trace_id: {t_a}", flush=True)
        if t_a:
            rep = self.flush_otel(self.small_sid, t_a)
            print(f"  flush(A): {json.dumps({k: rep.get(k) for k in ('ok','nodes_count','message_blobs_written','projected_to_bucket','spine_joined','reason','error')})}", flush=True)
        self.slug = self._discover_slug()
        print(f"  slug: {self.slug}", flush=True)

        print(f"=== Session B (big, >200 steps): {self.big_sid} ===", flush=True)
        t_b = f"verify158-big-{self.big_sid[:8]}"
        rep_b = self.flush_otel(self.big_sid, t_b)
        print(f"  flush(B): {json.dumps({k: rep_b.get(k) for k in ('ok','nodes_count','message_blobs_written','projected_to_bucket','reason','error')})}", flush=True)

        print("=== CHECKS ===", flush=True)
        # storage/data checks on the big session
        self.check_2_roundtrip(t_b)
        self.check_3_schema_usage(t_b)
        self.check_4_linear_storage(t_b)
        # surface checks on the big session
        self.check_1_ctx_text(t_b)
        self.check_5_bucket_backed(t_b)
        self.check_7_doctor(t_b)
        # spine cross-check on the ingested small session
        if t_a:
            self.check_6_spine(t_a)
        else:
            self.record("6_spine_crosscheck", False, "ingest of session A failed; no real trace")
        # JSONL ctx read must be checked BEFORE check_8's destructive strip.
        self._jsonl_ok = False
        if t_a:
            d, cp, e = self.cli_json("ctx", "tree", t_a, "--project", str(self.proj), "--json")
            self._jsonl_ok = d is not None and len(d.get("nodes", [])) > 0
        self.check_8_digest_and_jsonl(t_a)

        print("\n=== SUMMARY ===", flush=True)
        passed = [n for n, ok, _ in self.results if ok]
        failed = [n for n, ok, _ in self.results if not ok]
        print(f"PASS {len(passed)} / {len(self.results)}", flush=True)
        if failed:
            print("FAILED:", ", ".join(failed), flush=True)
        return 0 if not failed else 1

    def cleanup(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)


def main() -> int:
    args = sys.argv[1:]
    keep = "--keep" in args
    positional = [a for a in args if not a.startswith("--")]
    small = positional[0] if len(positional) > 0 else SMALL_SID
    big = positional[1] if len(positional) > 1 else BIG_SID
    v = Verifier(small, big)
    try:
        return v.run()
    finally:
        if not keep:
            v.cleanup()
        else:
            print(f"(kept scratch at {v.scratch})")


if __name__ == "__main__":
    raise SystemExit(main())
