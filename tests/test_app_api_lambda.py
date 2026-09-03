import importlib.util
from pathlib import Path

import boto3

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-app-api"
    / "lambda_function.py"
)


class FakeSSM:
    def get_parameter(
        self,
        *,
        Name,
        WithDecryption,
    ):
        assert WithDecryption is True

        values = {
            "/dashboard": "correct-code",
            "/session": "s" * 64,
        }

        return {
            "Parameter": {
                "Value": values[Name]
            }
        }


class FakeDDB:
    def query(self, **kwargs):
        return {
            "Items": []
        }

    def scan(self, **kwargs):
        return {
            "Items": []
        }

    def get_item(self, **kwargs):
        return {}


class FakeSQS:
    def send_message(
        self,
        **kwargs,
    ):
        return {
            "MessageId": "message-1"
        }


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
        "app_api_test_module",
        MODULE_PATH,
    )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    module._secret_cache.clear()

    return module


def event(
    method,
    path,
    *,
    body=None,
    cookies=None,
):
    value = {
        "rawPath": path,
        "requestContext": {
            "http": {
                "method": method,
            }
        },
    }

    if body is not None:
        value["body"] = body

    if cookies is not None:
        value["cookies"] = cookies

    return value


def test_health_is_public(
    monkeypatch,
):
    module = load_module(
        monkeypatch
    )

    assert module.lambda_handler(
        event(
            "GET",
            "/health",
        ),
        None,
    )["statusCode"] == 200


def test_session_requires_auth(
    monkeypatch,
):
    module = load_module(
        monkeypatch
    )

    assert module.lambda_handler(
        event(
            "GET",
            "/auth/session",
        ),
        None,
    )["statusCode"] == 401


def test_login_cookie_security_and_session(
    monkeypatch,
):
    module = load_module(
        monkeypatch
    )

    response = module.lambda_handler(
        event(
            "POST",
            "/auth/login",
            body='{"code":"correct-code"}',
        ),
        None,
    )

    assert response["statusCode"] == 200

    cookie = response["cookies"][0]

    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie
    assert "Max-Age=43200" in cookie

    token = cookie.split(
        ";",
        1,
    )[0].split(
        "=",
        1,
    )[1]

    response = module.lambda_handler(
        event(
            "GET",
            "/auth/session",
            cookies=[
                f"pocket_tts_session={token}"
            ],
        ),
        None,
    )

    assert response["statusCode"] == 200


def test_wrong_code_fails(
    monkeypatch,
):
    module = load_module(
        monkeypatch
    )

    response = module.lambda_handler(
        event(
            "POST",
            "/auth/login",
            body='{"code":"wrong"}',
        ),
        None,
    )

    assert response["statusCode"] == 401
