import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")


DEV_BUCKET = "pocket-tts-dev-test"

VALID_QUOTE_MODES = {
    "preserve",
    "exclude",
    "two_voice",
}

POST_READ_PREFIXES = (
    "input/",      # existing legacy test path
    "ghost/",      # future immutable Ghost snapshots
)

VOICE_READ_PREFIXES = (
    "voices/",
)

OUTPUT_WRITE_PREFIXES = (
    "test-results/",   # existing legacy test path
    "generations/",    # future Studio generations
    "voice-tests/",    # future voice tests
)


def _log(event_name, **fields):
    payload = {
        "event": event_name,
        **fields,
    }

    print(
        json.dumps(
            payload,
            sort_keys=True,
            default=str,
        )
    )


def _require_mapping(parent, name):
    value = parent.get(name)

    if not isinstance(value, dict):
        raise ValueError(
            f"{name} must be an object"
        )

    return value


def _require_string(parent, name):
    value = parent.get(name)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{name} must be a non-empty string"
        )

    return value.strip()


SAFE_ID_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789_-"
)


def _require_safe_id(parent, name):
    raw_value = parent.get(name)
    value = _require_string(parent, name)

    if raw_value != value:
        raise ValueError(
            f"{name} must not contain leading or trailing whitespace"
        )

    if (
        len(value) > 128
        or any(char not in SAFE_ID_CHARS for char in value)
    ):
        raise ValueError(
            f"{name} must match ^[A-Za-z0-9_-]{{1,128}}$"
        )

    return value


def _validate_s3_key(key, allowed_prefixes, expected_suffix=None):
    if not isinstance(key, str) or not key:
        raise ValueError("S3 key must be a non-empty string")

    if "\\" in key or key.startswith("/"):
        raise ValueError(
            f"Invalid S3 key: {key!r}"
        )

    parts = key.split("/")

    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            f"Unsafe S3 key: {key!r}"
        )

    if not any(
        key.startswith(prefix)
        for prefix in allowed_prefixes
    ):
        raise ValueError(
            f"S3 key is outside allowed prefixes: {key!r}"
        )

    if expected_suffix and not key.lower().endswith(expected_suffix):
        raise ValueError(
            f"S3 key must end with {expected_suffix}: {key!r}"
        )


def _validate_location(
    location,
    name,
    allowed_prefixes,
    expected_suffix,
):
    if not isinstance(location, dict):
        raise ValueError(
            f"{name} must be an object"
        )

    bucket = _require_string(location, "bucket")
    key = _require_string(location, "key")

    if bucket != DEV_BUCKET:
        raise ValueError(
            f"{name}.bucket must be {DEV_BUCKET!r}"
        )

    _validate_s3_key(
        key,
        allowed_prefixes,
        expected_suffix,
    )

    return {
        **location,
        "bucket": bucket,
        "key": key,
    }


V1_JOB_FIELDS = frozenset(
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
        "quote_voice",
    }
)

V1_LOCATION_FIELDS = frozenset(
    {
        "bucket",
        "key",
    }
)

V1_VOICE_FIELDS = frozenset(
    {
        "voice_id",
        "bucket",
        "key",
    }
)


def _reject_unknown_fields(parent, allowed_fields, name):
    unknown_fields = sorted(
        set(parent.keys()) - allowed_fields
    )

    if unknown_fields:
        raise ValueError(
            f"{name} contains unknown fields: "
            + ", ".join(unknown_fields)
        )


