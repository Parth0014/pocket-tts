"""Pure Studio Bridge V1 state preparation.

The bridge creates manager-intake state only. It never chooses a voice,
creates a generation, dispatches TTS, or publishes audio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StudioBridgeContractError(ValueError):
    """Bridge state violates the internal V1 contract."""


@dataclass(frozen=True, slots=True)
class BridgeIntake:
    receipt: Mapping[str, Any]
    current: Mapping[str, Any]


def _require_post_id(post_id: str) -> str:
    if (
        not isinstance(post_id, str)
        or _POST_ID_RE.fullmatch(post_id) is None
    ):
        raise StudioBridgeContractError(
            "post_id is invalid"
        )

    return post_id


def _require_hash(
    value: str,
    *,
    field: str,
) -> str:
    if (
        not isinstance(value, str)
        or _HASH_RE.fullmatch(value) is None
    ):
        raise StudioBridgeContractError(
            f"{field} must be lowercase SHA-256"
        )

    return value


def bridge_receipt_key(
    post_id: str,
    content_hash: str,
) -> tuple[str, str]:
    post_id = _require_post_id(post_id)
    content_hash = _require_hash(
        content_hash,
        field="content_hash",
    )

    return (
        f"POST#{post_id}",
        f"BRIDGE#{content_hash}",
    )


def bridge_current_key(
    post_id: str,
) -> tuple[str, str]:
    post_id = _require_post_id(post_id)

    return (
        f"POST#{post_id}",
        "BRIDGE#CURRENT",
    )


def narration_document_key(
    *,
    post_id: str,
    content_hash: str,
    processor_version: int,
    narration_hash: str,
) -> str:
    post_id = _require_post_id(post_id)
    content_hash = _require_hash(
        content_hash,
        field="content_hash",
    )
    narration_hash = _require_hash(
        narration_hash,
        field="narration_hash",
    )

    if (
        isinstance(processor_version, bool)
        or not isinstance(processor_version, int)
        or not 1 <= processor_version <= 999999
    ):
        raise StudioBridgeContractError(
            "processor_version is invalid"
        )

    return (
        f"narration-documents/{post_id}/"
        f"{content_hash}/"
        f"p{processor_version:06d}/"
        f"{narration_hash}.json"
    )


def prepare_bridge_intake(
    *,
    post_id: str,
    content_hash: str,
    narration_hash: str,
    processor_version: int,
    raw_bucket: str,
    raw_key: str,
    document_bucket: str,
    document_key: str,
    reason: str,
    title: str | None,
    slug: str | None,
    url: str | None,
    ghost_updated_at: str | None,
    observed_at: str,
) -> BridgeIntake:
    receipt_pk, receipt_sk = bridge_receipt_key(
        post_id,
        content_hash,
    )
    current_pk, current_sk = bridge_current_key(
        post_id
    )

    _require_hash(
        narration_hash,
        field="narration_hash",
    )

    if reason not in {
        "NEW_POST",
        "CONTENT_CHANGED",
    }:
        raise StudioBridgeContractError(
            "reason is invalid"
        )

    if (
        isinstance(processor_version, bool)
        or not isinstance(processor_version, int)
        or not 1 <= processor_version <= 999999
    ):
        raise StudioBridgeContractError(
            "processor_version is invalid"
        )

    for field, value in (
        ("raw_bucket", raw_bucket),
        ("raw_key", raw_key),
        ("document_bucket", document_bucket),
        ("document_key", document_key),
        ("observed_at", observed_at),
    ):
        if not isinstance(value, str) or not value:
            raise StudioBridgeContractError(
                f"{field} is required"
            )

    receipt: dict[str, Any] = {
        "pk": receipt_pk,
        "sk": receipt_sk,
        "entity_type": "studio_bridge_intake",
        "schema_version": 1,
        "bridge_status": "INGESTED",
        "post_id": post_id,
        "content_hash": content_hash,
        "narration_hash": narration_hash,
        "processor_version": processor_version,
        "raw_bucket": raw_bucket,
        "raw_key": raw_key,
        "document_bucket": document_bucket,
        "document_key": document_key,
        "first_event_reason": reason,
        "last_event_reason": reason,
        "created_at": observed_at,
        "updated_at": observed_at,
        "last_seen_at": observed_at,
    }

    for field, value in (
        ("title", title),
        ("slug", slug),
        ("url", url),
        ("ghost_updated_at", ghost_updated_at),
    ):
        if value is not None:
            receipt[field] = value

    current: dict[str, Any] = {
        "pk": current_pk,
        "sk": current_sk,
        "entity_type": "studio_bridge_current",
        "schema_version": 1,
        "bridge_status": "INGESTED",
        "post_id": post_id,
        "content_hash": content_hash,
        "narration_hash": narration_hash,
        "processor_version": processor_version,
        "raw_bucket": raw_bucket,
        "raw_key": raw_key,
        "document_bucket": document_bucket,
        "document_key": document_key,
        "event_reason": reason,
        "updated_at": observed_at,
    }

    for field, value in (
        ("title", title),
        ("slug", slug),
        ("url", url),
        ("ghost_updated_at", ghost_updated_at),
    ):
        if value is not None:
            current[field] = value

    # Deliberately absent: room_id, voice_id, generation_id, job_id,
    # publication_key and all production-audio fields.
    return BridgeIntake(
        receipt=receipt,
        current=current,
    )
