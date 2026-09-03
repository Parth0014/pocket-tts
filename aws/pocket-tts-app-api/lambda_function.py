"""Internal Narration Studio App API."""

from __future__ import annotations

import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import boto3

from narration_studio.auth import SessionError, sign_session, verify_session
from narration_studio.dispatch import (
    DynamoGenerationDispatchStore,
    SqsStudioJobPublisher,
    StudioDispatchConflictError,
    StudioDispatchError,
)
from narration_studio.worker_contract import (
    build_worker_job_v1,
    new_job_id,
)

APP_TABLE = os.environ["APP_TABLE"]
VOICE_TABLE = os.environ["VOICE_TABLE"]
OWNER_ID = os.environ["OWNER_ID"]
DASHBOARD_PARAM = os.environ["DASHBOARD_SECRET_CODE_PARAMETER"]
SESSION_PARAM = os.environ["SESSION_SIGNING_SECRET_PARAMETER"]
STUDIO_QUEUE_URL = os.environ["STUDIO_QUEUE_URL"]

COOKIE_NAME = "pocket_tts_session"
COOKIE_MAX_AGE = 12 * 60 * 60

_ENQUEUE_RE = re.compile(
    r"^/rooms/(?P<room_id>room_[0-9a-f]{32})/"
    r"generations/(?P<generation_id>gen_[0-9a-f]{32})/enqueue$"
)

_ssm = boto3.client("ssm")
_ddb = boto3.client("dynamodb")
_sqs = boto3.client("sqs")
_secret_cache: dict[str, str] = {}


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


def _response(
    status_code: int,
    body: dict[str, Any],
    *,
    cookies: list[str] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }

    if cookies:
        response["cookies"] = cookies

    return response


def _method(event: dict[str, Any]) -> str:
    request_context = event.get("requestContext")

    if not isinstance(request_context, dict):
        return ""

    http = request_context.get("http")

    if not isinstance(http, dict):
        return ""

    value = http.get("method")

    return value.upper() if isinstance(value, str) else ""


def _path(event: dict[str, Any]) -> str:
    value = event.get("rawPath")
    return value if isinstance(value, str) else ""


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")

    if raw is None:
        return {}

    if not isinstance(raw, str):
        raise ValueError("invalid request body")

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON") from exc

    if not isinstance(value, dict):
        raise ValueError("request body must be an object")

    return value


def _cookie_map(event: dict[str, Any]) -> dict[str, str]:
    values: list[str] = []

    event_cookies = event.get("cookies")

    if isinstance(event_cookies, list):
        values.extend(
            value
            for value in event_cookies
            if isinstance(value, str)
        )

    headers = event.get("headers")

    if isinstance(headers, dict):
        for key, value in headers.items():
            if (
                isinstance(key, str)
                and key.lower() == "cookie"
                and isinstance(value, str)
            ):
                values.append(value)

    parsed: dict[str, str] = {}

    for header in values:
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")

            if separator and name:
                parsed[name] = value

    return parsed


def _session_subject(event: dict[str, Any]) -> str:
    token = _cookie_map(event).get(COOKIE_NAME)

    if not token:
        raise SessionError("missing session")

    payload = verify_session(
        token,
        signing_secret=_secret(SESSION_PARAM),
    )

    if payload["sub"] != OWNER_ID:
        raise SessionError("unauthorized subject")

    return payload["sub"]


def _session_cookie(token: str) -> str:
    return (
        f"{COOKIE_NAME}={token}; "
        "Path=/; "
        f"Max-Age={COOKIE_MAX_AGE}; "
        "HttpOnly; Secure; SameSite=Lax"
    )


def _clear_cookie() -> str:
    return (
        f"{COOKIE_NAME}=; Path=/; Max-Age=0; "
        "HttpOnly; Secure; SameSite=Lax"
    )


def _string(item: dict[str, Any], name: str) -> str | None:
    value = item.get(name)

    if not isinstance(value, dict):
        return None

    raw = value.get("S")

    return raw if isinstance(raw, str) else None


def _get_item(
    *,
    table_name: str,
    key: dict[str, Any],
) -> dict[str, Any] | None:
    response = _ddb.get_item(
        TableName=table_name,
        Key=key,
        ConsistentRead=True,
    )

    item = response.get("Item")

    return item if isinstance(item, dict) else None


