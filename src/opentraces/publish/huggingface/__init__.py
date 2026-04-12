"""HuggingFace Hub publishing: sharded JSONL upload, dataset cards, schema.

Public API re-exports so callers can do:
    from opentraces.publish.huggingface import HFUploader, generate_dataset_card
"""

from __future__ import annotations

from .upload import (
    HFUploader,
    RemoteShardError,
    UploadResult,
    verify_hf_token,
)
from .schema import build_dataset_infos
from .dataset_card import generate_dataset_card

__all__ = [
    "HFUploader",
    "RemoteShardError",
    "UploadResult",
    "verify_hf_token",
    "build_dataset_infos",
    "generate_dataset_card",
]
