"""HuggingFace Features schema for opentraces datasets.

The dataset viewer reads ``dataset_infos.json`` and casts every streamed JSONL
row to that declared schema. If this file drifts from ``TraceRecord``, Hugging
Face raises ``CastError`` even when the underlying rows are valid.

To avoid another hand-maintained schema drift, generate the Features tree
directly from the Pydantic model. Dict-shaped fields stay as ``Json`` because
they are intentionally open-ended (for example ``metadata`` and attribution
contributors).
"""

from __future__ import annotations

from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from opentraces_schema.models import TraceRecord
from opentraces_schema.version import SCHEMA_VERSION

_V = lambda dtype: {"dtype": dtype, "_type": "Value"}  # noqa: E731
_JSON = {"_type": "Json"}

_SCALAR_DTYPES: dict[type, str] = {
    str: "string",
    int: "int64",
    float: "float64",
    bool: "bool",
}


def _strip_optional(annotation: Any) -> Any:
    """Return the non-None member of ``Optional[T]``, else ``annotation``."""
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return annotation
    args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
    if len(args) == 1:
        return args[0]
    return annotation


def _feature_for_literal(annotation: Any) -> dict[str, str]:
    values = tuple(v for v in get_args(annotation) if v is not None)
    if not values:
        return _JSON
    value_types = {type(v) for v in values}
    if len(value_types) == 1:
        only = next(iter(value_types))
        dtype = _SCALAR_DTYPES.get(only)
        if dtype is not None:
            return _V(dtype)
    return _JSON


def _feature_for_model(model_cls: type[BaseModel]) -> dict[str, Any]:
    return {
        name: _feature_for_annotation(field.annotation)
        for name, field in model_cls.model_fields.items()
    }


def _feature_for_annotation(annotation: Any) -> Any:
    annotation = _strip_optional(annotation)
    origin = get_origin(annotation)

    if origin is Literal:
        return _feature_for_literal(annotation)

    dtype = _SCALAR_DTYPES.get(annotation)
    if dtype is not None:
        return _V(dtype)

    if annotation is Any:
        return _JSON

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _feature_for_model(annotation)

    if origin is list:
        item_annotation = get_args(annotation)[0] if get_args(annotation) else Any
        return [_feature_for_annotation(item_annotation)]

    if origin is dict:
        return _JSON

    # Fall back to Json for any future open-ended typing shape instead of
    # silently under-declaring the schema and breaking HF streaming.
    return _JSON


def _dataset_version_info() -> dict[str, int | str]:
    try:
        major, minor, patch = (int(part) for part in SCHEMA_VERSION.split("."))
    except ValueError:
        major = minor = patch = 0
    return {
        "version_str": SCHEMA_VERSION,
        "major": major,
        "minor": minor,
        "patch": patch,
    }


HF_FEATURES: dict[str, Any] = _feature_for_model(TraceRecord)


def build_dataset_infos(repo_name: str) -> dict:
    """Build the dataset_infos.json dict for a given repo.

    Only declares `features` — HF computes splits, num_bytes, and num_examples
    itself after processing the shards. Providing placeholder zero-counts for
    splits causes NonMatchingSplitsSizesError when HF verifies the actual data.

    Args:
        repo_name: short repo name (e.g. "opentraces-runtime"), used as dataset_name.
    """
    return {
        "default": {
            "description": "",
            "citation": "",
            "homepage": "",
            "license": "cc-by-4.0",
            "features": HF_FEATURES,
            "builder_name": "json",
            "dataset_name": repo_name,
            "config_name": "default",
            "version": _dataset_version_info(),
        }
    }
