"""Pure Studio V1 preparation logic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from narration_content.validation import validate_document

from .models import (
    ArtifactRef,
    GenerationRecord,
    GenerationStatus,
    StudioContractError,
    StudioDocumentRevision,
    VoiceRecord,
    VoiceStatus,
    _require_positive_int,
    _require_prefixed_id,
)


@dataclass(frozen=True)
class PreparedArtifact:
    key: str
    body: bytes
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class PreparedStudioRevision:
    revision: StudioDocumentRevision
    artifact: PreparedArtifact


@dataclass(frozen=True)
class PreparedVoiceReference:
    reference: ArtifactRef
    artifact: PreparedArtifact


@dataclass(frozen=True)
class PreparedGeneration:
    generation: GenerationRecord
    artifact: PreparedArtifact


def canonical_json_bytes(
    value: Mapping[str, Any],
) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(
    value: bytes,
) -> str:
    return hashlib.sha256(value).hexdigest()


def studio_document_key(
    room_id: str,
    doc_id: str,
    revision: int,
) -> str:
    _require_prefixed_id(
        room_id,
        prefix="room",
        field="room_id",
    )
    _require_prefixed_id(
        doc_id,
        prefix="doc",
        field="doc_id",
    )
    _require_positive_int(
        revision,
        field="revision",
    )

    return (
        f"studio-documents/{room_id}/{doc_id}/"
        f"v{revision:06d}.json"
    )


def generation_input_key(
    room_id: str,
    generation_id: str,
) -> str:
    _require_prefixed_id(
        room_id,
        prefix="room",
        field="room_id",
    )
    _require_prefixed_id(
        generation_id,
        prefix="gen",
        field="generation_id",
    )

    return (
        f"studio-generation-inputs/"
        f"{room_id}/{generation_id}.json"
    )


def voice_reference_key(
    voice_id: str,
    version: int,
) -> str:
    _require_prefixed_id(
        voice_id,
        prefix="voice",
        field="voice_id",
    )
    _require_positive_int(
        version,
        field="version",
    )

    return (
        f"studio-voices/{voice_id}/"
        f"v{version:06d}/reference.wav"
    )


def prepare_imported_revision(
    *,
    room_id: str,
    doc_id: str,
    revision: int,
    narration_document: Mapping[str, Any],
    bucket: str,
    created_at: str,
) -> PreparedStudioRevision:
    validate_document(
        narration_document,
        verify_hash=True,
    )

    source_post_id = narration_document["post_id"]
    source_content_hash = narration_document[
        "content_hash"
    ]
    source_narration_hash = narration_document[
        "narration_hash"
    ]
    source_processor_version = narration_document[
        "processor_version"
    ]

    key = studio_document_key(
        room_id,
        doc_id,
        revision,
    )

    payload = {
        "schema_version": 1,
        "kind": "STUDIO_DOCUMENT_REVISION",
        "room_id": room_id,
        "doc_id": doc_id,
        "revision": revision,
        "source": {
            "kind": "GHOST_NARRATION_DOCUMENT",
            "post_id": source_post_id,
            "content_hash": source_content_hash,
            "narration_hash": source_narration_hash,
            "processor_version": source_processor_version,
        },
        "narration_document": narration_document,
        "created_at": created_at,
    }

    body = canonical_json_bytes(payload)
    digest = sha256_hex(body)

    reference = ArtifactRef(
        bucket=bucket,
        key=key,
        sha256=digest,
    )

    revision_record = StudioDocumentRevision(
        room_id=room_id,
        doc_id=doc_id,
        revision=revision,
        source_post_id=source_post_id,
        source_content_hash=source_content_hash,
        source_narration_hash=source_narration_hash,
        source_processor_version=source_processor_version,
        document=reference,
        created_at=created_at,
    )

    artifact = PreparedArtifact(
        key=key,
        body=body,
        metadata={
            "artifact-kind": "studio-document-v1",
            "room-id": room_id,
            "doc-id": doc_id,
            "revision": str(revision),
            "sha256": digest,
        },
    )

    return PreparedStudioRevision(
        revision=revision_record,
        artifact=artifact,
    )


def prepare_voice_reference(
    *,
    voice_id: str,
    version: int,
    wav_bytes: bytes,
    bucket: str,
) -> PreparedVoiceReference:
    _require_prefixed_id(
        voice_id,
        prefix="voice",
        field="voice_id",
    )
    _require_positive_int(
        version,
        field="version",
    )

    if not isinstance(wav_bytes, bytes):
        raise StudioContractError(
            "reference WAV must be bytes"
        )

    if (
        len(wav_bytes) < 12
        or wav_bytes[:4] != b"RIFF"
        or wav_bytes[8:12] != b"WAVE"
    ):
        raise StudioContractError(
            "reference audio must be a RIFF/WAVE file"
        )

    key = voice_reference_key(
        voice_id,
        version,
    )

    digest = sha256_hex(
        wav_bytes
    )

    reference = ArtifactRef(
        bucket=bucket,
        key=key,
        sha256=digest,
    )

    artifact = PreparedArtifact(
        key=key,
        body=wav_bytes,
        metadata={
            "artifact-kind": "voice-reference-wav",
            "voice-id": voice_id,
            "voice-version": str(version),
            "sha256": digest,
        },
    )

    return PreparedVoiceReference(
        reference=reference,
        artifact=artifact,
    )


def prepare_generation_input(
    *,
    room_id: str,
    generation_id: str,
    revision: StudioDocumentRevision,
    voice: VoiceRecord,
    bucket: str,
    created_at: str,
) -> PreparedGeneration:
    if revision.room_id != room_id:
        raise StudioContractError(
            "document revision does not belong to room"
        )

    if voice.status is not VoiceStatus.ACTIVE:
        raise StudioContractError(
            "generation requires an ACTIVE voice"
        )

    key = generation_input_key(
        room_id,
        generation_id,
    )

    payload = {
        "schema_version": 1,
        "kind": "STUDIO_GENERATION_INPUT",
        "generation_id": generation_id,
        "room_id": room_id,
        "document": {
            "doc_id": revision.doc_id,
            "revision": revision.revision,
            "bucket": revision.document.bucket,
            "key": revision.document.key,
            "sha256": revision.document.sha256,
        },
        "voice": {
            "voice_id": voice.voice_id,
            "version": voice.version,
            "display_name": voice.display_name,
            "reference_audio": {
                "bucket": voice.reference_audio.bucket,
                "key": voice.reference_audio.key,
                "sha256": voice.reference_audio.sha256,
            },
        },
        "created_at": created_at,
    }

    body = canonical_json_bytes(
        payload
    )

    digest = sha256_hex(
        body
    )

    input_reference = ArtifactRef(
        bucket=bucket,
        key=key,
        sha256=digest,
    )

    generation = GenerationRecord(
        room_id=room_id,
        generation_id=generation_id,
        doc_id=revision.doc_id,
        document_revision=revision.revision,
        document=revision.document,
        voice_id=voice.voice_id,
        voice_version=voice.version,
        voice_reference_audio=voice.reference_audio,
        generation_input=input_reference,
        status=GenerationStatus.READY,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )

    artifact = PreparedArtifact(
        key=key,
        body=body,
        metadata={
            "artifact-kind": "studio-generation-input-v1",
            "room-id": room_id,
            "generation-id": generation_id,
            "document-sha256": revision.document.sha256,
            "voice-id": voice.voice_id,
            "voice-version": str(voice.version),
            "voice-reference-sha256": (
                voice.reference_audio.sha256
            ),
            "sha256": digest,
        },
    )

    return PreparedGeneration(
        generation=generation,
        artifact=artifact,
    )