"""Internal Narration Studio App API."""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

import boto3

from narration_studio.auth import SessionError, sign_session, verify_session

APP_TABLE = os.environ["APP_TABLE"]
VOICE_TABLE = os.environ["VOICE_TABLE"]
OWNER_ID = os.environ["OWNER_ID"]
DASHBOARD_PARAM = os.environ["DASHBOARD_SECRET_CODE_PARAMETER"]
SESSION_PARAM = os.environ["SESSION_SIGNING_SECRET_PARAMETER"]

COOKIE_NAME = "pocket_tts_session"
COOKIE_MAX_AGE = 12 * 60 * 60

_ssm = boto3.client("ssm")
_ddb = boto3.client("dynamodb")
_secret_cache: dict[str, str] = {}


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

    return _response(
        404,
        {
            "ok": False,
            "error": "route not found",
        },
    )
