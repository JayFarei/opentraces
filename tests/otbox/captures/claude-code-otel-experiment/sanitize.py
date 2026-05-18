"""Scrub identifiers from the two raw OTLP dumps so the artifact is safe to commit.

Replaces concrete user/org/session identifiers with sentinel placeholders,
preserving structural attributes (sequence numbers, durations, token counts,
attribute keys) so the analysis remains reproducible.

Also produces the merged ``raw-otel-emissions.jsonl`` artifact requested by
the experiment spec: every captured envelope from both test variants, in
chronological order, tagged with ``test_variant`` so a reader can filter
which run a record came from.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent

# Real values observed during the test session (captured from a Claude Code
# run authenticated against the experimenter's personal account). Replaced
# with deterministic placeholders before commit.
REPLACEMENTS = {
    "eca2912d-f45e-43da-98d1-9e8b56047fd8": "<org-uuid-redacted>",
    "user_01KbnwJhAYvvNauXuUVFM43Y": "<user-account-id-redacted>",
    "96a017e2-f320-4ced-b1b7-e8b8f614743f": "<user-account-uuid-redacted>",
    "fareiunastrage@gmail.com": "<user-email-redacted>",
    "68666d590b46a17e98378208dc02dc3f0eae9dc3c4b063f0bae07b77d6846957": "<device-id-redacted>",
    # Per-test session ids, derived dynamically below.
}
# Session IDs and prompt IDs vary per run; redact every UUID-shaped value
# that appears in identifier fields.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _scrub(value):
    if isinstance(value, str):
        for needle, replacement in REPLACEMENTS.items():
            value = value.replace(needle, replacement)
        # Keep UUID structure but make IDs deterministic per-original so
        # correlations across events still work.
        def _stable_id(match: re.Match) -> str:
            digest = hashlib.sha256(match.group(0).encode()).hexdigest()[:32]
            return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"

        return UUID_RE.sub(_stable_id, value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def scrub_jsonl(in_path: Path, variant: str) -> list[dict]:
    out = []
    with in_path.open() as handle:
        for line in handle:
            env = json.loads(line)
            env = _scrub(env)
            env["test_variant"] = variant
            out.append(env)
    return out


def main() -> None:
    merged: list[dict] = []
    merged.extend(scrub_jsonl(HERE / "test-a-default.jsonl", "A_default_content_off"))
    merged.extend(scrub_jsonl(HERE / "test-b-content-on.jsonl", "B_content_on"))

    out_path = HERE / "raw-otel-emissions.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for env in sorted(merged, key=lambda e: e["received_at"]):
            handle.write(json.dumps(env, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    print(f"wrote {out_path} ({len(merged)} envelopes)")

    # Sanitize the captured raw API body files too: replace user/account
    # identifiers in the metadata block and drop request bodies into a
    # subfolder readable by future agents reproducing the analysis. We keep
    # one full request and one full response as exhibits; the rest are too
    # large to commit and structurally identical.
    body_dir = HERE / "raw-body-files"
    if not body_dir.exists():
        return

    exhibits_dir = HERE / "raw-body-exhibits"
    exhibits_dir.mkdir(exist_ok=True)

    request_files = sorted(body_dir.glob("*.request.json"))
    response_files = sorted(body_dir.glob("*.response.json"))
    if request_files:
        body = json.loads(request_files[0].read_text())
        body = _scrub(body)
        (exhibits_dir / "sample.request.json").write_text(
            json.dumps(body, indent=2, sort_keys=True)
        )
        print(f"wrote exhibit: sample.request.json ({len(json.dumps(body))} chars)")
    if response_files:
        body = json.loads(response_files[0].read_text())
        body = _scrub(body)
        (exhibits_dir / "sample.response.json").write_text(
            json.dumps(body, indent=2, sort_keys=True)
        )
        print(f"wrote exhibit: sample.response.json ({len(json.dumps(body))} chars)")


if __name__ == "__main__":
    main()