def _rooms(owner_id: str) -> list[dict[str, Any]]:
    response = _ddb.query(
        TableName=APP_TABLE,
        KeyConditionExpression="#pk = :pk",
        ExpressionAttributeNames={
            "#pk": "pk",
        },
        ExpressionAttributeValues={
            ":pk": {
                "S": f"OWNER#{owner_id}",
            }
        },
        ConsistentRead=True,
    )

    rows = [
        {
            "room_id": _string(item, "room_id"),
            "title": _string(item, "title"),
            "status": _string(item, "status"),
            "updated_at": _string(item, "updated_at"),
        }
        for item in response.get("Items", [])
    ]

    return sorted(
        rows,
        key=lambda row: (
            row["updated_at"] or "",
            row["room_id"] or "",
        ),
        reverse=True,
    )


def _voices() -> list[dict[str, Any]]:
    response = _ddb.scan(
        TableName=VOICE_TABLE,
        ProjectionExpression=(
            "voice_id, display_name, #status, #version, "
            "reference_sha256, created_at, updated_at"
        ),
        ExpressionAttributeNames={
            "#status": "status",
            "#version": "version",
        },
    )

    rows = []

    for item in response.get("Items", []):
        raw_version = item.get(
            "version",
            {},
        ).get("N")

        rows.append(
            {
                "voice_id": _string(item, "voice_id"),
                "display_name": _string(item, "display_name"),
                "status": _string(item, "status"),
                "version": (
                    int(raw_version)
                    if isinstance(raw_version, str)
                    else None
                ),
                "reference_sha256": _string(
                    item,
                    "reference_sha256",
                ),
                "created_at": _string(item, "created_at"),
                "updated_at": _string(item, "updated_at"),
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            row["display_name"] or "",
            row["voice_id"] or "",
        ),
    )


