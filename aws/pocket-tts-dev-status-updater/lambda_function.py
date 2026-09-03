"""DEV Studio generation execution-status updater."""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from narration_studio.status_contract import (
    StatusContractError,
    status_event_fingerprint,
    validate_status_event_v1,
)

APP_TABLE = os.environ["APP_TABLE"]

_ddb = boto3.client("dynamodb")


def _log(
    event_name: str,
    **fields: Any,
) -> None:
    print(
        json.dumps(
            {
                "event": event_name,
                **fields,
            },
            sort_keys=True,
            default=str,
        )
    )


def _error_code(
    exc: Exception,
) -> str:
    response = getattr(
        exc,
        "response",
        {},
    )

    if not isinstance(
        response,
        dict,
    ):
        return ""

    error = response.get(
        "Error",
        {},
    )

    if not isinstance(
        error,
        dict,
    ):
        return ""

    return str(
        error.get(
            "Code",
            "",
        )
    )


def _route_room_id(
    generation_id: str,
) -> str:
    response = _ddb.get_item(
        TableName=APP_TABLE,
        Key={
            "pk": {
                "S": f"GEN#{generation_id}",
            },
            "sk": {
                "S": "ROUTE",
            },
        },
        ConsistentRead=True,
    )

    item = response.get(
        "Item"
    )

    if not isinstance(
        item,
        dict,
    ):
        raise RuntimeError(
            "generation route not found"
        )

    try:
        stored_generation_id = item[
            "generation_id"
        ][
            "S"
        ]
        room_id = item[
            "room_id"
        ][
            "S"
        ]

    except (
        KeyError,
        TypeError,
    ):
        raise RuntimeError(
            "generation route is malformed"
        ) from None

    if (
        stored_generation_id
        != generation_id
        or not room_id.startswith(
            "room_"
        )
    ):
        raise RuntimeError(
            "generation route does not match status event"
        )

    return room_id


def _update_running(
    *,
    room_id: str,
    event: dict[str, Any],
    event_fingerprint: str,
) -> None:
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {
                "S": f"ROOM#{room_id}",
            },
            "sk": {
                "S": (
                    "GEN#"
                    + event[
                        "generation_id"
                    ]
                ),
            },
        },
        ConditionExpression=(
            "#generation_id = :generation_id "
            "AND #job_id = :job_id "
            "AND #worker_job_fingerprint = :job_fingerprint "
            "AND (#generation_status = :queued "
            "OR #generation_status = :running)"
        ),
        UpdateExpression=(
            "SET #generation_status = :running, "
            "#worker_attempt = :attempt, "
            "#started_at = if_not_exists(#started_at, :occurred_at), "
            "#updated_at = :occurred_at, "
            "#last_status_event_fingerprint = :event_fingerprint"
        ),
        ExpressionAttributeNames={
            "#generation_id": "generation_id",
            "#job_id": "job_id",
            "#worker_job_fingerprint": (
                "worker_job_fingerprint"
            ),
            "#generation_status": "generation_status",
            "#worker_attempt": "worker_attempt",
            "#started_at": "started_at",
            "#updated_at": "updated_at",
            "#last_status_event_fingerprint": (
                "last_status_event_fingerprint"
            ),
        },
        ExpressionAttributeValues={
            ":generation_id": {
                "S": event[
                    "generation_id"
                ]
            },
            ":job_id": {
                "S": event[
                    "job_id"
                ]
            },
            ":job_fingerprint": {
                "S": event[
                    "job_fingerprint"
                ]
            },
            ":queued": {
                "S": "QUEUED"
            },
            ":running": {
                "S": "RUNNING"
            },
            ":attempt": {
                "N": str(
                    event[
                        "attempt"
                    ]
                )
            },
            ":occurred_at": {
                "S": event[
                    "occurred_at"
                ]
            },
            ":event_fingerprint": {
                "S": event_fingerprint
            },
        },
    )


def _update_completed(
    *,
    room_id: str,
    event: dict[str, Any],
    event_fingerprint: str,
) -> None:
    output = event[
        "output"
    ]

    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {
                "S": f"ROOM#{room_id}",
            },
            "sk": {
                "S": (
                    "GEN#"
                    + event[
                        "generation_id"
                    ]
                ),
            },
        },
        ConditionExpression=(
            "#generation_id = :generation_id "
            "AND #job_id = :job_id "
            "AND #worker_job_fingerprint = :job_fingerprint "
            "AND (#generation_status = :queued "
            "OR #generation_status = :running "
            "OR #generation_status = :completed)"
        ),
        UpdateExpression=(
            "SET #generation_status = :completed, "
            "#worker_attempt = :attempt, "
            "#output_bucket = :output_bucket, "
            "#output_key = :output_key, "
            "#output_sha256 = :output_sha256, "
            "#completed_at = if_not_exists(#completed_at, :occurred_at), "
            "#updated_at = :occurred_at, "
            "#last_status_event_fingerprint = :event_fingerprint"
        ),
        ExpressionAttributeNames={
            "#generation_id": "generation_id",
            "#job_id": "job_id",
            "#worker_job_fingerprint": (
                "worker_job_fingerprint"
            ),
            "#generation_status": "generation_status",
            "#worker_attempt": "worker_attempt",
            "#output_bucket": "output_bucket",
            "#output_key": "output_key",
            "#output_sha256": "output_sha256",
            "#completed_at": "completed_at",
            "#updated_at": "updated_at",
            "#last_status_event_fingerprint": (
                "last_status_event_fingerprint"
            ),
        },
        ExpressionAttributeValues={
            ":generation_id": {
                "S": event[
                    "generation_id"
                ]
            },
            ":job_id": {
                "S": event[
                    "job_id"
                ]
            },
            ":job_fingerprint": {
                "S": event[
                    "job_fingerprint"
                ]
            },
            ":queued": {
                "S": "QUEUED"
            },
            ":running": {
                "S": "RUNNING"
            },
            ":completed": {
                "S": "COMPLETED"
            },
            ":attempt": {
                "N": str(
                    event[
                        "attempt"
                    ]
                )
            },
            ":output_bucket": {
                "S": output[
                    "bucket"
                ]
            },
            ":output_key": {
                "S": output[
                    "key"
                ]
            },
            ":output_sha256": {
                "S": output[
                    "sha256"
                ]
            },
            ":occurred_at": {
                "S": event[
                    "occurred_at"
                ]
            },
            ":event_fingerprint": {
                "S": event_fingerprint
            },
        },
    )


