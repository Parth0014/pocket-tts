import json
import sys
from unittest.mock import patch

import lambda_function as lf
from narration_studio.status_contract import (
    validate_status_event_v1,
)

GEN_ID = "gen_" + ("1" * 32)
JOB_ID = "job_" + ("2" * 32)
VOICE_ID = "voice_" + ("3" * 32)
POST_ID = "ghostpost123"
CONTENT_HASH = "a" * 64


JOB = {
    "schema_version": 1,
    "job_id": JOB_ID,
    "generation_id": GEN_ID,
    "post_id": POST_ID,
    "content_hash": CONTENT_HASH,
    "post": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            f"ghost/{POST_ID}/"
            f"{CONTENT_HASH}.html"
        ),
    },
    "voice": {
        "voice_id": VOICE_ID,
        "bucket": "pocket-tts-dev-test",
        "key": (
            f"voices/{VOICE_ID}/"
            "reference.wav"
        ),
    },
    "quote_mode": "preserve",
    "output": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            f"generations/{GEN_ID}/"
            "output.wav"
        ),
    },
}


def validated():
    return lf._validate_job(
        JOB,
        require_schema_v1=True,
    )


def test_worker_status_builder_matches_shared_contract():
    job = validated()
    fingerprint = lf._job_fingerprint(
        job
    )

    event = lf._build_status_event(
        job=job,
        job_fingerprint=fingerprint,
        status="COMPLETED",
        attempt=2,
        occurred_at=(
            "2026-09-03T12:30:00Z"
        ),
        output_sha256="b" * 64,
    )

    assert (
        validate_status_event_v1(
            event
        )
        == event
    )


def test_status_publisher_uses_generation_fifo_group():
    job = validated()
    fingerprint = lf._job_fingerprint(
        job
    )

    with patch.object(
        lf,
        "STATUS_QUEUE_URL",
        "https://example.invalid/status.fifo",
    ), patch.object(
        lf.status_sqs,
        "send_message",
        return_value={
            "MessageId": "status-1"
        },
    ) as send:
        lf._publish_generation_status(
            job=job,
            job_fingerprint=fingerprint,
            status="RUNNING",
            attempt=1,
        )

    call = send.call_args.kwargs

    assert call[
        "QueueUrl"
    ] == (
        "https://example.invalid/"
        "status.fifo"
    )

    assert call[
        "MessageGroupId"
    ] == GEN_ID

    body = json.loads(
        call[
            "MessageBody"
        ]
    )

    assert body[
        "status"
    ] == "RUNNING"

    assert (
        len(
            call[
                "MessageDeduplicationId"
            ]
        )
        == 64
    )


def test_existing_output_reconciles_completed_without_tts():
    job = validated()
    fingerprint = lf._job_fingerprint(
        job
    )

    output_sha = "c" * 64

    sys.modules.pop(
        "generate_narration",
        None,
    )

    with patch.object(
        lf,
        "STATUS_QUEUE_URL",
        "https://example.invalid/status.fifo",
    ), patch.object(
        lf.s3,
        "head_object",
        return_value={
            "Metadata": {
                "pocket-schema-version": "1",
                "pocket-job-fingerprint": (
                    fingerprint
                ),
                "pocket-output-sha256": (
                    output_sha
                ),
            }
        },
    ), patch.object(
        lf.status_sqs,
        "send_message",
        return_value={
            "MessageId": "status-1"
        },
    ) as send:
        result = lf._process_job(
            job,
            require_schema_v1=True,
            status_feedback=True,
            receive_count=2,
            max_receive_count=3,
        )

    assert result[
        "status"
    ] == "completed"

    assert (
        "generate_narration"
        not in sys.modules
    )

    body = json.loads(
        send.call_args.kwargs[
            "MessageBody"
        ]
    )

    assert body[
        "status"
    ] == "COMPLETED"

    assert body[
        "output"
    ][
        "sha256"
    ] == output_sha


def test_sqs_handler_passes_receive_count_to_worker(
    monkeypatch,
):
    captured = {}

    def fake_process(
        job,
        **kwargs,
    ):
        captured.update(
            kwargs
        )
        return {
            "status": "completed"
        }

    monkeypatch.setenv(
        "STUDIO_MAX_RECEIVE_COUNT",
        "3",
    )

    monkeypatch.setattr(
        lf,
        "_process_job",
        fake_process,
    )

    result = lf.lambda_handler(
        {
            "Records": [
                {
                    "eventSource": "aws:sqs",
                    "body": json.dumps(
                        JOB
                    ),
                    "attributes": {
                        "ApproximateReceiveCount": (
                            "2"
                        )
                    },
                }
            ]
        },
        None,
    )

    assert result[
        "status"
    ] == "completed"

    assert captured[
        "status_feedback"
    ] is True

    assert captured[
        "receive_count"
    ] == 2

    assert captured[
        "max_receive_count"
    ] == 3
