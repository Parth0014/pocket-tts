"""Production-ingestion -> Narration Studio Bridge V1.

This Lambda consumes the existing production narration-needed event contract,
re-fetches the authoritative published post from Ghost Content API, verifies
the exact HTML hash, archives/verifies canonical DEV artifacts, and records
idempotent manager-intake state.

It does NOT create a generation, send a Studio TTS job, or publish audio.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from narration_content.document import build_document
from narration_content.hashing import compute_content_hash
from narration_content.normalizer import normalize_ghost_html
from narration_content.validation import validate_document
from narration_studio.bridge import (
    narration_document_key,
    prepare_bridge_intake,
)
from narration_studio.production_event import (
    ProductionEventError,
    validate_production_event_v1,
)

GHOST_BASE_URL = os.environ["GHOST_BASE_URL"].rstrip("/")
GHOST_KEY_PARAMETER = os.environ[
    "GHOST_CONTENT_API_KEY_PARAMETER"
]
APP_TABLE = os.environ["APP_TABLE"]
DEV_BUCKET = os.environ["DEV_BUCKET"]

_s3 = boto3.client("s3")
_ddb = boto3.client("dynamodb")
_ssm = boto3.client("ssm")

_secret_cache: dict[str, str] = {}


class BridgeRuntimeError(RuntimeError):
    """Safe operational bridge failure."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _secret(name: str) -> str:
    cached = _secret_cache.get(name)

    if cached is not None:
        return cached

    value = _ssm.get_parameter(
        Name=name,
        WithDecryption=True,
    )["Parameter"]["Value"]

    _secret_cache[name] = value
    return value


def _log(
    event: str,
    **fields: Any,
) -> None:
    payload = {
        "event": event,
        **fields,
    }

    print(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _ghost_post(
    post_id: str,
) -> dict[str, Any]:
    # The Content API key is deliberately kept out of every log/error string.
    query = urllib.parse.urlencode(
        {
            "key": _secret(
                GHOST_KEY_PARAMETER
            ),
            "formats": "html",
        }
    )
    quoted_id = urllib.parse.quote(
        post_id,
        safe="",
    )
    url = (
        f"{GHOST_BASE_URL}/ghost/api/content/"
        f"posts/{quoted_id}/?{query}"
    )

    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": (
                "pocket-tts-studio-bridge/1"
            ),
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            raw = response.read(
                5_000_001
            )
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
    ):
        raise BridgeRuntimeError(
            "Ghost Content API fetch failed"
        ) from None

    if len(raw) > 5_000_000:
        raise BridgeRuntimeError(
            "Ghost Content API response is too large"
        )

    try:
        payload = json.loads(
            raw.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise BridgeRuntimeError(
            "Ghost Content API response is invalid"
        ) from None

    posts = payload.get("posts")

    if (
        not isinstance(posts, list)
        or len(posts) != 1
        or not isinstance(posts[0], dict)
    ):
        raise BridgeRuntimeError(
            "Ghost Content API returned unexpected post shape"
        )

    post = posts[0]

    if post.get("id") != post_id:
        raise BridgeRuntimeError(
            "Ghost post identity mismatch"
        )

    html = post.get("html")

    if not isinstance(html, str):
        raise BridgeRuntimeError(
            "Ghost post HTML is missing"
        )

    return post


def _canonical_json_bytes(
    value: dict[str, Any],
) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _error_code(
    exc: Exception,
) -> str:
    response = getattr(
        exc,
        "response",
        {},
    )
    if not isinstance(response, dict):
        return ""

    error = response.get(
        "Error",
        {},
    )
    if not isinstance(error, dict):
        return ""

    return str(
        error.get(
            "Code",
            "",
        )
    )


def _put_raw_immutable(
    *,
    key: str,
    body: bytes,
    post_id: str,
    content_hash: str,
) -> None:
    try:
        _s3.put_object(
            Bucket=DEV_BUCKET,
            Key=key,
            Body=body,
            ContentType=(
                "text/html; charset=utf-8"
            ),
            Metadata={
                "artifact-kind": "ghost-html",
                "post-id": post_id,
                "content-hash": content_hash,
            },
            IfNoneMatch="*",
        )
        return
    except ClientError as exc:
        if _error_code(exc) not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        }:
            raise BridgeRuntimeError(
                "DEV raw artifact write failed"
            ) from None

    try:
        existing = _s3.get_object(
            Bucket=DEV_BUCKET,
            Key=key,
        )["Body"].read()
    except ClientError:
        raise BridgeRuntimeError(
            "DEV raw artifact verification failed"
        ) from None

    if existing != body:
        raise BridgeRuntimeError(
            "DEV raw artifact conflict"
        )