def _update_failed(
    *,
    room_id: str,
    event: dict[str, Any],
    event_fingerprint: str,
) -> None:
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {
                "S": f"ROOM#{room_id}",
            },
            "sk": {
                "S": (
                    "GEN#"
                    + event[
                        "generation_id"
                    ]
                ),
            },
        },
        ConditionExpression=(
            "#generation_id = :generation_id "
            "AND #job_id = :job_id "
            "AND #worker_job_fingerprint = :job_fingerprint "
            "AND (#generation_status = :queued "
            "OR #generation_status = :running "
            "OR #generation_status = :failed)"
        ),
        UpdateExpression=(
            "SET #generation_status = :failed, "
            "#worker_attempt = :attempt, "
            "#last_error_code = :error_code, "
            "#failed_at = if_not_exists(#failed_at, :occurred_at), "
            "#updated_at = :occurred_at, "
            "#last_status_event_fingerprint = :event_fingerprint"
        ),
        ExpressionAttributeNames={
            "#generation_id": "generation_id",
            "#job_id": "job_id",
            "#worker_job_fingerprint": (
                "worker_job_fingerprint"
            ),
            "#generation_status": "generation_status",
            "#worker_attempt": "worker_attempt",
            "#last_error_code": "last_error_code",
            "#failed_at": "failed_at",
            "#updated_at": "updated_at",
            "#last_status_event_fingerprint": (
                "last_status_event_fingerprint"
            ),
        },
        ExpressionAttributeValues={
            ":generation_id": {
                "S": event[
                    "generation_id"
                ]
            },
            ":job_id": {
                "S": event[
                    "job_id"
                ]
            },
            ":job_fingerprint": {
                "S": event[
                    "job_fingerprint"
                ]
            },
            ":queued": {
                "S": "QUEUED"
            },
            ":running": {
                "S": "RUNNING"
            },
            ":failed": {
                "S": "FAILED"
            },
            ":attempt": {
                "N": str(
                    event[
                        "attempt"
                    ]
                )
            },
            ":error_code": {
                "S": event[
                    "error_code"
                ]
            },
            ":occurred_at": {
                "S": event[
                    "occurred_at"
                ]
            },
            ":event_fingerprint": {
                "S": event_fingerprint
            },
        },
    )


def _apply_status(
    raw_event: Any,
) -> dict[str, Any]:
    event = validate_status_event_v1(
        raw_event
    )

    room_id = _route_room_id(
        event[
            "generation_id"
        ]
    )

    fingerprint = status_event_fingerprint(
        event
    )

    try:
        if event[
            "status"
        ] == "RUNNING":
            _update_running(
                room_id=room_id,
                event=event,
                event_fingerprint=fingerprint,
            )

        elif event[
            "status"
        ] == "COMPLETED":
            _update_completed(
                room_id=room_id,
                event=event,
                event_fingerprint=fingerprint,
            )

        elif event[
            "status"
        ] == "FAILED":
            _update_failed(
                room_id=room_id,
                event=event,
                event_fingerprint=fingerprint,
            )

        else:
            raise AssertionError(
                "validated status is unknown"
            )

    except ClientError as exc:
        if _error_code(
            exc
        ) == (
            "ConditionalCheckFailedException"
        ):
            raise RuntimeError(
                "generation status transition rejected"
            ) from None

        raise

    _log(
        "generation_status_applied",
        generation_id=event[
            "generation_id"
        ],
        job_id=event[
            "job_id"
        ],
        generation_status=event[
            "status"
        ],
        attempt=event[
            "attempt"
        ],
    )

    return event


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    records = event.get(
        "Records"
    )

    if (
        not isinstance(
            records,
            list,
        )
        or not records
        or not all(
            isinstance(
                record,
                dict,
            )
            and record.get(
                "eventSource"
            )
            == "aws:sqs"
            for record in records
        )
    ):
        raise ValueError(
            "status updater accepts SQS events only"
        )

    results = []

    for record in records:
        try:
            raw = json.loads(
                record[
                    "body"
                ]
            )
        except (
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ):
            raise ValueError(
                "status message body must be JSON"
            ) from None

        try:
            applied = _apply_status(
                raw
            )
        except StatusContractError:
            _log(
                "generation_status_rejected",
                reason="contract",
            )
            raise

        results.append(
            {
                "generation_id": applied[
                    "generation_id"
                ],
                "status": applied[
                    "status"
                ],
            }
        )

    return {
        "status": "ok",
        "results": results,
    }
