"""Pure Publisher preflight.

This module allocates no publication and writes no production bytes.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VOICE_ID_RE = re.compile(r"^voice_[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PUBLICATION_KEY_RE = re.compile(r"^v([0-9]{6})$")


class PublisherPreflightError(ValueError):
    """A generation is not currently publishable."""


def next_publication_key(
    existing_keys: list[str],
) -> str:
    highest = 0

    for key in existing_keys:
        match = _PUBLICATION_KEY_RE.fullmatch(key)
        if match is None:
            raise PublisherPreflightError(
                "existing publication_key is malformed"
            )
        highest = max(highest, int(match.group(1)))

    if highest >= 999999:
        raise PublisherPreflightError(
            "publication version space is exhausted"
        )

    return f"v{highest + 1:06d}"


def production_audio_key(
    *,
    post_id: str,
    voice_id: str,
    publication_key: str,
) -> str:
    if (
        not isinstance(post_id, str)
        or _POST_ID_RE.fullmatch(post_id) is None
    ):
        raise PublisherPreflightError("post_id is invalid")

    if (
        not isinstance(voice_id, str)
        or _VOICE_ID_RE.fullmatch(voice_id) is None
    ):
        raise PublisherPreflightError("voice_id is invalid")

    if _PUBLICATION_KEY_RE.fullmatch(
        publication_key
    ) is None:
        raise PublisherPreflightError(
            "publication_key is invalid"
        )

    return (
        f"narrations/{post_id}/{voice_id}/"
        f"{publication_key}.wav"
    )


def validate_ready_generation(
    *,
    generation: Mapping[str, Any],
    current_content_hash: str,
    voice_status: str,
) -> None:
    if generation.get("generation_status") != "COMPLETED":
        raise PublisherPreflightError(
            "generation execution is not COMPLETED"
        )

    if generation.get("review_status") != "READY":
        raise PublisherPreflightError(
            "generation review state is not READY"
        )

    source_hash = generation.get("source_content_hash")

    if (
        not isinstance(source_hash, str)
        or _HASH_RE.fullmatch(source_hash) is None
    ):
        raise PublisherPreflightError(
            "generation source_content_hash is invalid"
        )

    if source_hash != current_content_hash:
        raise PublisherPreflightError(
            "generation content is no longer current"
        )

    if voice_status != "ACTIVE":
        raise PublisherPreflightError(
            "generation voice is not ACTIVE"
        )

    output_bucket = generation.get("output_bucket")
    output_key = generation.get("output_key")
    output_sha256 = generation.get("output_sha256")

    if (
        output_bucket != "pocket-tts-dev-test"
        or not isinstance(output_key, str)
        or output_key
        != (
            "generations/"
            + str(generation.get("generation_id"))
            + "/output.wav"
        )
        or not isinstance(output_sha256, str)
        or _HASH_RE.fullmatch(output_sha256) is None
    ):
        raise PublisherPreflightError(
            "completed DEV output identity is invalid"
        )
