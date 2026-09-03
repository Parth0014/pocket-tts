import importlib.util
import json
from pathlib import Path

import boto3

from narration_studio.auth import sign_session

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-app-api"
    / "lambda_function.py"
)

ROOM_ID = "room_" + ("1" * 32)
GEN_ID = "gen_" + ("2" * 32)
VOICE_ID = "voice_" + ("3" * 32)


class FakeSSM:
    def get_parameter(
        self,
        *,
        Name,
        WithDecryption,
    ):
        values = {
            "/dashboard": "correct-code",
            "/session": "s" * 64,
        }

        return {
            "Parameter": {
                "Value": values[Name]
            }
        }


class FakeSQS:
    def __init__(self):
        self.calls = []

    def send_message(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return {
            "MessageId": "message-1"
        }


class FakeDDB:
    def __init__(self):
        self.updates = []

    def query(
        self,
        **kwargs,
    ):
        return {
            "Items": []
        }

    def scan(
        self,
        **kwargs,
    ):
        return {
            "Items": []
        }

    def get_item(
        self,
        *,
        TableName,
        Key,
        ConsistentRead,
    ):
        assert ConsistentRead is True

        if TableName == "pocket-tts-app":
            if Key["sk"]["S"] == "META":
                return {
                    "Item": {
                        "owner_id": {
                            "S": "internal-dashboard"
                        },
                        "status": {
                            "S": "ACTIVE"
                        },
                    }
                }

            return {
                "Item": {
                    "generation_id": {
                        "S": GEN_ID
                    },
                    "source_post_id": {
                        "S": "ghostpost123"
                    },
                    "source_content_hash": {
                        "S": "a" * 64
                    },
                    "voice_id": {
                        "S": VOICE_ID
                    },
                    "quote_mode": {
                        "S": "preserve"
                    },
                    "review_status": {
                        "S": "UNREVIEWED"
                    },
                }
            }

        if TableName == "NarrationVoices":
            return {
                "Item": {
                    "status": {
                        "S": "ACTIVE"
                    }
                }
            }

        raise AssertionError(
            TableName
        )

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
    monkeypatch.setenv(
        "VOICE_TABLE",
        "NarrationVoices",
    )
    monkeypatch.setenv(
        "OWNER_ID",
        "internal-dashboard",
    )
    monkeypatch.setenv(
        "DASHBOARD_SECRET_CODE_PARAMETER",
        "/dashboard",
    )
    monkeypatch.setenv(
        "SESSION_SIGNING_SECRET_PARAMETER",
        "/session",
    )
    monkeypatch.setenv(
        "STUDIO_QUEUE_URL",
        "https://example.invalid/studio.fifo",
    )

    fake_ssm = FakeSSM()
    fake_ddb = FakeDDB()
    fake_sqs = FakeSQS()

    def fake_client(
        service_name,
        *args,
        **kwargs,
    ):
        return {
            "ssm": fake_ssm,
            "dynamodb": fake_ddb,
            "sqs": fake_sqs,
        }[
            service_name
        ]

    monkeypatch.setattr(
        boto3,
        "client",
        fake_client,
    )

    spec = importlib.util.spec_from_file_location(
        "app_api_enqueue_test_module",
        MODULE_PATH,
    )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return (
        module,
        fake_ddb,
        fake_sqs,
    )


def test_authenticated_enqueue_pins_sends_and_marks_queued(
    monkeypatch,
):
    module, fake_ddb, fake_sqs = load_module(
        monkeypatch
    )

    token = sign_session(
        subject="internal-dashboard",
        signing_secret="s" * 64,
        now=1000,
        ttl_seconds=3600,
    )

    monkeypatch.setattr(
        module,
        "verify_session",
        lambda token, signing_secret: {
            "v": 1,
            "sub": "internal-dashboard",
            "iat": 1000,
            "exp": 4600,
        },
    )

    response = module.lambda_handler(
        {
            "rawPath": (
                f"/rooms/{ROOM_ID}/"
                f"generations/{GEN_ID}/enqueue"
            ),
            "requestContext": {
                "http": {
                    "method": "POST"
                }
            },
            "cookies": [
                f"pocket_tts_session={token}"
            ],
        },
        None,
    )

    body = json.loads(
        response["body"]
    )

    assert response["statusCode"] == 202
    assert body["generation_status"] == "QUEUED"
    assert body["already_queued"] is False

    assert len(
        fake_sqs.calls
    ) == 1

    call = fake_sqs.calls[0]

    assert call[
        "MessageGroupId"
    ] == "tts"

    assert call[
        "MessageDeduplicationId"
    ] == GEN_ID

    assert any(
        "generation_status"
        in update[
            "ExpressionAttributeNames"
        ].values()
        for update in fake_ddb.updates
    )
