"""Release-gate rollup (U11) — one command, one go/no-go verdict.

Composes the standing gates into a single envelope:

  (a) claims gate          — the vendored claims ledger validates clean
  (b) catalogue lint       — zero non-quarantined journey violations
  (c) jtbd drift           — Click <-> jtbd-command-map <-> journey SSoT (strict)
  (d) local run ledger     — red rows in --ledger <run-ledger.json> are red;
                             a verified claim whose journey verifier executed
                             FAIL/ERROR in that ledger is red
  (e) CI evidence          — optional --check-ci: nightly freshness via
                             gate_evidence.read_gate() + ci-pr conclusion for
                             HEAD via gh; offline reports unknown(offline),
                             never crashes

The envelope-budget check (tests/otbox/test_envelope_budgets.py) is NOT
re-run here — it builds a captured-world checkpoint. The ``make
release-gate`` target composes the pytest steps (claims gate, envelope
budgets, catalogue lint) ahead of this rollup.

Stdlib only. Run as ``python -m tests.otbox.release_gate [--ledger PATH]
[--check-ci] [--json]``. Exit 0 only on verdict "go"; "no-go" and
"unknown" exit 1 (fail-closed, like gate_evidence).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = "opentraces.release_gate.v1"


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


# ---------------------------------------------------------------------------
# (a) claims gate
# ---------------------------------------------------------------------------
def check_claims(reds: list[str]) -> tuple[dict, list]:
    from .claims_ledger import parse_claims_ledger, validate_ledger

    # Validate the FULL parse (fail-closed): malformed rows + header-counts
    # reconciliation are violations alongside the per-row rules (PR #63).
    parse = parse_claims_ledger()
    rows = list(parse.rows)
    problems = validate_ledger(parse)
    for p in problems:
        reds.append(f"claims: {p}")
    status = "pass" if not problems else "fail"
    return _check("claims-ledger", status, f"{len(rows)} claims, {len(problems)} violation(s)"), rows


# ---------------------------------------------------------------------------
# (b) catalogue lint
# ---------------------------------------------------------------------------
def check_catalogue_lint(reds: list[str]) -> dict:
    from .catalogue_lint import lint_catalogue

    failing, quarantined = lint_catalogue()
    for v in failing:
        reds.append(f"catalogue-lint: {v}")
    status = "pass" if not failing else "fail"
    return _check(
        "catalogue-lint", status,
        f"{len(failing)} failing, {len(quarantined)} quarantined",
    )


# ---------------------------------------------------------------------------
# (c) jtbd drift (strict)
# ---------------------------------------------------------------------------
def check_jtbd(reds: list[str]) -> dict:
    from .inventory import build_inventory

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
        out = Path(tf.name)
    try:
        _, _summary, drift = build_inventory(out_path=out, strict=True)
    finally:
        out.unlink(missing_ok=True)
    if not drift.ok:
        reds.append(f"jtbd: {drift.human_summary()}")
    return _check("jtbd-drift", "pass" if drift.ok else "fail", drift.human_summary())


# ---------------------------------------------------------------------------
# (d) local run ledger
# ---------------------------------------------------------------------------
def check_run_ledger(path: Path, claim_rows, reds: list[str]) -> tuple[dict, dict]:
    from .claims_ledger import derive_claim_statuses

    if not path.exists():
        reds.append(f"ledger: {path} does not exist")
        return _check("run-ledger", "fail", f"missing: {path}"), {}
    try:
        run_ledger = json.loads(path.read_text())
    except ValueError as exc:
        reds.append(f"ledger: {path} is not valid JSON: {exc}")
        return _check("run-ledger", "fail", f"unparseable: {path}"), {}

    ledger_rows = run_ledger.get("rows", [])
    red_rows = [r for r in ledger_rows if r.get("red")]
    for r in red_rows:
        reds.append(f"ledger: {r.get('journey')}: {r.get('red_reason', 'red')}")

    # A verified claim whose journey verifier executed FAIL/ERROR in this
    # run is a red — declared status contradicted by executed evidence.
    # (PENDING/SKIP stay informational: lane non-execution is already the
    # ledger's own SKIP-fails concern, and tier-1/live-lane verifiers
    # legitimately don't run in every ledger.)
    verdicts = {r.get("journey"): r.get("verdict") for r in ledger_rows}
    for row in claim_rows:
        if row.status != "verified":
            continue
        for j in row.journey_verifiers:
            if verdicts.get(j) in ("FAIL", "ERROR"):
                reds.append(
                    f"claims: {row.id} declared verified but verifier {j!r} "
                    f"executed {verdicts[j]} in {path.name}"
                )

    derived = derive_claim_statuses(run_ledger, claim_rows)
    status = "pass" if not red_rows else "fail"
    detail = f"{len(ledger_rows)} rows, {len(red_rows)} red ({path})"
    return _check("run-ledger", status, detail), derived


# ---------------------------------------------------------------------------
# (e) CI evidence (optional, offline-tolerant)
# ---------------------------------------------------------------------------
def _gh_reachable() -> bool:
    if shutil.which("gh") is None:
        return False
    try:
        res = subprocess.run(
            ["gh", "api", "rate_limit"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def check_ci(repo: str | None, reds: list[str]) -> list[dict]:
    if not _gh_reachable():
        return [
            _check("ci-nightly", "unknown", "unknown(offline)"),
            _check("ci-pr-head", "unknown", "unknown(offline)"),
        ]

    from .gate_evidence import read_gate

    checks = []
    try:
        gate = read_gate(repo=repo)
    except Exception as exc:  # noqa: BLE001 - offline tolerance is the contract
        checks.append(_check("ci-nightly", "unknown", f"unknown(offline): {exc}"))
    else:
        if gate.ok:
            checks.append(_check("ci-nightly", "pass", gate.reason))
        else:
            reds.append(f"ci-nightly: {gate.reason}")
            checks.append(_check("ci-nightly", "fail", gate.reason))

    checks.append(_check_ci_pr_head(repo, reds))
    return checks


def _check_ci_pr_head(repo: str | None, reds: list[str]) -> dict:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        args = ["gh", "run", "list", "--workflow", "ci-pr.yml", "--limit", "30",
                "--json", "headSha,status,conclusion"]
        if repo:
            args += ["--repo", repo]
        res = subprocess.run(args, capture_output=True, text=True, timeout=30)
        rows = json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = None
    if rows is None:
        return _check("ci-pr-head", "unknown", "unknown(offline)")
    match = next((r for r in rows if r.get("headSha") == head), None)
    if match is None:
        return _check("ci-pr-head", "unknown", f"no ci-pr run for HEAD {head[:12]}")
    if match.get("status") != "completed":
        return _check("ci-pr-head", "unknown", f"ci-pr for HEAD is {match.get('status')}")
    if match.get("conclusion") == "success":
        return _check("ci-pr-head", "pass", f"ci-pr success on HEAD {head[:12]}")
    reds.append(f"ci-pr-head: concluded {match.get('conclusion')!r} on HEAD {head[:12]}")
    return _check("ci-pr-head", "fail", f"concluded {match.get('conclusion')!r}")


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------
def _axes_summary(claim_rows) -> dict:
    from .claims_ledger import AXES, STATUS_VOCAB

    axes: dict[str, dict] = {}
    for letter, (_prefix, name) in AXES.items():
        counts = {s: 0 for s in sorted(STATUS_VOCAB)}
        total = 0
        for row in claim_rows:
            if row.axis != letter:
                continue
            total += 1
            if row.status in counts:
                counts[row.status] += 1
        axes[letter] = {"name": name, "total": total, **counts}
    return axes


def _claims_summary(claim_rows) -> dict:
    from .claims_ledger import STATUS_VOCAB

    counts = {s: 0 for s in ("verified", "partial", "open", "waived", "tracked")}
    for row in claim_rows:
        if row.status in STATUS_VOCAB:
            counts[row.status] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_gate", description="opentraces release-gate rollup (U11)"
    )
    parser.add_argument("--ledger", help="path to a local run-ledger.json")
    parser.add_argument("--check-ci", action="store_true",
                        help="also read nightly + ci-pr evidence via gh (offline-tolerant)")
    parser.add_argument("--repo", help="owner/repo override for gh calls")
    parser.add_argument("--json", action="store_true", help="emit the JSON envelope")
    args = parser.parse_args(argv)

    reds: list[str] = []
    checks: list[dict] = []

    claims_check, claim_rows = check_claims(reds)
    checks.append(claims_check)
    checks.append(check_catalogue_lint(reds))
    checks.append(check_jtbd(reds))

    derived: dict = {}
    if args.ledger:
        ledger_check, derived = check_run_ledger(Path(args.ledger), claim_rows, reds)
        checks.append(ledger_check)
    else:
        checks.append(_check("run-ledger", "skipped", "no --ledger provided"))

    if args.check_ci:
        checks.extend(check_ci(args.repo, reds))

    if reds:
        verdict = "no-go"
    elif any(c["status"] == "unknown" for c in checks):
        verdict = "unknown"
    else:
        verdict = "go"

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "axes": _axes_summary(claim_rows),
        "claims": _claims_summary(claim_rows),
        "checks": checks,
        "reds": reds,
        "verdict": verdict,
    }
    if derived:
        envelope["claims_derived"] = derived

    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        print(_human(envelope))
    return 0 if verdict == "go" else 1


def _human(envelope: dict) -> str:
    lines = ["release gate (opentraces.release_gate.v1)", ""]
    lines.append(f"  {'check':<16} {'status':<9} detail")
    for c in envelope["checks"]:
        lines.append(f"  {c['name']:<16} {c['status']:<9} {c['detail']}")
    lines.append("")
    counts = envelope["claims"]
    lines.append(
        "  claims: "
        + "  ".join(f"{k}={counts[k]}" for k in ("verified", "partial", "open", "waived", "tracked"))
    )
    lines.append(f"  {'axis':<6} {'name':<16} {'total':>5} {'verified':>9} {'partial':>8} {'open':>5} {'tracked':>8}")
    for letter, ax in envelope["axes"].items():
        lines.append(
            f"  {letter:<6} {ax['name']:<16} {ax['total']:>5} {ax['verified']:>9} "
            f"{ax['partial']:>8} {ax['open']:>5} {ax['tracked']:>8}"
        )
    if envelope["reds"]:
        lines.append("")
        lines.append(f"  {len(envelope['reds'])} red(s):")
        for r in envelope["reds"]:
            lines.append(f"    - {r}")
    lines.append("")
    lines.append("  note: envelope budgets run as a pytest step in `make release-gate`,")
    lines.append("        not re-run here (needs a built captured-world checkpoint).")
    lines.append("")
    lines.append(f"  verdict: {envelope['verdict']}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
