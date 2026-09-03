"""Pocket TTS internal Narration Studio V1.

The package root intentionally uses lazy exports so lightweight surfaces such
as ``narration_studio.auth`` do not import the full document/TTS dependency
graph during module initialization.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ArtifactRef": ("narration_studio.models", "ArtifactRef"),
    "GenerationExecutionStatus": (
        "narration_studio.models",
        "GenerationExecutionStatus",
    ),
    "GenerationRecord": ("narration_studio.models", "GenerationRecord"),
    "GenerationReviewStatus": (
        "narration_studio.models",
        "GenerationReviewStatus",
    ),
    "PreparedArtifact": ("narration_studio.artifacts", "PreparedArtifact"),
    "PreparedGeneration": ("narration_studio.core", "PreparedGeneration"),
    "PreparedStudioRevision": ("narration_studio.core", "PreparedStudioRevision"),
    "PreparedVoiceReference": ("narration_studio.core", "PreparedVoiceReference"),
    "RoomRecord": ("narration_studio.models", "RoomRecord"),
    "RoomStatus": ("narration_studio.models", "RoomStatus"),
    "StudioContractError": ("narration_studio.models", "StudioContractError"),
    "StudioDocumentRevision": ("narration_studio.models", "StudioDocumentRevision"),
    "StudioService": ("narration_studio.service", "StudioService"),
    "VoiceRecord": ("narration_studio.models", "VoiceRecord"),
    "VoiceStatus": ("narration_studio.models", "VoiceStatus"),
    "prepare_generation_input": (
        "narration_studio.core",
        "prepare_generation_input",
    ),
    "prepare_imported_revision": (
        "narration_studio.core",
        "prepare_imported_revision",
    ),
    "prepare_voice_reference": (
        "narration_studio.core",
        "prepare_voice_reference",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)

    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    module_name, attribute_name = target
    value = getattr(
        import_module(module_name),
        attribute_name,
    )

    globals()[name] = value

    return value


def __dir__() -> list[str]:
    return sorted(
        set(globals()) | set(__all__)
    )