def _put_document_immutable(
    *,
    key: str,
    body: bytes,
    document: dict[str, Any],
) -> None:
    try:
        _s3.put_object(
            Bucket=DEV_BUCKET,
            Key=key,
            Body=body,
            ContentType=(
                "application/json; charset=utf-8"
            ),
            Metadata={
                "artifact-kind": (
                    "narration-document-v1"
                ),
                "post-id": document[
                    "post_id"
                ],
                "content-hash": document[
                    "content_hash"
                ],
                "narration-hash": document[
                    "narration_hash"
                ],
                "processor-version": str(
                    document[
                        "processor_version"
                    ]
                ),
            },
            IfNoneMatch="*",
        )
        return
    except ClientError as exc:
        if _error_code(exc) not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        }:
            raise BridgeRuntimeError(
                "DEV Narration Document write failed"
            ) from None

    try:
        existing_bytes = _s3.get_object(
            Bucket=DEV_BUCKET,
            Key=key,
        )["Body"].read()
        existing = json.loads(
            existing_bytes.decode(
                "utf-8"
            )
        )
        validate_document(
            existing,
            verify_hash=True,
        )
    except (
        ClientError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ):
        raise BridgeRuntimeError(
            "DEV Narration Document verification failed"
        ) from None

    if existing != document:
        raise BridgeRuntimeError(
            "DEV Narration Document conflict"
        )


def _ddb_value(
    value: Any,
) -> dict[str, str]:
    if isinstance(value, bool):
        raise BridgeRuntimeError(
            "boolean bridge attributes are unsupported"
        )

    if isinstance(value, int):
        return {
            "N": str(value)
        }

    if isinstance(value, str):
        return {
            "S": value
        }

    raise BridgeRuntimeError(
        "unsupported bridge attribute type"
    )


def _ddb_item(
    value: dict[str, Any],
) -> dict[str, dict[str, str]]:
    return {
        key: _ddb_value(item)
        for key, item in value.items()
    }


def _string(
    item: dict[str, Any],
    name: str,
) -> str | None:
    value = item.get(name)

    if not isinstance(value, dict):
        return None

    raw = value.get("S")

    return (
        raw
        if isinstance(raw, str)
        else None
    )


def _put_bridge_state(
    *,
    intake,
    observed_at: str,
) -> str:
    receipt = _ddb_item(
        dict(
            intake.receipt
        )
    )

    receipt_key = {
        "pk": receipt["pk"],
        "sk": receipt["sk"],
    }

    duplicate = False

    try:
        _ddb.put_item(
            TableName=APP_TABLE,
            Item=receipt,
            ConditionExpression=(
                "attribute_not_exists(pk)"
            ),
        )
    except ClientError as exc:
        if _error_code(exc) != (
            "ConditionalCheckFailedException"
        ):
            raise BridgeRuntimeError(
                "bridge receipt write failed"
            ) from None

        duplicate = True

        existing = _ddb.get_item(
            TableName=APP_TABLE,
            Key=receipt_key,
            ConsistentRead=True,
        ).get("Item")

        if not isinstance(
            existing,
            dict,
        ):
            raise BridgeRuntimeError(
                "bridge receipt disappeared"
            )

        for field in (
            "post_id",
            "content_hash",
            "narration_hash",
            "raw_bucket",
            "raw_key",
            "document_bucket",
            "document_key",
        ):
            if _string(
                existing,
                field,
            ) != intake.receipt[
                field
            ]:
                raise BridgeRuntimeError(
                    "bridge receipt conflict"
                )

        _ddb.update_item(
            TableName=APP_TABLE,
            Key=receipt_key,
            UpdateExpression=(
                "SET last_seen_at = :seen, "
                "updated_at = :seen, "
                "last_event_reason = :reason"
            ),
            ExpressionAttributeValues={
                ":seen": {
                    "S": observed_at
                },
                ":reason": {
                    "S": intake.receipt[
                        "last_event_reason"
                    ]
                },
            },
        )

    current = _ddb_item(
        dict(
            intake.current
        )
    )

    _ddb.put_item(
        TableName=APP_TABLE,
        Item=current,
    )

    return (
        "DUPLICATE"
        if duplicate
        else "INGESTED"
    )


