"""Studio V1 orchestration across immutable artifacts and metadata."""

from __future__ import annotations

from typing import Any, Mapping

from .core import (
    PreparedGeneration,
    PreparedStudioRevision,
    prepare_generation_input,
    prepare_imported_revision,
    prepare_voice_reference,
)
from .models import (
    RoomRecord,
    StudioDocumentRevision,
    VoiceRecord,
    VoiceStatus,
)


class StudioService:
    """Coordinates Studio V1 durable write ordering."""

    def __init__(
        self,
        *,
        artifacts,
        studio_repository,
        voice_repository,
        bucket_name: str,
    ) -> None:
        self._artifacts = artifacts
        self._studio_repository = studio_repository
        self._voice_repository = voice_repository
        self._bucket_name = bucket_name

    def create_room(
        self,
        room: RoomRecord,
    ) -> RoomRecord:
        self._studio_repository.create_room(
            room
        )
        return room

    def import_narration_document(
        self,
        *,
        room_id: str,
        doc_id: str,
        revision: int,
        narration_document: Mapping[str, Any],
        created_at: str,
    ) -> PreparedStudioRevision:
        prepared = prepare_imported_revision(
            room_id=room_id,
            doc_id=doc_id,
            revision=revision,
            narration_document=narration_document,
            bucket=self._bucket_name,
            created_at=created_at,
        )

        self._artifacts.put_immutable(
            prepared.artifact
        )

        self._studio_repository.record_document_revision(
            prepared.revision
        )

        return prepared

    def register_voice(
        self,
        *,
        voice_id: str,
        display_name: str,
        version: int,
        wav_bytes: bytes,
        status: VoiceStatus,
        created_at: str,
    ) -> VoiceRecord:
        prepared = prepare_voice_reference(
            voice_id=voice_id,
            version=version,
            wav_bytes=wav_bytes,
            bucket=self._bucket_name,
        )

        voice = VoiceRecord(
            voice_id=voice_id,
            display_name=display_name,
            status=status,
            version=version,
            reference_audio=prepared.reference,
            created_at=created_at,
            updated_at=created_at,
        )

        self._artifacts.put_immutable(
            prepared.artifact
        )

        self._voice_repository.create_voice(
            voice
        )

        return voice

    def create_generation(
        self,
        *,
        room_id: str,
        generation_id: str,
        revision: StudioDocumentRevision,
        voice: VoiceRecord,
        quote_mode: str,
        quote_voice: VoiceRecord | None,
        created_at: str,
    ) -> PreparedGeneration:
        prepared = prepare_generation_input(
            room_id=room_id,
            generation_id=generation_id,
            revision=revision,
            voice=voice,
            quote_mode=quote_mode,
            quote_voice=quote_voice,
            bucket=self._bucket_name,
            created_at=created_at,
        )

        self._artifacts.put_immutable(
            prepared.artifact
        )

        self._studio_repository.create_generation(
            prepared.generation
        )

        return prepared
