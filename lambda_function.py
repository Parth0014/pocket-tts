import json
import os
import shutil
import tempfile
from pathlib import Path

import boto3

from generate_narration import run_pipeline


s3 = boto3.client("s3")


def _download_s3_file(bucket, key, destination):
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, destination)
    return destination


def _upload_s3_file(local_path, bucket, key):
    s3.upload_file(
        local_path,
        bucket,
        key,
        ExtraArgs={"ContentType": "audio/wav"},
    )


def _process_job(job):
    """
    Process one test/worker job.

    Expected event:
    {
      "post": {"bucket": "...", "key": "input/post.html"},
      "voice": {"bucket": "...", "key": "voices/narrator.wav"},
      "quote_voice": {"bucket": "...", "key": "voices/quote.wav"},
      "quote_mode": "preserve",
      "output": {"bucket": "...", "key": "test-results/job-123.wav"}
    }

    quote_voice is optional unless quote_mode == "two_voice".
    """
    work_dir = tempfile.mkdtemp(prefix="pockettts-")
    output_dir = os.path.join(work_dir, "output")

    try:
        post_path = os.path.join(work_dir, "post.html")
        voice_path = os.path.join(work_dir, "narration_voice.wav")
        quote_voice_path = None

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
            quote_voice_path = os.path.join(work_dir, "quote_voice.wav")
            _download_s3_file(
                job["quote_voice"]["bucket"],
                job["quote_voice"]["key"],
                quote_voice_path,
            )

        output_path = run_pipeline(
            post_html_file=post_path,
            narration_reference_audio=voice_path,
            quote_reference_audio=quote_voice_path,
            quote_mode=job.get("quote_mode", "preserve"),
            output_dir=output_dir,
        )

        destination = job["output"]
        _upload_s3_file(
            output_path,
            destination["bucket"],
            destination["key"],
        )

        return {
            "status": "completed",
            "output": {
                "bucket": destination["bucket"],
                "key": destination["key"],
            },
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def lambda_handler(event, context):
    """
    Lambda entry point.

    Supports:
      1. Direct invocation with one job object.
      2. SQS events containing one or more job messages.

    The SQS path is intentionally thin: SQS/Lambda concerns stay here while
    narration behavior stays entirely inside run_pipeline().
    """
    # SQS -> Lambda
    if event.get("Records") and all(
        record.get("eventSource") == "aws:sqs"
        for record in event["Records"]
    ):
        results = []

        for record in event["Records"]:
            body = json.loads(record["body"])
            results.append(_process_job(body))

        return {
            "status": "completed",
            "results": results,
        }

    # Direct Lambda invocation for internal testing.
    return _process_job(event)
