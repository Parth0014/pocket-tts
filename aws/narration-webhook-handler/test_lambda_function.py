import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock


HANDLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "lambda_function.py",
)


class FakeContext:
    aws_request_id = "request-test-123"


def load_handler():
    """
    Load lambda_function.py with fake boto3 objects so no AWS
    client, credential lookup, or network request can occur.
    """

    fake_sqs = MagicMock(name="fake_sqs")
    fake_table = MagicMock(name="fake_table")

    fake_dynamodb_resource = MagicMock(
        name="fake_dynamodb_resource"
    )
    fake_dynamodb_resource.Table.return_value = fake_table

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = MagicMock(return_value=fake_sqs)
    fake_boto3.resource = MagicMock(
        return_value=fake_dynamodb_resource
    )

    old_boto3 = sys.modules.get("boto3")
    sys.modules["boto3"] = fake_boto3

    old_env = {
        "QUEUE_URL": os.environ.get("QUEUE_URL"),
        "POSTS_TABLE": os.environ.get("POSTS_TABLE"),
        "WEBHOOK_SECRET_TOKEN": os.environ.get(
            "WEBHOOK_SECRET_TOKEN"
        ),
    }

    os.environ["QUEUE_URL"] = (
        "https://example.invalid/test-queue"
    )
    os.environ["POSTS_TABLE"] = "TestNarrationPosts"
    os.environ["WEBHOOK_SECRET_TOKEN"] = (
        "unit-test-secret-token"
    )

    module_name = "narration_webhook_handler_under_test"

    try:
        sys.modules.pop(module_name, None)

        spec = importlib.util.spec_from_file_location(
            module_name,
            HANDLER_PATH,
        )

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    finally:
        if old_boto3 is None:
            sys.modules.pop("boto3", None)
        else:
            sys.modules["boto3"] = old_boto3

        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return module, fake_sqs, fake_table


def make_event(
    *,
    token="unit-test-secret-token",
    post_id="post-123",
    status="published",
    html="<p>Hello</p>",
):
    post = {
        "id": post_id,
        "status": status,
        "title": "Example",
        "slug": "example",
        "url": "https://example.com/example",
        "custom_excerpt": "Excerpt",
        "feature_image": None,
        "published_at": "2026-09-01T00:00:00.000Z",
        "updated_at": "2026-09-02T00:00:00.000Z",
    }

    if html is not Ellipsis:
        post["html"] = html

    return {
        "pathParameters": {
            "token": token,
        },
        "body": json.dumps(
            {
                "post": {
                    "current": post,
                }
            }
        ),
    }


def invoke(module, event):
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        response = module.lambda_handler(
            event,
            FakeContext(),
        )

    logs = [
        json.loads(line)
        for line in output.getvalue().splitlines()
        if line.strip()
    ]

    return response, logs