def _process(
    raw_event: dict[str, Any],
) -> dict[str, Any]:
    event = validate_production_event_v1(
        raw_event
    )

    post = _ghost_post(
        event.post_id
    )

    html = post["html"]
    current_hash = compute_content_hash(
        html
    )

    if current_hash != event.content_hash:
        _log(
            "bridge_stale_event",
            post_id=event.post_id,
            event_content_hash=event.content_hash,
            current_content_hash=current_hash,
        )
        return {
            "status": "STALE",
            "post_id": event.post_id,
            "event_content_hash": (
                event.content_hash
            ),
            "current_content_hash": (
                current_hash
            ),
        }

    blocks = normalize_ghost_html(
        html
    )

    document = build_document(
        post_id=event.post_id,
        content_hash=current_hash,
        blocks=blocks,
    )

    validate_document(
        document,
        verify_hash=True,
    )

    raw_key = (
        f"ghost/{event.post_id}/"
        f"{current_hash}.html"
    )

    document_key = narration_document_key(
        post_id=event.post_id,
        content_hash=current_hash,
        processor_version=document[
            "processor_version"
        ],
        narration_hash=document[
            "narration_hash"
        ],
    )

    _put_raw_immutable(
        key=raw_key,
        body=html.encode(
            "utf-8"
        ),
        post_id=event.post_id,
        content_hash=current_hash,
    )

    _put_document_immutable(
        key=document_key,
        body=_canonical_json_bytes(
            document
        ),
        document=document,
    )

    observed_at = _utc_now()

    intake = prepare_bridge_intake(
        post_id=event.post_id,
        content_hash=current_hash,
        narration_hash=document[
            "narration_hash"
        ],
        processor_version=document[
            "processor_version"
        ],
        raw_bucket=DEV_BUCKET,
        raw_key=raw_key,
        document_bucket=DEV_BUCKET,
        document_key=document_key,
        reason=event.reason,
        title=(
            post.get("title")
            if isinstance(
                post.get("title"),
                str,
            )
            else None
        ),
        slug=(
            post.get("slug")
            if isinstance(
                post.get("slug"),
                str,
            )
            else None
        ),
        url=(
            post.get("url")
            if isinstance(
                post.get("url"),
                str,
            )
            else None
        ),
        ghost_updated_at=(
            post.get("updated_at")
            if isinstance(
                post.get(
                    "updated_at"
                ),
                str,
            )
            else None
        ),
        observed_at=observed_at,
    )

    result = _put_bridge_state(
        intake=intake,
        observed_at=observed_at,
    )

    _log(
        "bridge_ingested",
        post_id=event.post_id,
        content_hash=current_hash,
        narration_hash=document[
            "narration_hash"
        ],
        result=result,
    )

    return {
        "status": result,
        "post_id": event.post_id,
        "content_hash": current_hash,
        "narration_hash": document[
            "narration_hash"
        ],
        "document_key": document_key,
    }


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    records = event.get("Records")

    # Direct smoke path uses the exact V1 object itself.
    if records is None:
        try:
            return _process(event)
        except ProductionEventError:
            raise BridgeRuntimeError(
                "invalid production ingestion event"
            ) from None

    if not isinstance(records, list):
        raise BridgeRuntimeError(
            "SQS Records must be a list"
        )

    results = []

    for record in records:
        if not isinstance(record, dict):
            raise BridgeRuntimeError(
                "SQS record must be an object"
            )

        body = record.get("body")

        if not isinstance(body, str):
            raise BridgeRuntimeError(
                "SQS body must be JSON text"
            )

        try:
            raw_event = json.loads(
                body
            )
            results.append(
                _process(
                    raw_event
                )
            )
        except (
            json.JSONDecodeError,
            ProductionEventError,
        ):
            raise BridgeRuntimeError(
                "invalid production ingestion event"
            ) from None

    return {
        "processed": len(results),
        "results": results,
    }
