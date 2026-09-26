"""The pool holds JSON only (spec §5); this is the one codec between it and the engine.

`encode` tags the three non-JSON types a phase result can carry -- pydantic
models, paths and datetimes -- so a checkpoint's `json.dumps` never meets
them; `decode` turns the tags back into the typed values. Models are dumped
by field name, not alias: gates that need aliases get them from
`dispatch.gate_values`. Tuples come back as lists.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel

SUBTASK = "subtask"
SKIPPED = "skipped"


def encode(value: Any) -> Any:
    if isinstance(value, BaseModel):
        cls = type(value)
        return {
            "$model": f"{cls.__module__}:{cls.__qualname__}",
            "data": value.model_dump(mode="json"),
        }
    if isinstance(value, PurePath):
        return {"$path": str(value)}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "$model" in value:
            module, qualname = value["$model"].split(":")
            cls: Any = importlib.import_module(module)
            for part in qualname.split("."):
                cls = getattr(cls, part)
            return cls.model_validate(value["data"])
        if "$path" in value:
            return Path(value["$path"])
        if "$dt" in value:
            return datetime.fromisoformat(value["$dt"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value
