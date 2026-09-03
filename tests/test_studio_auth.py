import pytest

from narration_studio.auth import SessionError, sign_session, verify_session

SECRET = "s" * 64


def test_session_round_trip():
    token = sign_session(
        subject="internal-dashboard",
        signing_secret=SECRET,
        now=1000,
        ttl_seconds=3600,
    )

    assert verify_session(
        token,
        signing_secret=SECRET,
        now=1200,
    ) == {
        "v": 1,
        "sub": "internal-dashboard",
        "iat": 1000,
        "exp": 4600,
    }


def test_tamper_fails():
    token = sign_session(
        subject="internal-dashboard",
        signing_secret=SECRET,
        now=1000,
    )

    payload, signature = token.split(".")

    with pytest.raises(SessionError):
        verify_session(
            payload + "x." + signature,
            signing_secret=SECRET,
            now=1001,
        )


def test_expired_fails():
    token = sign_session(
        subject="internal-dashboard",
        signing_secret=SECRET,
        now=1000,
        ttl_seconds=60,
    )

    with pytest.raises(SessionError):
        verify_session(
            token,
            signing_secret=SECRET,
            now=1060,
        )
