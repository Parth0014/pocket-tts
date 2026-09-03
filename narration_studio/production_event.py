"""Frozen production-ingestion event contract V1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REASONS = frozenset({"NEW_POST", "CONTENT_CHANGED"})
_FIELDS = frozenset(
    {
        "schema_version",
        "post_id",
        "content_hash",
        "reason",
    }
)


class ProductionEventError(ValueError):
    """Raised when a production ingestion event violates V1."""


@dataclass(frozen=True, slots=True)
class ProductionIngestionEventV1:
    post_id: str
    content_hash: str
    reason: str
    schema_version: int = 1


def validate_production_event_v1(
    value: Mapping[str, Any],
) -> ProductionIngestionEventV1:
    if not isinstance(value, Mapping):
        raise ProductionEventError(
            "production event must be an object"
        )

    if set(value) != _FIELDS:
        raise ProductionEventError(
            "production event contains unexpected fields"
        )

    schema_version = value.get("schema_version")

    if type(schema_version) is not int or schema_version != 1:
        raise ProductionEventError(
            "schema_version must be integer 1"
        )

    post_id = value.get("post_id")

    if (
        not isinstance(post_id, str)
        or _POST_ID_RE.fullmatch(post_id) is None
    ):
        raise ProductionEventError(
            "post_id is not worker-safe Ghost identity"
        )

    content_hash = value.get("content_hash")

    if (
        not isinstance(content_hash, str)
        or _HASH_RE.fullmatch(content_hash) is None
    ):
        raise ProductionEventError(
            "content_hash must be lowercase SHA-256"
        )

    reason = value.get("reason")

    if reason not in _REASONS:
        raise ProductionEventError(
            "reason must be NEW_POST or CONTENT_CHANGED"
        )

    return ProductionIngestionEventV1(
        post_id=post_id,
        content_hash=content_hash,
        reason=reason,
    )
