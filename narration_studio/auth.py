"""Stateless signed sessions for the internal Narration Studio."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

SESSION_SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 12 * 60 * 60


class SessionError(ValueError):
    """Invalid or expired internal Studio session."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SessionError("invalid session component")

    try:
        return base64.urlsafe_b64decode(
            value + ("=" * (-len(value) % 4))
        )
    except Exception as exc:
        raise SessionError("invalid session component") from exc


def sign_session(
    *,
    subject: str,
    signing_secret: str,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    if not isinstance(subject, str) or not subject:
        raise SessionError("subject is required")

    if not isinstance(signing_secret, str) or len(signing_secret) < 32:
        raise SessionError("signing secret is too short")

    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise SessionError("invalid session TTL")

    if ttl_seconds < 60 or ttl_seconds > 7 * 24 * 60 * 60:
        raise SessionError("invalid session TTL")

    issued_at = int(time.time()) if now is None else int(now)

    payload: dict[str, Any] = {
        "v": SESSION_SCHEMA_VERSION,
        "sub": subject,
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
    }

    encoded = _encode(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )

    signature = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()

    return encoded + "." + _encode(signature)


def verify_session(
    token: str,
    *,
    signing_secret: str,
    now: int | None = None,
) -> dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 1:
        raise SessionError("invalid session token")

    encoded, encoded_signature = token.split(".")

    expected = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(
        _decode(encoded_signature),
        expected,
    ):
        raise SessionError("invalid session signature")

    try:
        payload = json.loads(
            _decode(encoded).decode("utf-8")
        )
    except Exception as exc:
        raise SessionError("invalid session payload") from exc

    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "sub", "iat", "exp"}
    ):
        raise SessionError("invalid session payload")

    if payload["v"] != SESSION_SCHEMA_VERSION:
        raise SessionError("unsupported session version")

    if not isinstance(payload["sub"], str) or not payload["sub"]:
        raise SessionError("invalid session subject")

    if (
        type(payload["iat"]) is not int
        or type(payload["exp"]) is not int
    ):
        raise SessionError("invalid session timestamps")

    current = int(time.time()) if now is None else int(now)

    if payload["iat"] > current + 60:
        raise SessionError("session issued in the future")

    if payload["exp"] <= current:
        raise SessionError("session expired")

    if payload["exp"] - payload["iat"] > 7 * 24 * 60 * 60:
        raise SessionError("invalid session lifetime")

    return payload
