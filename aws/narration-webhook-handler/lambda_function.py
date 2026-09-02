import json
import hashlib
import hmac
import os
from datetime import datetime, timezone

import boto3


sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")

QUEUE_URL = os.environ["QUEUE_URL"]
POSTS_TABLE = os.environ["POSTS_TABLE"]
WEBHOOK_SECRET_TOKEN = os.environ["WEBHOOK_SECRET_TOKEN"]

posts_table = dynamodb.Table(POSTS_TABLE)


def lambda_handler(event, context):
    """
    Entry point for the Ghost webhook.

    Security relies on a long random token embedded
    in the webhook URL path.
    """

    request_id = getattr(context, "aws_request_id", None)

    # --------------------------------------------------
    # Authenticate webhook
    # --------------------------------------------------

    path_params = event.get("pathParameters") or {}
    supplied_token = path_params.get("token")

    if (
        not supplied_token
        or not hmac.compare_digest(
            supplied_token,
            WEBHOOK_SECRET_TOKEN,
        )
    ):
        _log(
            "webhook_rejected",
            request_id=request_id,
            reason="FORBIDDEN",
        )

        return _response(403, {"error": "forbidden"})

    # --------------------------------------------------
    # Parse webhook
    # --------------------------------------------------

    try:
        body = json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError):
        _log(
            "webhook_rejected",
            request_id=request_id,
            reason="INVALID_JSON",
        )

        return _response(400, {"error": "invalid JSON body"})

    post = body.get("post", {}).get("current", {})

    post_id = post.get("id")
    status = post.get("status")

    if not post_id:
        _log(
            "webhook_rejected",
            request_id=request_id,
            reason="MISSING_POST_ID",
        )

        return _response(400, {"error": "missing post id"})

    if status != "published":
        _log(
            "webhook_skipped",
            request_id=request_id,
            post_id=post_id,
            reason="NOT_PUBLISHED",
            ghost_status=status,
        )

        return _response(
            200,
            {"message": "skipped: not published"},
        )

    if "html" not in post or post.get("html") is None:
        _log(
            "webhook_rejected",
            request_id=request_id,
            post_id=post_id,
            reason="MISSING_HTML",
        )

        return _response(
            400,
            {"error": "missing post html"},
        )

    html_content = post["html"]

    content_hash = hashlib.sha256(
        html_content.encode("utf-8")
    ).hexdigest()

    # --------------------------------------------------
    # Existing state / dedup
    # --------------------------------------------------

    existing = posts_table.get_item(
        Key={"post_id": post_id}
    ).get("Item")

    is_new_post = existing is None

    content_changed = (
        is_new_post
        or existing.get("content_hash") != content_hash
    )

    now = _utc_now()

    # --------------------------------------------------
    # Update Ghost metadata WITHOUT replacing the item.
    # --------------------------------------------------

    fields = {
        "schema_version": 1,
        "source": "GHOST",
        "content_hash": content_hash,
        "ghost_status": "PUBLISHED",
        "last_webhook_at": now,

        "title": post.get("title"),
        "slug": post.get("slug"),
        "url": post.get("url"),

        "excerpt": (
            post.get("custom_excerpt")
            or post.get("excerpt")
        ),

        "feature_image": post.get("feature_image"),

        "published_at": post.get("published_at"),
        "updated_at": post.get("updated_at"),
    }

    if not content_changed:
        _update_post(
            post_id=post_id,
            fields=fields,
            first_seen_at=now,
        )

        _log(
            "post_unchanged",
            request_id=request_id,
            post_id=post_id,
            content_hash=content_hash,
        )

        return _response(
            200,
            {
                "message": "updated metadata: unchanged content",
                "post_id": post_id,
            },
        )

    reason = "NEW_POST" if is_new_post else "CONTENT_CHANGED"

    send_result = sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(
            {
                "schema_version": 1,
                "post_id": post_id,
                "content_hash": content_hash,
                "reason": reason,
            }
        ),
    )

    _log(
        "job_enqueued",
        request_id=request_id,
        post_id=post_id,
        content_hash=content_hash,
        reason=reason,
        sqs_message_id=send_result.get("MessageId"),
    )

    # Queue accepted the narration-needed event.
    # Now persist the new Ghost state.

    _update_post(
        post_id=post_id,
        fields=fields,
        first_seen_at=now,
    )

    _log(
        "post_updated",
        request_id=request_id,
        post_id=post_id,
        content_hash=content_hash,
        reason=reason,
    )

    return _response(
        200,
        {
            "message": "queued",
            "post_id": post_id,
            "reason": reason,
        },
    )


def _update_post(post_id, fields, first_seen_at):
    """
    Update supplied fields while preserving unrelated
    attributes already stored on the DynamoDB item.
    """

    expression_names = {
        "#first_seen": "first_seen_at",
    }

    expression_values = {
        ":first_seen": first_seen_at,
    }

    update_parts = [
        "#first_seen = "
        "if_not_exists(#first_seen, :first_seen)"
    ]

    index = 0

    for field_name, value in fields.items():
        if value is None:
            continue

        name_token = f"#f{index}"
        value_token = f":v{index}"

        expression_names[name_token] = field_name
        expression_values[value_token] = value

        update_parts.append(
            f"{name_token} = {value_token}"
        )

        index += 1

    posts_table.update_item(
        Key={"post_id": post_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def _log(event_name, **fields):
    record = {
        "event": event_name,
        **{
            key: value
            for key, value in fields.items()
            if value is not None
        },
    }

    print(
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "body": json.dumps(body_dict),
    }