"""Regression: re-capturing a growing session must not keep every full copy.

Each capture of a still-growing session writes a new content-addressed raw blob
and repoints the session's link. Before the fix the predecessor was never
removed, so N captures of one session left N blobs whose total size is
O(N^2) in the transcript length.
"""

from __future__ import annotations

from pathlib import Path


def test_recaptured_growing_session_keeps_one_raw_blob(tmp_path: Path) -> None:
    from opentraces.core.bucket_layout import raw_sources_root
    from opentraces.core.bucket_raw_sources import write_raw_source_artifact

    source = tmp_path / "session.jsonl"
    lines: list[str] = []
    for turn in range(5):
        lines.append(f'{{"turn": {turn}, "text": "{"x" * 1000}"}}\n')
        source.write_text("".join(lines))
        link = write_raw_source_artifact(
            source,
            trace_id="growing",
            project_slug="demo",
            source_kind="claude-code-session-jsonl",
            parser="claude-code",
        )

    blobs = sorted((raw_sources_root() / "blobs").glob("*/*"))
    assert [p.name for p in blobs] == [Path(link["blob_path"]).name]
    assert sum(p.stat().st_size for p in blobs) == source.stat().st_size