def _enqueue_generation(
    *,
    subject: str,
    room_id: str,
    generation_id: str,
) -> dict[str, Any]:
    room = _get_item(
        table_name=APP_TABLE,
        key={
            "pk": {
                "S": f"ROOM#{room_id}",
            },
            "sk": {
                "S": "META",
            },
        },
    )

    if (
        room is None
        or _string(room, "owner_id") != subject
        or _string(room, "status") != "ACTIVE"
    ):
        return _response(
            404,
            {
                "ok": False,
                "error": "active room not found",
            },
        )

    generation = _get_item(
        table_name=APP_TABLE,
        key={
            "pk": {
                "S": f"ROOM#{room_id}",
            },
            "sk": {
                "S": f"GEN#{generation_id}",
            },
        },
    )

    if generation is None:
        return _response(
            404,
            {
                "ok": False,
                "error": "generation not found",
            },
        )

    generation_status = _string(
        generation,
        "generation_status",
    )

    if generation_status == "QUEUED":
        return _response(
            200,
            {
                "ok": True,
                "generation_id": generation_id,
                "generation_status": "QUEUED",
                "already_queued": True,
                "job_id": _string(
                    generation,
                    "job_id",
                ),
            },
        )

    if generation_status is not None:
        return _response(
            409,
            {
                "ok": False,
                "error": (
                    "generation is not enqueueable "
                    f"from status {generation_status}"
                ),
            },
        )

    voice_id = _string(
        generation,
        "voice_id",
    )

    voice = (
        _get_item(
            table_name=VOICE_TABLE,
            key={
                "voice_id": {
                    "S": voice_id,
                }
            },
        )
        if voice_id is not None
        else None
    )

    if (
        voice is None
        or _string(voice, "status") != "ACTIVE"
    ):
        return _response(
            409,
            {
                "ok": False,
                "error": "generation voice is not ACTIVE",
            },
        )

    quote_mode = _string(
        generation,
        "quote_mode",
    )

    quote_voice_id = _string(
        generation,
        "quote_voice_id",
    )

    if quote_mode == "two_voice":
        quote_voice = (
            _get_item(
                table_name=VOICE_TABLE,
                key={
                    "voice_id": {
                        "S": quote_voice_id,
                    }
                },
            )
            if quote_voice_id is not None
            else None
        )

        if (
            quote_voice is None
            or _string(
                quote_voice,
                "status",
            )
            != "ACTIVE"
        ):
            return _response(
                409,
                {
                    "ok": False,
                    "error": (
                        "generation quote voice is not ACTIVE"
                    ),
                },
            )

    elif quote_voice_id is not None:
        return _response(
            409,
            {
                "ok": False,
                "error": "generation quote voice state is invalid",
            },
        )

    source_post_id = _string(
        generation,
        "source_post_id",
    )

    source_content_hash = _string(
        generation,
        "source_content_hash",
    )

    if (
        source_post_id is None
        or source_content_hash is None
        or quote_mode is None
        or voice_id is None
    ):
        return _response(
            409,
            {
                "ok": False,
                "error": "generation intent is incomplete",
            },
        )

    store = DynamoGenerationDispatchStore(
        client=_ddb,
        table_name=APP_TABLE,
    )

    try:
        store.ensure_route(
            room_id=room_id,
            generation_id=generation_id,
            created_at=_utc_now(),
        )
    except StudioDispatchConflictError:
        return _response(
            409,
            {
                "ok": False,
                "error": "generation route conflict",
            },
        )
    except StudioDispatchError:
        return _response(
            503,
            {
                "ok": False,
                "error": "generation route write failed",
            },
        )

    pinned = store.get(
        room_id=room_id,
        generation_id=generation_id,
    )

    if pinned is None:
        job = build_worker_job_v1(
            job_id=new_job_id(),
            generation_id=generation_id,
            post_id=source_post_id,
            content_hash=source_content_hash,
            voice_id=voice_id,
            quote_mode=quote_mode,
            quote_voice_id=quote_voice_id,
        )

        try:
            pinned = store.pin(
                room_id=room_id,
                job=job,
                pinned_at=_utc_now(),
            )
        except StudioDispatchConflictError:
            pinned = store.get(
                room_id=room_id,
                generation_id=generation_id,
            )

            if pinned is None:
                return _response(
                    409,
                    {
                        "ok": False,
                        "error": "generation dispatch conflict",
                    },
                )

    publisher = SqsStudioJobPublisher(
        client=_sqs,
        queue_url=STUDIO_QUEUE_URL,
    )

    try:
        message_id = publisher.publish(
            pinned
        )

        store.mark_queued(
            room_id=room_id,
            pinned=pinned,
            queued_at=_utc_now(),
        )

    except StudioDispatchConflictError:
        return _response(
            409,
            {
                "ok": False,
                "error": "generation state changed during enqueue",
            },
        )

    except StudioDispatchError:
        return _response(
            503,
            {
                "ok": False,
                "error": "generation dispatch failed",
            },
        )

    return _response(
        202,
        {
            "ok": True,
            "generation_id": generation_id,
            "generation_status": "QUEUED",
            "job_id": pinned.job_id,
            "message_id": message_id,
            "already_queued": False,
        },
    )


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    method = _method(event)
    path = _path(event)

    if method == "GET" and path == "/health":
        return _response(
            200,
            {
                "ok": True,
                "service": "pocket-tts-app-api",
                "schema_version": 1,
            },
        )

    if method == "POST" and path == "/auth/login":
        try:
            body = _body(event)
        except ValueError as exc:
            return _response(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                },
            )

        if (
            set(body) != {"code"}
            or not isinstance(body.get("code"), str)
        ):
            return _response(
                400,
                {
                    "ok": False,
                    "error": (
                        "login body must contain only string code"
                    ),
                },
            )

        if not hmac.compare_digest(
            body["code"],
            _secret(DASHBOARD_PARAM),
        ):
            return _response(
                401,
                {
                    "ok": False,
                    "error": "invalid credentials",
                },
            )

        token = sign_session(
            subject=OWNER_ID,
            signing_secret=_secret(SESSION_PARAM),
        )

        return _response(
            200,
            {
                "ok": True,
                "authenticated": True,
            },
            cookies=[
                _session_cookie(token)
            ],
        )

    if method == "POST" and path == "/auth/logout":
        return _response(
            200,
            {
                "ok": True,
                "authenticated": False,
            },
            cookies=[
                _clear_cookie()
            ],
        )

    try:
        subject = _session_subject(event)
    except SessionError:
        return _response(
            401,
            {
                "ok": False,
                "error": "authentication required",
            },
        )

    if method == "GET" and path == "/auth/session":
        return _response(
            200,
            {
                "ok": True,
                "authenticated": True,
                "subject": subject,
            },
        )

    if method == "GET" and path == "/rooms":
        return _response(
            200,
            {
                "ok": True,
                "rooms": _rooms(subject),
            },
        )

    if method == "GET" and path == "/voices":
        return _response(
            200,
            {
                "ok": True,
                "voices": _voices(),
            },
        )

    enqueue_match = (
        _ENQUEUE_RE.fullmatch(
            path
        )
        if method == "POST"
        else None
    )

    if enqueue_match is not None:
        try:
            body = _body(event)
        except ValueError as exc:
            return _response(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                },
            )

        if body:
            return _response(
                400,
                {
                    "ok": False,
                    "error": "enqueue request body must be empty",
                },
            )

        return _enqueue_generation(
            subject=subject,
            room_id=enqueue_match.group(
                "room_id"
            ),
            generation_id=enqueue_match.group(
                "generation_id"
            ),
        )

    return _response(
        404,
        {
            "ok": False,
            "error": "route not found",
        },
    )
