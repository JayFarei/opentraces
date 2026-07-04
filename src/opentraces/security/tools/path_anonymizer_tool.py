"""Path-anonymisation transformer.

Walks string fields plus path-shaped fields the walker does not visit —
``snippet.file_path``, ``attribution.files[].path``, and top-level
``record.metadata`` string entries — and rewrites local usernames to hashed
equivalents.
"""

from __future__ import annotations

import os
from typing import Any

from opentraces_schema import TraceRecord

from . import ToolContext, ToolInfo, ToolResult, cfg_block
from ..walker import FieldType, walk_string_fields
from ..anonymizer import anonymize_paths


class PathAnonymizerTransformer:
    name = "path_anonymizer"
    display_name = "Path anonymiser"
    kind = "transformer"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        if block is None:
            return False
        return bool(getattr(block, "enabled", False))

    def transform_text(
        self,
        text: str,
        *,
        cfg: Any = None,
        extra_usernames: list[str] | None = None,
        consume_tail: bool = True,
    ) -> str:
        """Per-string entry point for the companion path (#143 move 1).

        Unlike a ``Detector.find() -> spans``, this is a transform-in-place:
        it RETURNS the rewritten TEXT (``anonymize_paths`` already returns text),
        and the companion walker mutates the leaf with the returned string. It
        defaults to ``consume_tail=True`` (the aggressive home-path-tail mode)
        so a single ctx/trail leaf can be anonymized without constructing a whole
        ``TraceRecord``. The record-level ``apply`` below keeps the
        username-only (``consume_tail=False``) behavior.
        """
        if not text:
            return text
        username = os.environ.get("USER") or os.environ.get("USERNAME") or None
        extra = extra_usernames
        if extra is None and cfg is not None:
            extra = getattr(cfg, "custom_redact_strings", None) or None
        return anonymize_paths(
            text, username=username, extra_usernames=extra, consume_tail=consume_tail
        )

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        cfg = ctx.cfg
        username = os.environ.get("USER") or os.environ.get("USERNAME") or None
        extra_usernames = (getattr(cfg, "custom_redact_strings", None) or None) if cfg else None

        def _anon(text: str | None) -> str | None:
            if not text:
                return text
            return anonymize_paths(text, username=username, extra_usernames=extra_usernames)

        count = 0

        def _walker_transform(text: str, _path: str, _ft: FieldType) -> str:
            nonlocal count
            new = _anon(text) or text
            if new != text:
                count += 1
            return new

        walk_string_fields(record, _walker_transform)

        for k, v in list(record.metadata.items()):
            if isinstance(v, str):
                new = _anon(v) or v
                if new != v:
                    record.metadata[k] = new
                    count += 1

        for step in record.steps:
            for snippet in step.snippets:
                if snippet.file_path:
                    new = _anon(snippet.file_path) or snippet.file_path
                    if new != snippet.file_path:
                        snippet.file_path = new
                        count += 1

        if record.attribution:
            for attr_file in record.attribution.files:
                if attr_file.path:
                    new = _anon(attr_file.path) or attr_file.path
                    if new != attr_file.path:
                        attr_file.path = new
                        count += 1

        return ToolResult(
            name=self.name,
            kind=self.kind,
            redactions_applied=count,
            metadata_patch={"changes_applied": count},
        )

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state="enabled" if is_on else "disabled",
            detail="rewrites usernames in filesystem paths",
            setup_cmd=None,
            disable_cmd=None,
        )