def _validate_job(job, require_schema_v1=False):
    if not isinstance(job, dict):
        raise ValueError(
            "Job payload must be a JSON object"
        )

    schema_version = job.get("schema_version")

    is_schema_v1 = (
        type(schema_version) is int
        and schema_version == 1
    )

    # Future SQS traffic must always use the explicit V1 contract.
    if require_schema_v1 and not is_schema_v1:
        raise ValueError(
            "SQS jobs must use schema_version 1"
        )

    # Direct invocation may still use the old test contract
    # until the old test harness is retired.
    if schema_version is not None and not is_schema_v1:
        raise ValueError(
            f"Unsupported schema_version: {schema_version!r}"
        )

    if is_schema_v1:
        _reject_unknown_fields(
            job,
            V1_JOB_FIELDS,
            "job",
        )

        if "quote_mode" not in job:
            raise ValueError(
                "quote_mode is required for schema_version 1"
            )

    quote_mode = job.get(
        "quote_mode",
        "preserve",
    )

    if quote_mode not in VALID_QUOTE_MODES:
        raise ValueError(
            "quote_mode must be one of: "
            "preserve, exclude, two_voice"
        )

    post_raw = _require_mapping(
        job,
        "post",
    )

    voice_raw = _require_mapping(
        job,
        "voice",
    )

    output_raw = _require_mapping(
        job,
        "output",
    )

    has_quote_voice = "quote_voice" in job
    quote_voice_raw = job.get("quote_voice")

    if is_schema_v1:
        _reject_unknown_fields(
            post_raw,
            V1_LOCATION_FIELDS,
            "post",
        )

        _reject_unknown_fields(
            voice_raw,
            V1_VOICE_FIELDS,
            "voice",
        )

        _reject_unknown_fields(
            output_raw,
            V1_LOCATION_FIELDS,
            "output",
        )

        if quote_mode == "two_voice":
            if not has_quote_voice:
                raise ValueError(
                    "quote_voice is required when "
                    "quote_mode is two_voice"
                )
        elif has_quote_voice:
            raise ValueError(
                "quote_voice must be absent unless "
                "quote_mode is two_voice"
            )

        if quote_mode == "two_voice":
            if not isinstance(quote_voice_raw, dict):
                raise ValueError(
                    "quote_voice must be an object"
                )

            _reject_unknown_fields(
                quote_voice_raw,
                V1_VOICE_FIELDS,
                "quote_voice",
            )

    post = _validate_location(
        post_raw,
        "post",
        POST_READ_PREFIXES,
        ".html",
    )

    voice = _validate_location(
        voice_raw,
        "voice",
        VOICE_READ_PREFIXES,
        ".wav",
    )

    output = _validate_location(
        output_raw,
        "output",
        OUTPUT_WRITE_PREFIXES,
        ".wav",
    )

    quote_voice = None

    if quote_voice_raw is not None:
        quote_voice = _validate_location(
            quote_voice_raw,
            "quote_voice",
            VOICE_READ_PREFIXES,
            ".wav",
        )

    if quote_mode == "two_voice" and quote_voice is None:
        raise ValueError(
            "quote_voice is required when quote_mode is two_voice"
        )

    # Preserve the legacy direct-invocation contract.
    if not is_schema_v1:
        validated = {
            **job,
            "post": post,
            "voice": voice,
            "output": output,
            "quote_mode": quote_mode,
        }

        if quote_voice is not None:
            validated["quote_voice"] = quote_voice

        return validated

    # -------------------------------
    # Frozen V1 Studio-generation contract
    # -------------------------------

    job_id = _require_safe_id(
        job,
        "job_id",
    )

    generation_id = _require_safe_id(
        job,
        "generation_id",
    )

    post_id = _require_safe_id(
        job,
        "post_id",
    )

    raw_content_hash = job.get("content_hash")

    content_hash = _require_string(
        job,
        "content_hash",
    )

    if (
        raw_content_hash != content_hash
        or len(content_hash) != 64
        or any(
            char not in "0123456789abcdef"
            for char in content_hash
        )
    ):
        raise ValueError(
            "content_hash must be a SHA-256 "
            "lowercase hex digest"
        )

    voice_id = _require_safe_id(
        voice_raw,
        "voice_id",
    )

    # V1 values are exact contract values. Do not silently
    # normalize surrounding whitespace in bucket/key strings.
    location_pairs = [
        ("post", post_raw, post),
        ("voice", voice_raw, voice),
        ("output", output_raw, output),
    ]

    if quote_voice is not None:
        location_pairs.append(
            (
                "quote_voice",
                quote_voice_raw,
                quote_voice,
            )
        )

    for location_name, raw_location, normalized_location in location_pairs:
        for field_name in ("bucket", "key"):
            if (
                raw_location.get(field_name)
                != normalized_location[field_name]
            ):
                raise ValueError(
                    f"{location_name}.{field_name} must not "
                    "contain leading or trailing whitespace"
                )

    expected_post_key = (
        f"ghost/{post_id}/{content_hash}.html"
    )

    if post["key"] != expected_post_key:
        raise ValueError(
            "V1 post key must be exactly "
            f"{expected_post_key!r}"
        )

    expected_output_key = (
        f"generations/{generation_id}/output.wav"
    )

    if output["key"] != expected_output_key:
        raise ValueError(
            "V1 generation output must be exactly "
            f"{expected_output_key!r}"
        )

    expected_voice_key = (
        f"voices/{voice_id}/reference.wav"
    )

    if voice["key"] != expected_voice_key:
        raise ValueError(
            "V1 voice key must be exactly "
            f"{expected_voice_key!r}"
        )

    quote_voice_id = None

    if quote_voice is not None:
        quote_voice_id = _require_safe_id(
            quote_voice_raw,
            "voice_id",
        )

        expected_quote_voice_key = (
            f"voices/{quote_voice_id}/reference.wav"
        )

        if quote_voice["key"] != expected_quote_voice_key:
            raise ValueError(
                "V1 quote_voice key must be exactly "
                f"{expected_quote_voice_key!r}"
            )

    # Construct a canonical object rather than returning **job.
    # The idempotency fingerprint therefore contains only frozen
    # V1 contract fields.
    validated = {
        "schema_version": 1,
        "job_id": job_id,
        "generation_id": generation_id,
        "post_id": post_id,
        "content_hash": content_hash,
        "post": {
            "bucket": post["bucket"],
            "key": post["key"],
        },
        "voice": {
            "voice_id": voice_id,
            "bucket": voice["bucket"],
            "key": voice["key"],
        },
        "quote_mode": quote_mode,
        "output": {
            "bucket": output["bucket"],
            "key": output["key"],
        },
    }

    if quote_voice is not None:
        validated["quote_voice"] = {
            "voice_id": quote_voice_id,
            "bucket": quote_voice["bucket"],
            "key": quote_voice["key"],
        }

    return validated

