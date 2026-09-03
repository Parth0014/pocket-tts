import hashlib
import json
from types import SimpleNamespace

import pytest

from narration_studio.models import StudioContractError
from narration_studio.worker_contract import (
    DEV_BUCKET,
    build_worker_job_for_generation,
    build_worker_job_v1,
    canonical_job_json,
    job_fingerprint,
    prepare_worker_voice_reference,
    validate_worker_job_v1,
    worker_output_key,
    worker_voice_reference_key,
)

JOB_ID = "job_" + ("1" * 32)
GENERATION_ID = "gen_" + ("2" * 32)
VOICE_ID = "voice_" + ("3" * 32)
QUOTE_VOICE_ID = "voice_" + ("4" * 32)
POST_ID = "ghostpost123"
CONTENT_HASH = "a" * 64


def test_worker_paths_are_frozen():
    assert worker_voice_reference_key(VOICE_ID) == (
        f"voices/{VOICE_ID}/reference.wav"
    )
    assert worker_output_key(GENERATION_ID) == (
        f"generations/{GENERATION_ID}/output.wav"
    )


def test_worker_job_exact_preserve_contract():
    job = build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
    )

    assert set(job) == {
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
    assert job["post"] == {
        "bucket": DEV_BUCKET,
        "key": f"ghost/{POST_ID}/{CONTENT_HASH}.html",
    }
    assert job["voice"] == {
        "voice_id": VOICE_ID,
        "bucket": DEV_BUCKET,
        "key": f"voices/{VOICE_ID}/reference.wav",
    }
    assert job["output"] == {
        "bucket": DEV_BUCKET,
        "key": f"generations/{GENERATION_ID}/output.wav",
    }


def test_two_voice_contract_is_exact():
    job = build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="two_voice",
        quote_voice_id=QUOTE_VOICE_ID,
    )
    assert job["quote_voice"] == {
        "voice_id": QUOTE_VOICE_ID,
        "bucket": DEV_BUCKET,
        "key": f"voices/{QUOTE_VOICE_ID}/reference.wav",
    }


def test_quote_voice_is_required_only_for_two_voice():
    with pytest.raises(StudioContractError):
        build_worker_job_v1(
            job_id=JOB_ID,
            generation_id=GENERATION_ID,
            post_id=POST_ID,
            content_hash=CONTENT_HASH,
            voice_id=VOICE_ID,
            quote_mode="two_voice",
        )

    with pytest.raises(StudioContractError):
        build_worker_job_v1(
            job_id=JOB_ID,
            generation_id=GENERATION_ID,
            post_id=POST_ID,
            content_hash=CONTENT_HASH,
            voice_id=VOICE_ID,
            quote_mode="preserve",
            quote_voice_id=QUOTE_VOICE_ID,
        )


def test_unknown_fields_fail_closed():
    job = build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
    )
    job["temperature"] = 0.5
    with pytest.raises(StudioContractError):
        validate_worker_job_v1(job)


def test_canonical_body_and_fingerprint_are_stable():
    job = build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
    )
    body = canonical_job_json(job)
    reordered = {key: job[key] for key in reversed(list(job))}
    assert canonical_job_json(reordered) == body
    assert job_fingerprint(job) == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert json.loads(body) == job


def test_generation_builder_pins_revision_source():
    document = SimpleNamespace(sha256="b" * 64)
    generation = SimpleNamespace(
        generation_id=GENERATION_ID,
        doc_id="doc_" + ("5" * 32),
        document_revision=1,
        document=document,
        voice_id=VOICE_ID,
    )
    revision = SimpleNamespace(
        doc_id=generation.doc_id,
        revision=1,
        document=document,
        source_post_id=POST_ID,
        source_content_hash=CONTENT_HASH,
    )

    job = build_worker_job_for_generation(
        job_id=JOB_ID,
        generation=generation,
        revision=revision,
        quote_mode="exclude",
    )
    assert job["post_id"] == POST_ID
    assert job["content_hash"] == CONTENT_HASH


def test_generation_builder_rejects_pointer_drift():
    generation = SimpleNamespace(
        generation_id=GENERATION_ID,
        doc_id="doc_" + ("5" * 32),
        document_revision=1,
        document=SimpleNamespace(sha256="b" * 64),
        voice_id=VOICE_ID,
    )
    revision = SimpleNamespace(
        doc_id=generation.doc_id,
        revision=2,
        document=SimpleNamespace(sha256="b" * 64),
        source_post_id=POST_ID,
        source_content_hash=CONTENT_HASH,
    )

    with pytest.raises(StudioContractError):
        build_worker_job_for_generation(
            job_id=JOB_ID,
            generation=generation,
            revision=revision,
            quote_mode="preserve",
        )


def test_worker_voice_mirror_must_match_studio_sha():
    wav = b"RIFFworker-reference"
    digest = hashlib.sha256(wav).hexdigest()
    voice = SimpleNamespace(
        voice_id=VOICE_ID,
        reference_audio=SimpleNamespace(
            key=f"studio-voices/{VOICE_ID}/v000001/reference.wav",
            sha256=digest,
        ),
    )

    artifact = prepare_worker_voice_reference(
        voice=voice,
        wav_bytes=wav,
    )
    assert artifact.key == f"voices/{VOICE_ID}/reference.wav"
    assert artifact.metadata["sha256"] == digest

    with pytest.raises(StudioContractError):
        prepare_worker_voice_reference(
            voice=voice,
            wav_bytes=b"different",
        )
