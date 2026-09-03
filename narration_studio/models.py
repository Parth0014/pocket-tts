"""Strict Studio V1 domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z]+_[0-9a-f]{32}$")


class StudioContractError(ValueError):
    """Raised when Studio V1 domain data violates its contract."""


class RoomStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class VoiceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class GenerationStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def _require_nonempty(
    value: str,
    *,
    field: str,
    max_length: int = 512,
) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > max_length
    ):
        raise StudioContractError(
            f"{field} must be a non-empty string"
        )


def _require_prefixed_id(
    value: str,
    *,
    prefix: str,
    field: str,
) -> None:
    if (
        not isinstance(value, str)
        or not _ID_RE.fullmatch(value)
        or not value.startswith(prefix + "_")
    ):
        raise StudioContractError(
            f"{field} must be {prefix}_ followed by 32 lowercase hex characters"
        )


def _require_sha256(
    value: str,
    *,
    field: str,
) -> None:
    if (
        not isinstance(value, str)
        or not _SHA256_RE.fullmatch(value)
    ):
        raise StudioContractError(
            f"{field} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(
    value: int,
    *,
    field: str,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise StudioContractError(
            f"{field} must be a positive integer"
        )


def _require_utc_z(
    value: str,
    *,
    field: str,
) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StudioContractError(
            f"{field} must be a UTC RFC 3339 timestamp ending in Z"
        )

    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00"
        )
    except ValueError:
        raise StudioContractError(
            f"{field} must be a valid UTC RFC 3339 timestamp"
        ) from None

    if parsed.utcoffset() != timedelta(0):
        raise StudioContractError(
            f"{field} must represent UTC"
        )


@dataclass(frozen=True)
class ArtifactRef:
    bucket: str
    key: str
    sha256: str

    def __post_init__(self) -> None:
        _require_nonempty(
            self.bucket,
            field="bucket",
            max_length=255,
        )
        _require_nonempty(
            self.key,
            field="key",
            max_length=1024,
        )
        _require_sha256(
            self.sha256,
            field="sha256",
        )


@dataclass(frozen=True)
class RoomRecord:
    room_id: str
    owner_id: str
    title: str
    status: RoomStatus
    version: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_prefixed_id(
            self.room_id,
            prefix="room",
            field="room_id",
        )
        _require_nonempty(
            self.owner_id,
            field="owner_id",
            max_length=256,
        )
        _require_nonempty(
            self.title,
            field="title",
            max_length=240,
        )
        _require_positive_int(
            self.version,
            field="version",
        )
        _require_utc_z(
            self.created_at,
            field="created_at",
        )
        _require_utc_z(
            self.updated_at,
            field="updated_at",
        )


@dataclass(frozen=True)
class StudioDocumentRevision:
    room_id: str
    doc_id: str
    revision: int
    source_post_id: str
    source_content_hash: str
    source_narration_hash: str
    source_processor_version: int
    document: ArtifactRef
    created_at: str

    def __post_init__(self) -> None:
        _require_prefixed_id(
            self.room_id,
            prefix="room",
            field="room_id",
        )
        _require_prefixed_id(
            self.doc_id,
            prefix="doc",
            field="doc_id",
        )
        _require_positive_int(
            self.revision,
            field="revision",
        )
        _require_nonempty(
            self.source_post_id,
            field="source_post_id",
        )
        _require_sha256(
            self.source_content_hash,
            field="source_content_hash",
        )
        _require_sha256(
            self.source_narration_hash,
            field="source_narration_hash",
        )
        _require_positive_int(
            self.source_processor_version,
            field="source_processor_version",
        )
        _require_utc_z(
            self.created_at,
            field="created_at",
        )


@dataclass(frozen=True)
class VoiceRecord:
    voice_id: str
    display_name: str
    status: VoiceStatus
    version: int
    reference_audio: ArtifactRef
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_prefixed_id(
            self.voice_id,
            prefix="voice",
            field="voice_id",
        )
        _require_nonempty(
            self.display_name,
            field="display_name",
            max_length=160,
        )
        _require_positive_int(
            self.version,
            field="version",
        )
        _require_utc_z(
            self.created_at,
            field="created_at",
        )
        _require_utc_z(
            self.updated_at,
            field="updated_at",
        )


@dataclass(frozen=True)
class GenerationRecord:
    room_id: str
    generation_id: str
    doc_id: str
    document_revision: int
    document: ArtifactRef
    voice_id: str
    voice_version: int
    voice_reference_audio: ArtifactRef
    generation_input: ArtifactRef
    status: GenerationStatus
    version: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _require_prefixed_id(
            self.room_id,
            prefix="room",
            field="room_id",
        )
        _require_prefixed_id(
            self.generation_id,
            prefix="gen",
            field="generation_id",
        )
        _require_prefixed_id(
            self.doc_id,
            prefix="doc",
            field="doc_id",
        )
        _require_prefixed_id(
            self.voice_id,
            prefix="voice",
            field="voice_id",
        )
        _require_positive_int(
            self.document_revision,
            field="document_revision",
        )
        _require_positive_int(
            self.voice_version,
            field="voice_version",
        )
        _require_positive_int(
            self.version,
            field="version",
        )
        _require_utc_z(
            self.created_at,
            field="created_at",
        )
        _require_utc_z(
            self.updated_at,
            field="updated_at",
        )