def _download_s3_file(bucket, key, destination):
    Path(destination).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    s3.download_file(
        bucket,
        key,
        destination,
    )

    return destination

def _job_fingerprint(job):
    """
    Return a deterministic SHA-256 fingerprint for the complete
    validated job payload.

    SQS may deliver the same message more than once. The fingerprint
    lets us distinguish a true retry from an accidental reuse of the
    same generation output key for a different job.
    """

    canonical_job = json.dumps(
        job,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        canonical_job.encode("utf-8")
    ).hexdigest()


def _get_existing_output_metadata(bucket, key):
    """
    Return S3 user metadata when an output object exists.

    Return None only when the object definitely does not exist.
    Other S3 errors are propagated.
    """

    try:
        response = s3.head_object(
            Bucket=bucket,
            Key=key,
        )

    except ClientError as exc:
        error_code = str(
            exc.response.get(
                "Error",
                {},
            ).get(
                "Code",
                "",
            )
        )

        status_code = (
            exc.response.get(
                "ResponseMetadata",
                {},
            ).get(
                "HTTPStatusCode"
            )
        )

        if error_code in {
            "404",
            "NoSuchKey",
            "NotFound",
        } or status_code == 404:
            return None

        raise

    return {
        str(key).lower(): str(value)
        for key, value in response.get(
            "Metadata",
            {},
        ).items()
    }


def _existing_output_matches_job(
    bucket,
    key,
    expected_fingerprint,
):

    metadata = _get_existing_output_metadata(
        bucket,
        key,
    )

    if metadata is None:
        return False

    actual_fingerprint = metadata.get(
        "pocket-job-fingerprint"
    )

    if actual_fingerprint == expected_fingerprint:
        return True

    raise RuntimeError(
        "Output object already exists but belongs "
        "to a different or unverifiable job: "
        f"s3://{bucket}/{key}"
    )

def _upload_s3_file_immutable(
    local_path,
    bucket,
    key,
    job_fingerprint=None,
):
    """
    Upload an object only when its key does not already exist.

    For V1 generation jobs, an existing object is accepted only when
    its stored job fingerprint matches the exact validated job. This
    makes SQS retries idempotent without permitting accidental
    overwrites.
    """

    put_args = {
        "Bucket": bucket,
        "Key": key,
        "ContentType": "audio/wav",
        "IfNoneMatch": "*",
    }

    if job_fingerprint is not None:
        put_args["Metadata"] = {
            "pocket-schema-version": "1",
            "pocket-job-fingerprint": job_fingerprint,
        }

    try:
        with open(local_path, "rb") as body:
            put_args["Body"] = body
            s3.put_object(**put_args)

        return "uploaded"

    except ClientError as exc:
        error_code = str(
            exc.response.get(
                "Error",
                {},
            ).get(
                "Code",
                "",
            )
        )

        status_code = (
            exc.response.get(
                "ResponseMetadata",
                {},
            ).get(
                "HTTPStatusCode"
            )
        )

        if error_code in {
            "PreconditionFailed",
            "412",
        } or status_code == 412:

            if (
                job_fingerprint is not None
                and _existing_output_matches_job(
                    bucket,
                    key,
                    job_fingerprint,
                )
            ):
                return "existing"

            raise RuntimeError(
                f"Output object already exists: "
                f"s3://{bucket}/{key}"
            ) from exc

        raise


