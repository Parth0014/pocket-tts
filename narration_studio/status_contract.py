"""Frozen Worker -> Studio execution-status contract V1."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

DEV_BUCKET = "pocket-tts-dev-test"

_GENERATION_ID_RE = re.compile(r"^gen_[0-9a-f]{32}$")
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")
_STATUSES = frozenset(
    {
        "RUNNING",
        "COMPLETED",
        "FAILED",
    }
)

_BASE_FIELDS = frozenset(
    {
        "schema_version",
        "generation_id",
        "job_id",
        "job_fingerprint",
        "status",
        "attempt",
        "occurred_at",
    }
)


class StatusContractError(ValueError):
    """Raised when a Worker V2 status event violates its contract."""


def _require_string(
    field: str,
    value: Any,
) -> str:
    if not isinstance(value, str) or not value:
        raise StatusContractError(
            f"{field} must be a non-empty string"
        )
    return value


def _require_pattern(
    field: str,
    value: Any,
    pattern: re.Pattern[str],
) -> str:
    value = _require_string(
        field,
        value,
    )

    if pattern.fullmatch(
        value
    ) is None:
        raise StatusContractError(
            f"{field} has invalid format"
        )

    return value


def _require_utc_z(
    field: str,
    value: Any,
) -> str:
    value = _require_string(
        field,
        value,
    )

    if not value.endswith("Z"):
        raise StatusContractError(
            f"{field} must be UTC RFC3339 ending in Z"
        )

    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00"
        )
    except ValueError:
        raise StatusContractError(
            f"{field} must be valid RFC3339"
        ) from None

    if parsed.utcoffset() != timedelta(0):
        raise StatusContractError(
            f"{field} must represent UTC"
        )

    return value


def validate_status_event_v1(
    event: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(
        event,
        Mapping,
    ):
        raise StatusContractError(
            "status event must be an object"
        )

    if (
        type(
            event.get(
                "schema_version"
            )
        )
        is not int
        or event[
            "schema_version"
        ]
        != 1
    ):
        raise StatusContractError(
            "schema_version must be integer 1"
        )

    generation_id = _require_pattern(
        "generation_id",
        event.get(
            "generation_id"
        ),
        _GENERATION_ID_RE,
    )

    job_id = _require_pattern(
        "job_id",
        event.get(
            "job_id"
        ),
        _JOB_ID_RE,
    )

    job_fingerprint = _require_pattern(
        "job_fingerprint",
        event.get(
            "job_fingerprint"
        ),
        _SHA256_RE,
    )

    status = _require_string(
        "status",
        event.get(
            "status"
        ),
    )

    if status not in _STATUSES:
        raise StatusContractError(
            "status must be RUNNING, COMPLETED, or FAILED"
        )

    attempt = event.get(
        "attempt"
    )

    if (
        isinstance(
            attempt,
            bool,
        )
        or not isinstance(
            attempt,
            int,
        )
        or attempt < 1
    ):
        raise StatusContractError(
            "attempt must be a positive integer"
        )

    occurred_at = _require_utc_z(
        "occurred_at",
        event.get(
            "occurred_at"
        ),
    )

    expected_fields = set(
        _BASE_FIELDS
    )

    if status == "COMPLETED":
        expected_fields.add(
            "output"
        )

    if status == "FAILED":
        expected_fields.add(
            "error_code"
        )

    if set(
        event
    ) != expected_fields:
        raise StatusContractError(
            "status event contains unexpected fields"
        )

    normalized: dict[str, Any] = {
        "schema_version": 1,
        "generation_id": generation_id,
        "job_id": job_id,
        "job_fingerprint": job_fingerprint,
        "status": status,
        "attempt": attempt,
        "occurred_at": occurred_at,
    }

    if status == "COMPLETED":
        output = event.get(
            "output"
        )

        if (
            not isinstance(
                output,
                Mapping,
            )
            or set(
                output
            )
            != {
                "bucket",
                "key",
                "sha256",
            }
        ):
            raise StatusContractError(
                "output must contain exactly bucket, key, and sha256"
            )

        bucket = _require_string(
            "output.bucket",
            output.get(
                "bucket"
            ),
        )

        if bucket != DEV_BUCKET:
            raise StatusContractError(
                "output.bucket must be the DEV bucket"
            )

        key = _require_string(
            "output.key",
            output.get(
                "key"
            ),
        )

        expected_key = (
            f"generations/{generation_id}/output.wav"
        )

        if key != expected_key:
            raise StatusContractError(
                "output.key violates the Worker V1 generation path"
            )

        sha256 = _require_pattern(
            "output.sha256",
            output.get(
                "sha256"
            ),
            _SHA256_RE,
        )

        normalized[
            "output"
        ] = {
            "bucket": bucket,
            "key": key,
            "sha256": sha256,
        }

    if status == "FAILED":
        error_code = _require_pattern(
            "error_code",
            event.get(
                "error_code"
            ),
            _ERROR_CODE_RE,
        )

        normalized[
            "error_code"
        ] = error_code

    return normalized


def canonical_status_json(
    event: Mapping[str, Any],
) -> str:
    return json.dumps(
        validate_status_event_v1(
            event
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def status_event_fingerprint(
    event: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_status_json(
            event
        ).encode(
            "utf-8"
        )
    ).hexdigest()