class NarrationWebhookTests(unittest.TestCase):
    def test_bad_token_returns_403_without_storage_or_queue(self):
        module, sqs, table = load_handler()

        response, logs = invoke(
            module,
            make_event(token="wrong-token"),
        )

        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(
            json.loads(response["body"]),
            {"error": "forbidden"},
        )

        table.get_item.assert_not_called()
        table.update_item.assert_not_called()
        sqs.send_message.assert_not_called()

        self.assertEqual(logs[0]["event"], "webhook_rejected")
        self.assertEqual(logs[0]["reason"], "FORBIDDEN")
        self.assertEqual(
            logs[0]["request_id"],
            "request-test-123",
        )

        rendered = json.dumps(logs)
        self.assertNotIn("wrong-token", rendered)
        self.assertNotIn(
            "unit-test-secret-token",
            rendered,
        )

    def test_invalid_json_returns_400(self):
        module, sqs, table = load_handler()

        event = {
            "pathParameters": {
                "token": "unit-test-secret-token",
            },
            "body": "{not-json",
        }

        response, logs = invoke(module, event)

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(logs[0]["event"], "webhook_rejected")
        self.assertEqual(logs[0]["reason"], "INVALID_JSON")

        table.get_item.assert_not_called()
        table.update_item.assert_not_called()
        sqs.send_message.assert_not_called()

    def test_missing_post_id_returns_400(self):
        module, sqs, table = load_handler()

        response, logs = invoke(
            module,
            make_event(post_id=None),
        )

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(
            logs[0]["reason"],
            "MISSING_POST_ID",
        )

        table.get_item.assert_not_called()
        table.update_item.assert_not_called()
        sqs.send_message.assert_not_called()

    def test_unpublished_post_is_skipped(self):
        module, sqs, table = load_handler()

        response, logs = invoke(
            module,
            make_event(status="draft"),
        )

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(
            json.loads(response["body"]),
            {"message": "skipped: not published"},
        )

        self.assertEqual(logs[0]["event"], "webhook_skipped")
        self.assertEqual(logs[0]["reason"], "NOT_PUBLISHED")
        self.assertEqual(logs[0]["post_id"], "post-123")

        table.get_item.assert_not_called()
        table.update_item.assert_not_called()
        sqs.send_message.assert_not_called()

    def test_missing_html_returns_400(self):
        module, sqs, table = load_handler()

        response, logs = invoke(
            module,
            make_event(html=Ellipsis),
        )

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(logs[0]["event"], "webhook_rejected")
        self.assertEqual(logs[0]["reason"], "MISSING_HTML")

        table.get_item.assert_not_called()
        table.update_item.assert_not_called()
        sqs.send_message.assert_not_called()

    def test_unchanged_content_updates_metadata_without_queue(self):
        module, sqs, table = load_handler()

        html = "<p>Hello</p>"
        content_hash = hashlib.sha256(
            html.encode("utf-8")
        ).hexdigest()

        table.get_item.return_value = {
            "Item": {
                "post_id": "post-123",
                "content_hash": content_hash,
            }
        }

        response, logs = invoke(
            module,
            make_event(html=html),
        )

        self.assertEqual(response["statusCode"], 200)

        self.assertEqual(
            json.loads(response["body"]),
            {
                "message": (
                    "updated metadata: unchanged content"
                ),
                "post_id": "post-123",
            },
        )

        table.get_item.assert_called_once_with(
            Key={"post_id": "post-123"}
        )

        table.update_item.assert_called_once()
        sqs.send_message.assert_not_called()

        self.assertEqual(
            [item["event"] for item in logs],
            ["post_unchanged"],
        )

        self.assertEqual(
            logs[0]["content_hash"],
            content_hash,
        )

    def test_new_post_enqueues_before_dynamodb_update(self):
        module, sqs, table = load_handler()

        call_order = []

        table.get_item.return_value = {}

        sqs.send_message.side_effect = lambda **kwargs: (
            call_order.append("sqs")
            or {"MessageId": "message-123"}
        )

        table.update_item.side_effect = lambda **kwargs: (
            call_order.append("dynamodb")
        )

        response, logs = invoke(
            module,
            make_event(),
        )

        self.assertEqual(response["statusCode"], 200)

        body = json.loads(response["body"])

        self.assertEqual(body["message"], "queued")
        self.assertEqual(body["post_id"], "post-123")
        self.assertEqual(body["reason"], "NEW_POST")

        self.assertEqual(
            call_order,
            ["sqs", "dynamodb"],
        )

        sqs.send_message.assert_called_once()

        queued = json.loads(
            sqs.send_message.call_args.kwargs[
                "MessageBody"
            ]
        )

        expected_hash = hashlib.sha256(
            b"<p>Hello</p>"
        ).hexdigest()

        self.assertEqual(
            queued,
            {
                "schema_version": 1,
                "post_id": "post-123",
                "content_hash": expected_hash,
                "reason": "NEW_POST",
            },
        )

        self.assertEqual(
            [item["event"] for item in logs],
            ["job_enqueued", "post_updated"],
        )

        self.assertEqual(
            logs[0]["sqs_message_id"],
            "message-123",
        )

    def test_changed_post_uses_content_changed_reason(self):
        module, sqs, table = load_handler()

        table.get_item.return_value = {
            "Item": {
                "post_id": "post-123",
                "content_hash": "old-hash",
            }
        }

        sqs.send_message.return_value = {
            "MessageId": "message-456"
        }

        response, logs = invoke(
            module,
            make_event(html="<p>Changed</p>"),
        )

        self.assertEqual(response["statusCode"], 200)

        body = json.loads(response["body"])
        self.assertEqual(
            body["reason"],
            "CONTENT_CHANGED",
        )

        queued = json.loads(
            sqs.send_message.call_args.kwargs[
                "MessageBody"
            ]
        )

        expected_hash = hashlib.sha256(
            b"<p>Changed</p>"
        ).hexdigest()

        self.assertEqual(
            queued,
            {
                "schema_version": 1,
                "post_id": "post-123",
                "content_hash": expected_hash,
                "reason": "CONTENT_CHANGED",
            },
        )

        table.update_item.assert_called_once()

        self.assertEqual(
            [item["event"] for item in logs],
            ["job_enqueued", "post_updated"],
        )

    def test_sqs_failure_prevents_dynamodb_update(self):
        module, sqs, table = load_handler()

        table.get_item.return_value = {}

        sqs.send_message.side_effect = RuntimeError(
            "synthetic sqs failure"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic sqs failure",
        ):
            invoke(
                module,
                make_event(),
            )

        sqs.send_message.assert_called_once()
        table.update_item.assert_not_called()

    def test_dynamodb_failure_occurs_after_successful_enqueue(self):
        module, sqs, table = load_handler()

        call_order = []

        table.get_item.return_value = {}

        sqs.send_message.side_effect = lambda **kwargs: (
            call_order.append("sqs")
            or {"MessageId": "message-789"}
        )

        table.update_item.side_effect = RuntimeError(
            "synthetic dynamodb failure"
        )

        output = io.StringIO()

        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic dynamodb failure",
        ):
            with contextlib.redirect_stdout(output):
                module.lambda_handler(
                    make_event(),
                    FakeContext(),
                )

        call_order.append("dynamodb_failed")

        self.assertEqual(
            call_order,
            ["sqs", "dynamodb_failed"],
        )

        logs = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip()
        ]

        self.assertEqual(
            [item["event"] for item in logs],
            ["job_enqueued"],
        )

        self.assertNotIn(
            "post_updated",
            [
                item["event"]
                for item in logs
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)