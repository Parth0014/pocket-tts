import importlib.util
import json
from pathlib import Path

import boto3

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-dev-status-updater"
    / "lambda_function.py"
)

GEN_ID = "gen_" + ("1" * 32)
ROOM_ID = "room_" + ("2" * 32)
JOB_ID = "job_" + ("3" * 32)
FP = "a" * 64
NOW = "2026-09-03T12:30:00Z"


class FakeDDB:
    def __init__(self):
        self.updates = []
        self.gets = []

    def get_item(
        self,
        **kwargs,
    ):
        self.gets.append(
            kwargs
        )

        return {
            "Item": {
                "generation_id": {
                    "S": GEN_ID
                },
                "room_id": {
                    "S": ROOM_ID
                },
            }
        }

    def update_item(
        self,
        **kwargs,
    ):
        self.updates.append(
            kwargs
        )

        return {}


def load_module(
    monkeypatch,
):
    monkeypatch.setenv(
        "APP_TABLE",
        "pocket-tts-app",
    )

    fake = FakeDDB()

    monkeypatch.setattr(
        boto3,
        "client",
        lambda service_name, *args, **kwargs: fake,
    )

    spec = importlib.util.spec_from_file_location(
        "status_updater_test_module",
        MODULE_PATH,
    )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return module, fake


def event_body(
    status,
):
    value = {
        "schema_version": 1,
        "generation_id": GEN_ID,
        "job_id": JOB_ID,
        "job_fingerprint": FP,
        "status": status,
        "attempt": 1,
        "occurred_at": NOW,
    }

    if status == "COMPLETED":
        value["output"] = {
            "bucket": "pocket-tts-dev-test",
            "key": (
                f"generations/{GEN_ID}/"
                "output.wav"
            ),
            "sha256": "b" * 64,
        }

    if status == "FAILED":
        value["error_code"] = (
            "WORKER_FINAL_ATTEMPT_FAILED"
        )

    return value


def sqs_event(
    body,
):
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps(
                    body
                ),
            }
        ]
    }


def test_running_resolves_route_and_updates_generation(
    monkeypatch,
):
    module, fake = load_module(
        monkeypatch
    )

    result = module.lambda_handler(
        sqs_event(
            event_body(
                "RUNNING"
            )
        ),
        None,
    )

    assert result["status"] == "ok"

    assert fake.gets[0][
        "Key"
    ] == {
        "pk": {
            "S": f"GEN#{GEN_ID}"
        },
        "sk": {
            "S": "ROUTE"
        },
    }

    update = fake.updates[0]

    assert update[
        "Key"
    ] == {
        "pk": {
            "S": f"ROOM#{ROOM_ID}"
        },
        "sk": {
            "S": f"GEN#{GEN_ID}"
        },
    }

    assert (
        update[
            "ExpressionAttributeValues"
        ][
            ":running"
        ][
            "S"
        ]
        == "RUNNING"
    )

    assert (
        "review_status"
        not in update[
            "ExpressionAttributeNames"
        ].values()
    )


def test_completed_persists_output_sha(
    monkeypatch,
):
    module, fake = load_module(
        monkeypatch
    )

    module.lambda_handler(
        sqs_event(
            event_body(
                "COMPLETED"
            )
        ),
        None,
    )

    update = fake.updates[0]

    assert (
        update[
            "ExpressionAttributeValues"
        ][
            ":completed"
        ][
            "S"
        ]
        == "COMPLETED"
    )

    assert (
        update[
            "ExpressionAttributeValues"
        ][
            ":output_sha256"
        ][
            "S"
        ]
        == "b" * 64
    )


def test_failed_persists_bounded_error_code(
    monkeypatch,
):
    module, fake = load_module(
        monkeypatch
    )

    module.lambda_handler(
        sqs_event(
            event_body(
                "FAILED"
            )
        ),
        None,
    )

    update = fake.updates[0]

    assert (
        update[
            "ExpressionAttributeValues"
        ][
            ":error_code"
        ][
            "S"
        ]
        == "WORKER_FINAL_ATTEMPT_FAILED"
    )
