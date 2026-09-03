import io
from urllib.error import HTTPError

import pytest

from narration_content.ghost_http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    GhostHttpResponseTooLargeError,
    GhostHttpTransportError,
    UrllibGhostJsonTransport,
    _NoRedirectHandler,
)

SECRET_URL = (
    "https://gratitude.example/"
    "ghost/api/content/posts/"
    "?key=super-secret-content-key&page=1"
)


class FakeResponse:
    def __init__(
        self,
        body,
        *,
        status=200,
    ):
        self._body = body
        self._status = status

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def getcode(self):
        return self._status

    def read(self, size=-1):
        if size < 0:
            return self._body

        return self._body[:size]


class FakeOpener:
    def __init__(
        self,
        response,
    ):
        self.response = response
        self.requests = []

    def open(
        self,
        request,
        *,
        timeout,
    ):
        self.requests.append(
            (request, timeout)
        )

        return self.response


class RaisingOpener:
    def __init__(
        self,
        exception,
    ):
        self.exception = exception

    def open(
        self,
        request,
        *,
        timeout,
    ):
        raise self.exception


def test_transport_defaults_are_frozen():
    assert DEFAULT_TIMEOUT_SECONDS == 20.0
    assert DEFAULT_MAX_RESPONSE_BYTES == 64 * 1024 * 1024
    assert USER_AGENT == "PocketTTS-ContentSync/1"


def test_transport_decodes_json_object():
    opener = FakeOpener(
        FakeResponse(
            b'{"posts":[],"meta":{"pagination":{}}}'
        )
    )

    transport = UrllibGhostJsonTransport(
        opener=opener
    )

    result = transport.get_json(
        SECRET_URL
    )

    assert result == {
        "posts": [],
        "meta": {
            "pagination": {},
        },
    }


def test_transport_uses_get_accept_header_user_agent_and_timeout():
    opener = FakeOpener(
        FakeResponse(b'{"ok":true}')
    )

    transport = UrllibGhostJsonTransport(
        timeout_seconds=7.5,
        opener=opener,
    )

    transport.get_json(
        SECRET_URL
    )

    assert len(opener.requests) == 1

    request, timeout = opener.requests[0]

    assert request.get_method() == "GET"
    assert timeout == 7.5

    assert (
        request.get_header("Accept")
        == "application/json"
    )

    assert (
        request.get_header("User-agent")
        == USER_AGENT
    )

    # The transport necessarily receives the key-bearing URL,
    # but it must not expose it through its errors.
    assert "super-secret-content-key" in request.full_url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://gratitude.example/posts/?key=x",
        "ftp://gratitude.example/posts/?key=x",
        "https:///missing-host",
        "https://user:password@gratitude.example/posts/?key=x",
        "https://gratitude.example/posts/?key=x#fragment",
    ],
)
def test_transport_rejects_invalid_or_unsafe_urls(url):
    transport = UrllibGhostJsonTransport(
        opener=FakeOpener(
            FakeResponse(b'{"ok":true}')
        )
    )

    with pytest.raises(
        GhostHttpTransportError
    ):
        transport.get_json(url)


@pytest.mark.parametrize(
    "timeout",
    [
        0,
        -1,
        True,
        "20",
    ],
)
def test_timeout_must_be_positive_number(timeout):
    with pytest.raises(
        GhostHttpTransportError,
        match="timeout_seconds",
    ):
        UrllibGhostJsonTransport(
            timeout_seconds=timeout,
            opener=FakeOpener(
                FakeResponse(b'{"ok":true}')
            ),
        )


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        True,
        1.5,
        "1024",
    ],
)
def test_response_limit_must_be_positive_integer(limit):
    with pytest.raises(
        GhostHttpTransportError,
        match="max_response_bytes",
    ):
        UrllibGhostJsonTransport(
            max_response_bytes=limit,
            opener=FakeOpener(
                FakeResponse(b'{"ok":true}')
            ),
        )


def test_response_size_limit_reads_only_limit_plus_one():
    body = b'{"too_large":"' + (b"x" * 100) + b'"}'

    transport = UrllibGhostJsonTransport(
        max_response_bytes=20,
        opener=FakeOpener(
            FakeResponse(body)
        ),
    )

    with pytest.raises(
        GhostHttpResponseTooLargeError
    ):
        transport.get_json(
            SECRET_URL
        )


def test_invalid_utf8_fails_without_exposing_url():
    transport = UrllibGhostJsonTransport(
        opener=FakeOpener(
            FakeResponse(b"\xff\xfe")
        )
    )

    with pytest.raises(
        GhostHttpTransportError
    ) as exc_info:
        transport.get_json(
            SECRET_URL
        )

    message = str(exc_info.value)

    assert "UTF-8" in message
    assert "super-secret-content-key" not in message
    assert SECRET_URL not in message


def test_invalid_json_fails_without_exposing_url():
    transport = UrllibGhostJsonTransport(
        opener=FakeOpener(
            FakeResponse(b"not-json")
        )
    )

    with pytest.raises(
        GhostHttpTransportError
    ) as exc_info:
        transport.get_json(
            SECRET_URL
        )

    message = str(exc_info.value)

    assert "valid JSON" in message
    assert "super-secret-content-key" not in message
    assert SECRET_URL not in message


def test_json_root_must_be_object():
    transport = UrllibGhostJsonTransport(
        opener=FakeOpener(
            FakeResponse(b"[]")
        )
    )

    with pytest.raises(
        GhostHttpTransportError,
        match="root must be an object",
    ):
        transport.get_json(
            SECRET_URL
        )


def test_http_error_does_not_expose_key_bearing_url():
    error = HTTPError(
        SECRET_URL,
        503,
        "Service Unavailable",
        hdrs=None,
        fp=io.BytesIO(b""),
    )

    transport = UrllibGhostJsonTransport(
        opener=RaisingOpener(error)
    )

    with pytest.raises(
        GhostHttpTransportError
    ) as exc_info:
        transport.get_json(
            SECRET_URL
        )

    message = str(exc_info.value)

    assert "503" in message
    assert SECRET_URL not in message
    assert "super-secret-content-key" not in message

    # Python still stores the suppressed exception internally,
    # but normal traceback rendering will not expose it.
    assert exc_info.value.__suppress_context__ is True


def test_unknown_transport_failure_does_not_expose_original_message():
    transport = UrllibGhostJsonTransport(
        opener=RaisingOpener(
            RuntimeError(
                "failure for " + SECRET_URL
            )
        )
    )

    with pytest.raises(
        GhostHttpTransportError
    ) as exc_info:
        transport.get_json(
            SECRET_URL
        )

    message = str(exc_info.value)

    assert message == "Ghost HTTP request failed"
    assert SECRET_URL not in message
    assert "super-secret-content-key" not in message
    assert exc_info.value.__suppress_context__ is True


def test_non_success_status_from_response_is_safe():
    transport = UrllibGhostJsonTransport(
        opener=FakeOpener(
            FakeResponse(
                b'{"error":"unavailable"}',
                status=503,
            )
        )
    )

    with pytest.raises(
        GhostHttpTransportError
    ) as exc_info:
        transport.get_json(
            SECRET_URL
        )

    message = str(exc_info.value)

    assert "503" in message
    assert SECRET_URL not in message
    assert "super-secret-content-key" not in message


def test_redirect_handler_refuses_redirect_request():
    handler = _NoRedirectHandler()

    result = handler.redirect_request(
        req=None,
        fp=None,
        code=302,
        msg="Found",
        headers={},
        newurl=(
            "https://attacker.example/"
            "?key=super-secret-content-key"
        ),
    )

    assert result is None