def _process_job(job, require_schema_v1=False):
    raw_job = job

    try:
        job = _validate_job(
            raw_job,
            require_schema_v1=require_schema_v1,
        )

    except Exception as exc:
        raw_job_id = (
            raw_job.get("job_id")
            if isinstance(raw_job, dict)
            else None
        )

        raw_generation_id = (
            raw_job.get("generation_id")
            if isinstance(raw_job, dict)
            else None
        )

        _log(
            "job_rejected",
            job_id=raw_job_id,
            generation_id=raw_generation_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        raise

    job_id = job.get(
        "job_id",
        "legacy-direct-job",
    )

    generation_id = job.get(
        "generation_id",
        "legacy-direct-generation",
    )

    work_dir = tempfile.mkdtemp(
        prefix="pockettts-"
    )

    output_dir = os.path.join(
        work_dir,
        "output",
    )

    _log(
        "job_started",
        job_id=job_id,
        generation_id=generation_id,
        schema_version=job.get(
            "schema_version",
            "legacy",
        ),
        quote_mode=job["quote_mode"],
    )

    try:
        destination = job["output"]
        job_fingerprint = None

        if job.get("schema_version") == 1:
            job_fingerprint = _job_fingerprint(job)

            if _existing_output_matches_job(
                destination["bucket"],
                destination["key"],
                job_fingerprint,
            ):
                _log(
                    "job_already_completed",
                    job_id=job_id,
                    generation_id=generation_id,
                    output_key=destination["key"],
                )

                return {
                    "status": "completed",
                    "job_id": job_id,
                    "generation_id": generation_id,
                    "output": {
                        "bucket": destination["bucket"],
                        "key": destination["key"],
                    },
                }

        _log(
            "loading_narration_module",
            job_id=job_id,
            generation_id=generation_id,
        )

        from generate_narration import run_pipeline

        _log(
            "narration_module_loaded",
            job_id=job_id,
            generation_id=generation_id,
        )

        post_path = os.path.join(
            work_dir,
            "post.html",
        )

        voice_path = os.path.join(
            work_dir,
            "narration_voice.wav",
        )

        quote_voice_path = None

        _log(
            "downloading_inputs",
            job_id=job_id,
            generation_id=generation_id,
        )

        _download_s3_file(
            job["post"]["bucket"],
            job["post"]["key"],
            post_path,
        )

        _download_s3_file(
            job["voice"]["bucket"],
            job["voice"]["key"],
            voice_path,
        )

        if job.get("quote_voice"):
            quote_voice_path = os.path.join(
                work_dir,
                "quote_voice.wav",
            )

            _download_s3_file(
                job["quote_voice"]["bucket"],
                job["quote_voice"]["key"],
                quote_voice_path,
            )

        _log(
            "tts_started",
            job_id=job_id,
            generation_id=generation_id,
        )

        output_path = run_pipeline(
            post_html_file=post_path,
            narration_reference_audio=voice_path,
            quote_reference_audio=quote_voice_path,
            quote_mode=job["quote_mode"],
            output_dir=output_dir,
        )

        _log(
            "upload_started",
            job_id=job_id,
            generation_id=generation_id,
            output_key=destination["key"],
        )

        upload_result = _upload_s3_file_immutable(
            output_path,
            destination["bucket"],
            destination["key"],
            job_fingerprint=job_fingerprint,
        )

        if upload_result == "existing":
            _log(
                "job_already_completed",
                job_id=job_id,
                generation_id=generation_id,
                output_key=destination["key"],
            )
        else:
            _log(
                "job_completed",
                job_id=job_id,
                generation_id=generation_id,
                output_key=destination["key"],
            )

        return {
            "status": "completed",
            "job_id": job_id,
            "generation_id": generation_id,
            "output": {
                "bucket": destination["bucket"],
                "key": destination["key"],
            },
        }

    except Exception as exc:
        _log(
            "job_failed",
            job_id=job_id,
            generation_id=generation_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        raise

    finally:
        shutil.rmtree(
            work_dir,
            ignore_errors=True,
        )


def lambda_handler(event, context):
    """
    Supports:

    1. Direct Lambda invocation.
       Legacy direct test jobs remain supported.

    2. Future SQS jobs.
       SQS requires schema_version == 1.
    """

    records = event.get("Records")

    if records and all(
        record.get("eventSource") == "aws:sqs"
        for record in records
    ):
        results = []

        for record in records:
            body = json.loads(
                record["body"]
            )

            results.append(
                _process_job(
                    body,
                    require_schema_v1=True,
                )
            )

        return {
            "status": "completed",
            "results": results,
        }

    return _process_job(
        event,
        require_schema_v1=False,
    )
