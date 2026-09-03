"""Concrete HTTPS JSON transport for Ghost Content Sync.

Unlike ghost_client.py, this module is allowed to perform real HTTP I/O.

Security properties:

- HTTPS only;
- redirects disabled;
- request URLs never appear in raised transport messages;
- redirect and HTTP exceptions that may contain the URL are suppressed;
- response size is bounded;
- response JSON must be UTF-8 and have an object root.

The Content API key is carried in the Ghost request query string, so avoiding
redirects and URL-bearing error messages is a deliberate secret-handling
boundary.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
USER_AGENT = "PocketTTS-ContentSync/1"


class GhostHttpTransportError(RuntimeError):
    """Base error for the concrete Ghost HTTPS transport."""


class GhostHttpResponseTooLargeError(GhostHttpTransportError):
    """Raised when one JSON response exceeds the configured byte limit."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects rather than forwarding a key-bearing URL."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def _build_no_redirect_opener():
    context = ssl.create_default_context()

    return build_opener(
        _NoRedirectHandler(),
        HTTPSHandler(context=context),
    )


class UrllibGhostJsonTransport:
    """Concrete urllib implementation of GhostJsonTransport."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener=None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise GhostHttpTransportError(
                "timeout_seconds must be a positive number"
            )

        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes < 1
        ):
            raise GhostHttpTransportError(
                "max_response_bytes must be a positive integer"
            )

        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._opener = (
            opener
            if opener is not None
            else _build_no_redirect_opener()
        )

    def get_json(
        self,
        url: str,
    ) -> Mapping[str, Any]:
        """GET one HTTPS URL and return a decoded JSON object."""

        _validate_https_url(url)

        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

        try:
            with self._opener.open(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                status = response.getcode()

                if (
                    status is not None
                    and not 200 <= int(status) < 300
                ):
                    raise GhostHttpTransportError(
                        "Ghost HTTP request failed "
                        f"with status {status}"
                    )

                body = response.read(
                    self._max_response_bytes + 1
                )

        except GhostHttpTransportError:
            raise
        except HTTPError as exc:
            raise GhostHttpTransportError(
                "Ghost HTTP request failed "
                f"with status {exc.code}"
            ) from None
        except Exception:
            # urllib exceptions can contain the full request URL,
            # including the Content API key. Never propagate them.
            raise GhostHttpTransportError(
                "Ghost HTTP request failed"
            ) from None

        if len(body) > self._max_response_bytes:
            raise GhostHttpResponseTooLargeError(
                "Ghost HTTP response exceeded configured byte limit"
            )

        try:
            text = body.decode(
                "utf-8",
                errors="strict",
            )
        except UnicodeDecodeError:
            raise GhostHttpTransportError(
                "Ghost HTTP response is not valid UTF-8"
            ) from None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise GhostHttpTransportError(
                "Ghost HTTP response is not valid JSON"
            ) from None

        if not isinstance(payload, Mapping):
            raise GhostHttpTransportError(
                "Ghost HTTP JSON root must be an object"
            )

        return payload


def _validate_https_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        raise GhostHttpTransportError(
            "Ghost request URL must be a non-empty string"
        )

    parts = urlsplit(url)

    if parts.scheme != "https":
        raise GhostHttpTransportError(
            "Ghost request URL must use HTTPS"
        )

    if not parts.hostname:
        raise GhostHttpTransportError(
            "Ghost request URL must contain a host"
        )

    if (
        parts.username is not None
        or parts.password is not None
    ):
        raise GhostHttpTransportError(
            "Ghost request URL must not contain user info"
        )

    if parts.fragment:
        raise GhostHttpTransportError(
            "Ghost request URL must not contain a fragment"
        )