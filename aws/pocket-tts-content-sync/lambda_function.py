"""AWS Lambda entry point for Ghost Content Sync V1."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import boto3

from narration_content.dynamodb_sync import (
    DynamoCatalogStore,
    DynamoSyncStateStore,
)
from narration_content.ghost_client import GhostContentClient
from narration_content.ghost_http import UrllibGhostJsonTransport
from narration_content.s3_artifacts import ContentSyncS3ArtifactStore
from narration_content.sync_core import ContentSyncCore
from narration_content.sync_runner import ContentSyncRunner

_STATE_STORE = None
_RUNNER = None


def _required_env(name: str) -> str:
    value = os.environ.get(name)

    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            f"missing required environment variable: {name}"
        )

    return value


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _verification_pending(state) -> bool:
    value = state.verification_pending

    if callable(value):
        value = value()

    if not isinstance(value, bool):
        raise RuntimeError(
            "SyncState verification_pending is not boolean"
        )

    return value


def _state_payload(state) -> dict[str, Any] | None:
    if state is None:
        return None

    return {
        "sync_id": state.sync_id,
        "status": state.status.value,
        "next_page": state.next_page,
        "started_at": state.started_at,
        "updated_at": state.updated_at,
        "expected_total": state.expected_total,
        "expected_pages": state.expected_pages,
        "completed_at": state.completed_at,
        "last_error_code": state.last_error_code,
        "verification_pending": _verification_pending(
            state
        ),
    }


def _get_state_store():
    global _STATE_STORE

    if _STATE_STORE is not None:
        return _STATE_STORE

    app_table = _required_env(
        "APP_TABLE"
    )

    dynamodb = boto3.resource(
        "dynamodb"
    )

    _STATE_STORE = DynamoSyncStateStore(
        table=dynamodb.Table(
            app_table
        )
    )

    return _STATE_STORE


def _get_content_api_key() -> str:
    parameter_name = _required_env(
        "GHOST_CONTENT_API_KEY_PARAMETER"
    )

    ssm = boto3.client(
        "ssm"
    )

    try:
        response = ssm.get_parameter(
            Name=parameter_name,
            WithDecryption=True,
        )

        value = response[
            "Parameter"
        ]["Value"]

    except Exception:
        raise RuntimeError(
            "Ghost Content API key retrieval failed"
        ) from None

    if not isinstance(value, str) or not value:
        raise RuntimeError(
            "Ghost Content API key is empty"
        )

    return value


def _get_runner():
    global _RUNNER

    if _RUNNER is not None:
        return _RUNNER

    posts_table = _required_env(
        "POSTS_TABLE"
    )

    app_table = _required_env(
        "APP_TABLE"
    )

    dev_bucket = _required_env(
        "DEV_BUCKET"
    )

    ghost_base_url = _required_env(
        "GHOST_BASE_URL"
    )

    dynamodb = boto3.resource(
        "dynamodb"
    )

    catalog = DynamoCatalogStore(
        table=dynamodb.Table(
            posts_table
        )
    )

    state_store = DynamoSyncStateStore(
        table=dynamodb.Table(
            app_table
        )
    )

    artifacts = ContentSyncS3ArtifactStore(
        client=boto3.client(
            "s3"
        ),
        bucket_name=dev_bucket,
    )

    ghost = GhostContentClient(
        base_url=ghost_base_url,
        content_api_key=_get_content_api_key(),
        transport=UrllibGhostJsonTransport(),
    )

    core = ContentSyncCore(
        artifacts=artifacts,
        catalog=catalog,
        state_store=state_store,
    )

    _RUNNER = ContentSyncRunner(
        core=core,
        state_store=state_store,
        fetch_page=ghost.fetch_posts_page,
        now=_utc_now,
    )

    return _RUNNER


def lambda_handler(
    event: Mapping[str, Any] | None,
    context,
) -> dict[str, Any]:
    del context

    if event is None:
        event = {}

    if not isinstance(event, Mapping):
        raise ValueError(
            "event must be an object"
        )

    action = event.get(
        "action",
        "status",
    )

    if action == "status":
        state = (
            _get_state_store()
            .get_current()
        )

        return {
            "ok": True,
            "action": "STATUS",
            "state": _state_payload(
                state
            ),
        }

    if action != "run":
        raise ValueError(
            "action must be status or run"
        )

    start_new = event.get(
        "start_new",
        False,
    )

    resume_failed = event.get(
        "resume_failed",
        False,
    )

    if not isinstance(
        start_new,
        bool,
    ):
        raise ValueError(
            "start_new must be boolean"
        )

    if not isinstance(
        resume_failed,
        bool,
    ):
        raise ValueError(
            "resume_failed must be boolean"
        )

    result = (
        _get_runner()
        .run_once(
            start_new=start_new,
            resume_failed=resume_failed,
        )
    )

    payload: dict[str, Any] = {
        "ok": True,
        "action": result.action,
        "state": _state_payload(
            result.state
        ),
    }

    if result.reconciliation is not None:
        payload["reconciliation"] = {
            "marked_post_ids": list(
                result
                .reconciliation
                .marked_post_ids
            ),
            "webhook_race_skipped_post_ids": list(
                result
                .reconciliation
                .webhook_race_skipped_post_ids
            ),
        }

    return payload