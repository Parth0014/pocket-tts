"""Frozen Studio -> pocket-tts-dev worker contract V1."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from .artifacts import PreparedArtifact
from .models import StudioContractError

DEV_BUCKET = "pocket-tts-dev-test"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")
_GEN_ID_RE = re.compile(r"^gen_[0-9a-f]{32}$")
_VOICE_ID_RE = re.compile(r"^voice_[0-9a-f]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_QUOTE_MODES = frozenset({"preserve", "exclude", "two_voice"})
_BASE_FIELDS = frozenset(
    {
        "schema_version",
        "job_id",
        "generation_id",
        "post_id",
        "content_hash",
        "post",
        "voice",
        "quote_mode",
        "output",
    }
)


def _require_string(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise StudioContractError(f"{name} must be a non-empty string")
    return value


def _require_safe_id(name: str, value: Any) -> str:
    value = _require_string(name, value)
    if _SAFE_ID_RE.fullmatch(value) is None:
        raise StudioContractError(f"{name} is not worker-safe")
    return value


def _require_producer_id(
    name: str,
    value: Any,
    pattern: re.Pattern[str],
) -> str:
    value = _require_safe_id(name, value)
    if pattern.fullmatch(value) is None:
        raise StudioContractError(
            f"{name} is not in canonical producer format"
        )
    return value


def _exact_mapping(
    name: str,
    value: Any,
    fields: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StudioContractError(f"{name} must be an object")
    if set(value) != fields:
        raise StudioContractError(f"{name} contains unexpected fields")
    return value


def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex


def ghost_snapshot_key(post_id: str, content_hash: str) -> str:
    post_id = _require_safe_id("post_id", post_id)
    if not isinstance(content_hash, str) or _HASH_RE.fullmatch(content_hash) is None:
        raise StudioContractError("content_hash must be lowercase SHA-256")
    return f"ghost/{post_id}/{content_hash}.html"


def worker_voice_reference_key(voice_id: str) -> str:
    voice_id = _require_producer_id("voice_id", voice_id, _VOICE_ID_RE)
    return f"voices/{voice_id}/reference.wav"


def worker_output_key(generation_id: str) -> str:
    generation_id = _require_producer_id(
        "generation_id",
        generation_id,
        _GEN_ID_RE,
    )
    return f"generations/{generation_id}/output.wav"


def prepare_worker_voice_reference(
    *,
    voice,
    wav_bytes: bytes,
    bucket: str = DEV_BUCKET,
) -> PreparedArtifact:
    if bucket != DEV_BUCKET:
        raise StudioContractError("worker voice bucket must be the DEV bucket")
    if not isinstance(wav_bytes, bytes):
        raise StudioContractError("wav_bytes must be bytes")

    voice_id = _require_producer_id(
        "voice_id",
        getattr(voice, "voice_id", None),
        _VOICE_ID_RE,
    )
    reference = getattr(voice, "reference_audio", None)
    expected_sha = getattr(reference, "sha256", None)
    if not isinstance(expected_sha, str) or _HASH_RE.fullmatch(expected_sha) is None:
        raise StudioContractError("voice reference SHA-256 is invalid")

    actual_sha = hashlib.sha256(wav_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise StudioContractError(
            "worker voice bytes do not match the Studio voice reference"
        )

    source_key = getattr(reference, "key", None)
    if not isinstance(source_key, str) or not source_key:
        raise StudioContractError("Studio voice reference key is invalid")

    return PreparedArtifact(
        key=worker_voice_reference_key(voice_id),
        body=wav_bytes,
        metadata={
            "sha256": actual_sha,
            "voice-id": voice_id,
            "studio-reference-key": source_key,
        },
    )


def build_worker_job_v1(
    *,
    job_id: str,
    generation_id: str,
    post_id: str,
    content_hash: str,
    voice_id: str,
    quote_mode: str,
    quote_voice_id: str | None = None,
    bucket: str = DEV_BUCKET,
) -> dict[str, Any]:
    if bucket != DEV_BUCKET:
        raise StudioContractError("worker job bucket must be the DEV bucket")

    job_id = _require_producer_id("job_id", job_id, _JOB_ID_RE)
    generation_id = _require_producer_id(
        "generation_id",
        generation_id,
        _GEN_ID_RE,
    )
    post_id = _require_safe_id("post_id", post_id)
    voice_id = _require_producer_id("voice_id", voice_id, _VOICE_ID_RE)

    if not isinstance(content_hash, str) or _HASH_RE.fullmatch(content_hash) is None:
        raise StudioContractError("content_hash must be lowercase SHA-256")
    if quote_mode not in _QUOTE_MODES:
        raise StudioContractError("quote_mode is invalid")

    job: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "generation_id": generation_id,
        "post_id": post_id,
        "content_hash": content_hash,
        "post": {
            "bucket": bucket,
            "key": ghost_snapshot_key(post_id, content_hash),
        },
        "voice": {
            "voice_id": voice_id,
            "bucket": bucket,
            "key": worker_voice_reference_key(voice_id),
        },
        "quote_mode": quote_mode,
        "output": {
            "bucket": bucket,
            "key": worker_output_key(generation_id),
        },
    }

    if quote_mode == "two_voice":
        if quote_voice_id is None:
            raise StudioContractError(
                "quote_voice_id is required for two_voice"
            )
        quote_voice_id = _require_producer_id(
            "quote_voice_id",
            quote_voice_id,
            _VOICE_ID_RE,
        )
        job["quote_voice"] = {
            "voice_id": quote_voice_id,
            "bucket": bucket,
            "key": worker_voice_reference_key(quote_voice_id),
        }
    elif quote_voice_id is not None:
        raise StudioContractError(
            "quote_voice_id is forbidden unless quote_mode is two_voice"
        )

    return validate_worker_job_v1(job)


def build_worker_job_for_generation(
    *,
    job_id: str,
    generation,
    revision,
    quote_mode: str,
    quote_voice_id: str | None = None,
    bucket: str = DEV_BUCKET,
) -> dict[str, Any]:
    if getattr(generation, "doc_id", None) != getattr(revision, "doc_id", None):
        raise StudioContractError("generation document identity mismatch")
    if getattr(generation, "document_revision", None) != getattr(
        revision,
        "revision",
        None,
    ):
        raise StudioContractError("generation document revision mismatch")

    generation_doc = getattr(generation, "document", None)
    revision_doc = getattr(revision, "document", None)
    if getattr(generation_doc, "sha256", None) != getattr(
        revision_doc,
        "sha256",
        None,
    ):
        raise StudioContractError("generation document SHA-256 mismatch")

    if getattr(generation, "quote_mode", quote_mode) != quote_mode:
        raise StudioContractError("generation quote_mode mismatch")

    if getattr(generation, "quote_voice_id", None) != quote_voice_id:
        raise StudioContractError(
            "generation quote voice mismatch"
        )

    return build_worker_job_v1(
        job_id=job_id,
        generation_id=getattr(generation, "generation_id", None),
        post_id=getattr(revision, "source_post_id", None),
        content_hash=getattr(revision, "source_content_hash", None),
        voice_id=getattr(generation, "voice_id", None),
        quote_mode=quote_mode,
        quote_voice_id=quote_voice_id,
        bucket=bucket,
    )


def validate_worker_job_v1(job: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise StudioContractError("worker job must be an object")

    quote_mode = job.get("quote_mode")
    expected_fields = set(_BASE_FIELDS)
    if quote_mode == "two_voice":
        expected_fields.add("quote_voice")
    if set(job) != expected_fields:
        raise StudioContractError("worker job contains unexpected fields")

    if type(job.get("schema_version")) is not int or job["schema_version"] != 1:
        raise StudioContractError("schema_version must be integer 1")

    job_id = _require_producer_id("job_id", job.get("job_id"), _JOB_ID_RE)
    generation_id = _require_producer_id(
        "generation_id",
        job.get("generation_id"),
        _GEN_ID_RE,
    )
    post_id = _require_safe_id("post_id", job.get("post_id"))
    content_hash = job.get("content_hash")
    if not isinstance(content_hash, str) or _HASH_RE.fullmatch(content_hash) is None:
        raise StudioContractError("content_hash must be lowercase SHA-256")
    if quote_mode not in _QUOTE_MODES:
        raise StudioContractError("quote_mode is invalid")

    post = _exact_mapping("post", job.get("post"), {"bucket", "key"})
    if post["bucket"] != DEV_BUCKET:
        raise StudioContractError("post bucket is not the DEV bucket")
    if post["key"] != ghost_snapshot_key(post_id, content_hash):
        raise StudioContractError("post key violates the V1 contract")

    voice = _exact_mapping(
        "voice",
        job.get("voice"),
        {"voice_id", "bucket", "key"},
    )
    voice_id = _require_producer_id(
        "voice.voice_id",
        voice.get("voice_id"),
        _VOICE_ID_RE,
    )
    if voice["bucket"] != DEV_BUCKET:
        raise StudioContractError("voice bucket is not the DEV bucket")
    if voice["key"] != worker_voice_reference_key(voice_id):
        raise StudioContractError("voice key violates the V1 contract")

    output = _exact_mapping("output", job.get("output"), {"bucket", "key"})
    if output["bucket"] != DEV_BUCKET:
        raise StudioContractError("output bucket is not the DEV bucket")
    if output["key"] != worker_output_key(generation_id):
        raise StudioContractError("output key violates the V1 contract")

    if quote_mode == "two_voice":
        quote_voice = _exact_mapping(
            "quote_voice",
            job.get("quote_voice"),
            {"voice_id", "bucket", "key"},
        )
        quote_voice_id = _require_producer_id(
            "quote_voice.voice_id",
            quote_voice.get("voice_id"),
            _VOICE_ID_RE,
        )
        if quote_voice["bucket"] != DEV_BUCKET:
            raise StudioContractError("quote voice bucket is invalid")
        if quote_voice["key"] != worker_voice_reference_key(quote_voice_id):
            raise StudioContractError("quote voice key violates the V1 contract")
    elif "quote_voice" in job:
        raise StudioContractError("quote_voice is forbidden for this quote_mode")

    normalized = json.loads(json.dumps(job, ensure_ascii=False))
    assert normalized["job_id"] == job_id
    assert normalized["generation_id"] == generation_id
    return normalized


def canonical_job_json(job: Mapping[str, Any]) -> str:
    return json.dumps(
        validate_worker_job_v1(job),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def job_fingerprint(job: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_job_json(job).encode("utf-8")
    ).hexdigest()
