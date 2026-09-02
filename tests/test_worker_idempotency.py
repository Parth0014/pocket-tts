import sys
from unittest.mock import patch

from botocore.exceptions import ClientError

import lambda_function as lf


CONTENT_HASH = "a" * 64

JOB = {
    "schema_version": 1,
    "job_id": "job_retry_v5",
    "generation_id": "gen_retry_v5",
    "post_id": "post_retry_v5",
    "content_hash": CONTENT_HASH,
    "post": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            "ghost/post_retry_v5/"
            + CONTENT_HASH
            + ".html"
        ),
    },
    "voice": {
        "voice_id": "voice_retry",
        "bucket": "pocket-tts-dev-test",
        "key": "voices/voice_retry/reference.wav",
    },
    "quote_mode": "preserve",
    "output": {
        "bucket": "pocket-tts-dev-test",
        "key": "generations/gen_retry_v5/output.wav",
    },
}


validated_job = lf._validate_job(
    JOB,
    require_schema_v1=True,
)

fingerprint = lf._job_fingerprint(validated_job)

assert len(fingerprint) == 64

bucket = validated_job["output"]["bucket"]
key = validated_job["output"]["key"]


# ---------------------------------------------------------
# TEST 1: object does not exist
# ---------------------------------------------------------

not_found = ClientError(
    {
        "Error": {
            "Code": "404",
            "Message": "Not Found",
        },
        "ResponseMetadata": {
            "HTTPStatusCode": 404,
        },
    },
    "HeadObject",
)

with patch.object(
    lf.s3,
    "head_object",
    side_effect=not_found,
):
    result = lf._existing_output_matches_job(
        bucket,
        key,
        fingerprint,
    )

assert result is False

print("TEST 1 PASS: missing output returns False")


# ---------------------------------------------------------
# TEST 2: matching existing output
# ---------------------------------------------------------

with patch.object(
    lf.s3,
    "head_object",
    return_value={
        "Metadata": {
            "pocket-schema-version": "1",
            "pocket-job-fingerprint": fingerprint,
        }
    },
):
    result = lf._existing_output_matches_job(
        bucket,
        key,
        fingerprint,
    )

assert result is True

print("TEST 2 PASS: matching fingerprint is accepted")


# ---------------------------------------------------------
# TEST 3: conflicting existing output
# ---------------------------------------------------------

with patch.object(
    lf.s3,
    "head_object",
    return_value={
        "Metadata": {
            "pocket-schema-version": "1",
            "pocket-job-fingerprint": "b" * 64,
        }
    },
):
    try:
        lf._existing_output_matches_job(
            bucket,
            key,
            fingerprint,
        )
    except RuntimeError as exc:
        assert "different or unverifiable job" in str(exc)
    else:
        raise AssertionError(
            "Conflicting output was incorrectly accepted"
        )

print("TEST 3 PASS: conflicting fingerprint is rejected")


# ---------------------------------------------------------
# TEST 4: retry exits before loading TTS
# ---------------------------------------------------------

sys.modules.pop(
    "generate_narration",
    None,
)

with patch.object(
    lf.s3,
    "head_object",
    return_value={
        "Metadata": {
            "pocket-schema-version": "1",
            "pocket-job-fingerprint": fingerprint,
        }
    },
):
    result = lf._process_job(
        JOB,
        require_schema_v1=True,
    )

assert result["status"] == "completed"
assert result["job_id"] == "job_retry_v5"
assert result["generation_id"] == "gen_retry_v5"

assert "generate_narration" not in sys.modules

print(
    "TEST 4 PASS: matching retry returns completed "
    "without loading TTS"
)

print()
print("ALL IDEMPOTENCY TESTS PASSED")

# ---------------------------------------------------------
# TEST 5: upload race with matching existing output
# ---------------------------------------------------------

import tempfile
from pathlib import Path


precondition_failed = ClientError(
    {
        "Error": {
            "Code": "PreconditionFailed",
            "Message": "At least one condition failed",
        },
        "ResponseMetadata": {
            "HTTPStatusCode": 412,
        },
    },
    "PutObject",
)


with tempfile.TemporaryDirectory() as temp_dir:
    wav_path = Path(temp_dir) / "output.wav"
    wav_path.write_bytes(b"fake-wav-data")

    with patch.object(
        lf.s3,
        "put_object",
        side_effect=precondition_failed,
    ), patch.object(
        lf.s3,
        "head_object",
        return_value={
            "Metadata": {
                "pocket-schema-version": "1",
                "pocket-job-fingerprint": fingerprint,
            }
        },
    ):
        result = lf._upload_s3_file_immutable(
            str(wav_path),
            bucket,
            key,
            job_fingerprint=fingerprint,
        )

assert result == "existing"

print(
    "TEST 5 PASS: matching 412 upload race "
    "is treated as idempotent success"
)


# ---------------------------------------------------------
# TEST 6: upload race with conflicting existing output
# ---------------------------------------------------------

with tempfile.TemporaryDirectory() as temp_dir:
    wav_path = Path(temp_dir) / "output.wav"
    wav_path.write_bytes(b"fake-wav-data")

    with patch.object(
        lf.s3,
        "put_object",
        side_effect=precondition_failed,
    ), patch.object(
        lf.s3,
        "head_object",
        return_value={
            "Metadata": {
                "pocket-schema-version": "1",
                "pocket-job-fingerprint": "b" * 64,
            }
        },
    ):
        try:
            lf._upload_s3_file_immutable(
                str(wav_path),
                bucket,
                key,
                job_fingerprint=fingerprint,
            )
        except RuntimeError as exc:
            assert (
                "different or unverifiable job"
                in str(exc)
                or "already exists" in str(exc)
            )
        else:
            raise AssertionError(
                "Conflicting 412 upload race "
                "was incorrectly accepted"
            )

print(
    "TEST 6 PASS: conflicting 412 upload race "
    "is rejected"
)

print()
print("ALL UPLOAD RACE TESTS PASSED")