"""Authenticated Production Manager backend.

The Manager translates bridge intake into explicit Studio intent. It never
enqueues TTS and never writes production audio/publication state.

Publisher work in this Lambda is dry-run preflight only.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from narration_manager.contracts import (
    ManagerContractError,
    adoption_doc_id,
    adoption_room_id,
    parse_generation_request,
    parse_review_request,
)
from narration_manager.contracts import (
    generation_id as validate_generation_id,
)
from narration_publisher.preflight import (
    PublisherPreflightError,
    next_publication_key,
    production_audio_key,
    validate_ready_generation,
)
from narration_studio.auth import verify_session
from narration_studio.dynamodb import (
    DynamoStudioRepository,
    DynamoVoiceRepository,
)
from narration_studio.models import (
    ArtifactRef,
    GenerationReviewStatus,
    RoomRecord,
    RoomStatus,
    StudioDocumentRevision,
    VoiceStatus,
)
from narration_studio.s3_store import StudioS3ArtifactStore
from narration_studio.service import StudioService

APP_TABLE = os.environ["APP_TABLE"]
VOICE_TABLE = os.environ["VOICE_TABLE"]
PUBLICATION_TABLE = os.environ["PUBLICATION_TABLE"]
DEV_BUCKET = os.environ["DEV_BUCKET"]
OWNER_ID = os.environ["OWNER_ID"]
SESSION_PARAMETER = os.environ[
    "SESSION_SIGNING_SECRET_PARAMETER"
]

COOKIE_NAME = "pocket_tts_session"
PRODUCTION_BUCKET = "gratefulness-narration-audio"

_ddb = boto3.client("dynamodb")
_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")

_deserializer = TypeDeserializer()
_secret_cache: str | None = None

_POST_PATH = re.compile(
    r"^/manager/intakes/([A-Za-z0-9_-]{1,128})$"
)
_ADOPT_PATH = re.compile(
    r"^/manager/intakes/([A-Za-z0-9_-]{1,128})/adopt$"
)
_GENERATIONS_PATH = re.compile(
    r"^/manager/intakes/([A-Za-z0-9_-]{1,128})/generations$"
)
_REVIEW_PATH = re.compile(
    r"^/manager/intakes/([A-Za-z0-9_-]{1,128})/"
    r"generations/(gen_[0-9a-f]{32})/review$"
)
_PREFLIGHT_PATH = re.compile(
    r"^/manager/intakes/([A-Za-z0-9_-]{1,128})/"
    r"generations/(gen_[0-9a-f]{32})/publisher-preflight$"
)


class ManagerRuntimeError(RuntimeError):
    """Safe operational Manager failure."""


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _secret() -> str:
    global _secret_cache

    if _secret_cache is None:
        _secret_cache = _ssm.get_parameter(
            Name=SESSION_PARAMETER,
            WithDecryption=True,
        )["Parameter"]["Value"]

    return _secret_cache


def _response(
    status: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }


def _cookies(event: dict[str, Any]) -> list[str]:
    values = event.get("cookies")
    if isinstance(values, list):
        return [
            value
            for value in values
            if isinstance(value, str)
        ]

    header = (
        event.get("headers", {})
        if isinstance(event.get("headers"), dict)
        else {}
    ).get("cookie")

    return [header] if isinstance(header, str) else []


def _session(event: dict[str, Any]) -> dict[str, Any] | None:
    token = None

    for header in _cookies(event):
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == COOKIE_NAME:
                token = value
                break

    if not token:
        return None

    try:
        claims = verify_session(
            token=token,
            signing_secret=_secret(),
        )
    except Exception:
        return None

    if isinstance(claims, dict):
        subject = claims.get("sub") or claims.get("subject")
    elif isinstance(claims, str):
        subject = claims
    else:
        subject = (
            getattr(claims, "sub", None)
            or getattr(claims, "subject", None)
        )

    if subject != OWNER_ID:
        return None

    return claims


def _method(event: dict[str, Any]) -> str:
    context = event.get("requestContext")
    if not isinstance(context, dict):
        return ""

    http = context.get("http")
    if not isinstance(http, dict):
        return ""

    value = http.get("method")
    return value if isinstance(value, str) else ""


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")

    if raw in (None, ""):
        return {}

    if not isinstance(raw, str):
        raise ManagerContractError(
            "request body must be JSON text"
        )

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ManagerContractError(
            "request body is invalid JSON"
        ) from None

    if not isinstance(value, dict):
        raise ManagerContractError(
            "request body must be an object"
        )

    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return str(value)

    if isinstance(value, list):
        return [_plain(item) for item in value]

    if isinstance(value, dict):
        return {
            key: _plain(item)
            for key, item in value.items()
        }

    return value


def _python_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _plain(
            _deserializer.deserialize(value)
        )
        for key, value in item.items()
    }


def _get(
    *,
    pk: str,
    sk: str,
) -> dict[str, Any] | None:
    response = _ddb.get_item(
        TableName=APP_TABLE,
        Key={
            "pk": {"S": pk},
            "sk": {"S": sk},
        },
        ConsistentRead=True,
    )
    item = response.get("Item")
    return (
        _python_item(item)
        if isinstance(item, dict)
        else None
    )


def _put_python(
    item: dict[str, Any],
) -> None:
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()

    _ddb.put_item(
        TableName=APP_TABLE,
        Item={
            key: serializer.serialize(value)
            for key, value in item.items()
        },
    )


def _query_python(**kwargs) -> list[dict[str, Any]]:
    response = _ddb.query(
        TableName=APP_TABLE,
        **kwargs,
    )

    items = [
        _python_item(item)
        for item in response.get("Items", [])
    ]

    last = response.get("LastEvaluatedKey")

    while last is not None:
        response = _ddb.query(
            TableName=APP_TABLE,
            ExclusiveStartKey=last,
            **kwargs,
        )
        items.extend(
            _python_item(item)
            for item in response.get("Items", [])
        )
        last = response.get("LastEvaluatedKey")

    return items


def _current(post_id: str) -> dict[str, Any]:
    item = _get(
        pk=f"POST#{post_id}",
        sk="BRIDGE#CURRENT",
    )
    if item is None:
        raise ManagerRuntimeError(
            "current bridge intake does not exist"
        )
    return item


def _adoption(post_id: str) -> dict[str, Any] | None:
    return _get(
        pk=f"POST#{post_id}",
        sk="MANAGER#ADOPTION",
    )


def _service() -> tuple[
    StudioService,
    DynamoVoiceRepository,
]:
    studio_repository = DynamoStudioRepository(
        client=_ddb,
        table_name=APP_TABLE,
    )
    voice_repository = DynamoVoiceRepository(
        client=_ddb,
        table_name=VOICE_TABLE,
    )
    artifacts = StudioS3ArtifactStore(
        client=_s3,
        bucket_name=DEV_BUCKET,
    )

    return (
        StudioService(
            artifacts=artifacts,
            studio_repository=studio_repository,
            voice_repository=voice_repository,
            bucket_name=DEV_BUCKET,
        ),
        voice_repository,
    )


def _canonical_document(
    current: dict[str, Any],
) -> dict[str, Any]:
    if current.get("document_bucket") != DEV_BUCKET:
        raise ManagerRuntimeError(
            "bridge document is not in DEV"
        )

    key = current.get("document_key")
    if not isinstance(key, str) or not key:
        raise ManagerRuntimeError(
            "bridge document key is missing"
        )

    try:
        raw = _s3.get_object(
            Bucket=DEV_BUCKET,
            Key=key,
        )["Body"].read()
        value = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ManagerRuntimeError(
            "canonical Narration Document read failed"
        ) from None

    if not isinstance(value, dict):
        raise ManagerRuntimeError(
            "canonical Narration Document is invalid"
        )

    return value


def _adopt(
    post_id: str,
) -> dict[str, Any]:
    current = _current(post_id)
    existing = _adoption(post_id)
    now = _now()

    if (
        existing is not None
        and existing.get("source_content_hash")
        == current.get("content_hash")
    ):
        return {
            **existing,
            "already_adopted": True,
        }

    room_id = (
        existing.get("room_id")
        if existing is not None
        else adoption_room_id(post_id)
    )
    doc_id = (
        existing.get("doc_id")
        if existing is not None
        else adoption_doc_id(post_id)
    )
    revision = (
        int(existing.get("latest_revision", 0)) + 1
        if existing is not None
        else 1
    )

    service, _ = _service()

    room = _get(
        pk=f"ROOM#{room_id}",
        sk="META",
    )

    if room is None:
        title = current.get("title")
        if not isinstance(title, str) or not title.strip():
            title = f"Narration {post_id}"

        service.create_room(
            RoomRecord(
                room_id=room_id,
                owner_id=OWNER_ID,
                title=title[:240],
                status=RoomStatus.ACTIVE,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    elif (
        room.get("owner_id") != OWNER_ID
        or room.get("status") != "ACTIVE"
    ):
        raise ManagerRuntimeError(
            "manager room exists in an incompatible state"
        )

    prepared = service.import_narration_document(
        room_id=room_id,
        doc_id=doc_id,
        revision=revision,
        narration_document=_canonical_document(current),
        created_at=now,
    )

    value = {
        "pk": f"POST#{post_id}",
        "sk": "MANAGER#ADOPTION",
        "entity_type": "production_manager_adoption",
        "schema_version": 1,
        "manager_status": "ADOPTED",
        "post_id": post_id,
        "room_id": room_id,
        "doc_id": doc_id,
        "latest_revision": revision,
        "source_content_hash": (
            prepared.revision.source_content_hash
        ),
        "source_narration_hash": (
            prepared.revision.source_narration_hash
        ),
        "source_processor_version": (
            prepared.revision.source_processor_version
        ),
        "studio_document_bucket": (
            prepared.revision.document.bucket
        ),
        "studio_document_key": (
            prepared.revision.document.key
        ),
        "studio_document_sha256": (
            prepared.revision.document.sha256
        ),
        "created_at": (
            existing.get("created_at", now)
            if existing is not None
            else now
        ),
        "updated_at": now,
    }

    _put_python(value)

    return {
        **value,
        "already_adopted": False,
    }


def _revision(
    adoption: dict[str, Any],
) -> StudioDocumentRevision:
    return StudioDocumentRevision(
        room_id=str(adoption["room_id"]),
        doc_id=str(adoption["doc_id"]),
        revision=int(adoption["latest_revision"]),
        source_post_id=str(adoption["post_id"]),
        source_content_hash=str(
            adoption["source_content_hash"]
        ),
        source_narration_hash=str(
            adoption["source_narration_hash"]
        ),
        source_processor_version=int(
            adoption["source_processor_version"]
        ),
        document=ArtifactRef(
            bucket=str(
                adoption["studio_document_bucket"]
            ),
            key=str(
                adoption["studio_document_key"]
            ),
            sha256=str(
                adoption["studio_document_sha256"]
            ),
        ),
        created_at=str(adoption["updated_at"]),
    )


def _create_generation(
    post_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    request = parse_generation_request(body)
    current = _current(post_id)
    adoption = _adoption(post_id)

    if adoption is None:
        raise ManagerRuntimeError(
            "post must be adopted before generation intent"
        )

    if (
        adoption.get("source_content_hash")
        != current.get("content_hash")
    ):
        raise ManagerRuntimeError(
            "adoption is stale; adopt the current content first"
        )

    service, voices = _service()
    voice = voices.get_voice(
        request.voice_id
    )

    if voice is None:
        raise ManagerRuntimeError(
            "voice does not exist"
        )

    quote_voice = None
    if request.quote_voice_id is not None:
        quote_voice = voices.get_voice(
            request.quote_voice_id
        )
        if quote_voice is None:
            raise ManagerRuntimeError(
                "quote voice does not exist"
            )

    generation_id = (
        "gen_" + uuid.uuid4().hex
    )
    now = _now()

    prepared = service.create_generation(
        room_id=str(adoption["room_id"]),
        generation_id=generation_id,
        revision=_revision(adoption),
        voice=voice,
        quote_mode=request.quote_mode,
        quote_voice=quote_voice,
        created_at=now,
    )

    return {
        "post_id": post_id,
        "room_id": adoption["room_id"],
        "generation_id": (
            prepared.generation.generation_id
        ),
        "source_content_hash": (
            prepared.generation.source_content_hash
        ),
        "source_narration_hash": (
            prepared.generation.source_narration_hash
        ),
        "voice_id": prepared.generation.voice_id,
        "quote_mode": prepared.generation.quote_mode,
        "generation_status": None,
        "review_status": (
            prepared.generation.review_status.value
        ),
        "enqueued": False,
    }


def _generations(
    post_id: str,
) -> list[dict[str, Any]]:
    adoption = _adoption(post_id)

    if adoption is None:
        return []

    items = _query_python(
        KeyConditionExpression=(
            "pk = :pk AND begins_with(sk, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": {
                "S": f"ROOM#{adoption['room_id']}"
            },
            ":prefix": {"S": "GEN#"},
        },
    )

    items.sort(
        key=lambda item: str(
            item.get("created_at", "")
        ),
        reverse=True,
    )
    return items


def _generation(
    post_id: str,
    generation_id: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    validate_generation_id(
        generation_id
    )
    adoption = _adoption(post_id)

    if adoption is None:
        raise ManagerRuntimeError(
            "post is not adopted"
        )

    generation = _get(
        pk=f"ROOM#{adoption['room_id']}",
        sk=f"GEN#{generation_id}",
    )

    if generation is None:
        raise ManagerRuntimeError(
            "generation does not exist"
        )

    if (
        generation.get("source_post_id")
        != post_id
    ):
        raise ManagerRuntimeError(
            "generation does not belong to this post"
        )

    return adoption, generation


def _mark_other_selections_outdated(
    *,
    room_id: str,
    target_generation_id: str,
    now: str,
) -> None:
    items = _query_python(
        KeyConditionExpression=(
            "pk = :pk AND begins_with(sk, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": {"S": f"ROOM#{room_id}"},
            ":prefix": {"S": "GEN#"},
        },
    )

    for item in items:
        if (
            item.get("generation_id")
            == target_generation_id
            or item.get("review_status")
            not in {"SELECTED", "READY"}
        ):
            continue

        _ddb.update_item(
            TableName=APP_TABLE,
            Key={
                "pk": {"S": f"ROOM#{room_id}"},
                "sk": {
                    "S": (
                        "GEN#"
                        + str(item["generation_id"])
                    )
                },
            },
            UpdateExpression=(
                "SET review_status = :outdated, "
                "updated_at = :now"
            ),
            ExpressionAttributeValues={
                ":outdated": {"S": "OUTDATED"},
                ":now": {"S": now},
            },
        )


def _review(
    post_id: str,
    generation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    request = parse_review_request(body)
    adoption, generation = _generation(
        post_id,
        generation_id,
    )
    current = _current(post_id)
    now = _now()
    target = request.review_status

    if target == "OUTDATED":
        pass
    else:
        if (
            generation.get("generation_status")
            != "COMPLETED"
        ):
            raise ManagerRuntimeError(
                "only COMPLETED generations can be selected or readied"
            )

        if (
            generation.get("source_content_hash")
            != current.get("content_hash")
            or adoption.get("source_content_hash")
            != current.get("content_hash")
        ):
            raise ManagerRuntimeError(
                "generation content is no longer current"
            )

        _, voices = _service()
        voice = voices.get_voice(
            str(generation["voice_id"])
        )

        if (
            voice is None
            or voice.status is not VoiceStatus.ACTIVE
        ):
            raise ManagerRuntimeError(
                "generation voice is not ACTIVE"
            )

        if (
            target == "READY"
            and generation.get("review_status")
            != GenerationReviewStatus.SELECTED.value
        ):
            raise ManagerRuntimeError(
                "generation must be SELECTED before READY"
            )

    if target == "SELECTED":
        _mark_other_selections_outdated(
            room_id=str(adoption["room_id"]),
            target_generation_id=generation_id,
            now=now,
        )

    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {
                "S": f"ROOM#{adoption['room_id']}"
            },
            "sk": {
                "S": f"GEN#{generation_id}"
            },
        },
        UpdateExpression=(
            "SET review_status = :review, "
            "updated_at = :now"
        ),
        ExpressionAttributeValues={
            ":review": {"S": target},
            ":now": {"S": now},
        },
    )

    return {
        "post_id": post_id,
        "generation_id": generation_id,
        "review_status": target,
    }


def _publication_keys(
    post_id: str,
) -> list[str]:
    kwargs = {
        "TableName": PUBLICATION_TABLE,
        "KeyConditionExpression": (
            "post_id = :post_id"
        ),
        "ExpressionAttributeValues": {
            ":post_id": {"S": post_id}
        },
        "ProjectionExpression": "publication_key",
    }

    response = _ddb.query(
        **kwargs
    )
    keys = []

    while True:
        for item in response.get(
            "Items",
            [],
        ):
            value = item.get(
                "publication_key",
                {},
            ).get("S")
            if isinstance(value, str):
                keys.append(value)

        last = response.get(
            "LastEvaluatedKey"
        )

        if last is None:
            break

        response = _ddb.query(
            ExclusiveStartKey=last,
            **kwargs,
        )

    return keys


def _publisher_preflight(
    post_id: str,
    generation_id: str,
) -> dict[str, Any]:
    _, generation = _generation(
        post_id,
        generation_id,
    )
    current = _current(post_id)

    _, voices = _service()
    voice = voices.get_voice(
        str(generation["voice_id"])
    )
    voice_status = (
        voice.status.value
        if voice is not None
        else "MISSING"
    )

    validate_ready_generation(
        generation=generation,
        current_content_hash=str(
            current["content_hash"]
        ),
        voice_status=voice_status,
    )

    output_key = str(
        generation["output_key"]
    )

    try:
        head = _s3.head_object(
            Bucket=DEV_BUCKET,
            Key=output_key,
        )
    except ClientError:
        raise ManagerRuntimeError(
            "completed DEV output is not readable"
        ) from None

    metadata = {
        str(key).lower(): str(value)
        for key, value in head.get(
            "Metadata",
            {},
        ).items()
    }

    expected_sha = str(
        generation["output_sha256"]
    )
    metadata_sha = (
        metadata.get("pocket-output-sha256")
        or metadata.get("sha256")
    )

    if (
        metadata_sha is not None
        and metadata_sha != expected_sha
    ):
        raise ManagerRuntimeError(
            "DEV output metadata hash conflicts"
        )

    publication_key = next_publication_key(
        _publication_keys(post_id)
    )

    return {
        "publishable": True,
        "dry_run": True,
        "post_id": post_id,
        "generation_id": generation_id,
        "voice_id": generation["voice_id"],
        "publication_key": publication_key,
        "source": {
            "bucket": DEV_BUCKET,
            "key": output_key,
            "sha256": expected_sha,
        },
        "production_target": {
            "bucket": PRODUCTION_BUCKET,
            "key": production_audio_key(
                post_id=post_id,
                voice_id=str(
                    generation["voice_id"]
                ),
                publication_key=publication_key,
            ),
        },
        "production_write_performed": False,
    }


def _list_intakes() -> dict[str, Any]:
    items = _query_python(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={
            ":pk": {"S": "MANAGER#INTAKE"}
        },
    )
    items.sort(
        key=lambda item: str(
            item.get("updated_at", "")
        ),
        reverse=True,
    )

    return {
        "items": items,
        "count": len(items),
    }


def _detail(
    post_id: str,
) -> dict[str, Any]:
    current = _current(post_id)
    history = _query_python(
        KeyConditionExpression=(
            "pk = :pk AND begins_with(sk, :prefix)"
        ),
        ExpressionAttributeValues={
            ":pk": {"S": f"POST#{post_id}"},
            ":prefix": {"S": "BRIDGE#"},
        },
    )

    history = [
        item
        for item in history
        if item.get("sk") != "BRIDGE#CURRENT"
    ]
    history.sort(
        key=lambda item: str(
            item.get("created_at", "")
        ),
        reverse=True,
    )

    return {
        "current": current,
        "history": history,
        "adoption": _adoption(post_id),
        "generations": _generations(post_id),
    }


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    del context

    if _session(event) is None:
        return _response(
            401,
            {"error": "unauthorized"},
        )

    path = event.get("rawPath")
    if not isinstance(path, str):
        return _response(
            404,
            {"error": "not_found"},
        )

    method = _method(event)

    try:
        if method == "GET" and path == "/manager/intakes":
            return _response(
                200,
                _list_intakes(),
            )

        match = _POST_PATH.fullmatch(path)
        if method == "GET" and match is not None:
            return _response(
                200,
                _detail(match.group(1)),
            )

        match = _ADOPT_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _response(
                200,
                _adopt(match.group(1)),
            )

        match = _GENERATIONS_PATH.fullmatch(path)
        if match is not None:
            post_id = match.group(1)

            if method == "GET":
                return _response(
                    200,
                    {
                        "items": _generations(
                            post_id
                        )
                    },
                )

            if method == "POST":
                return _response(
                    201,
                    _create_generation(
                        post_id,
                        _body(event),
                    ),
                )

        match = _REVIEW_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _response(
                200,
                _review(
                    match.group(1),
                    match.group(2),
                    _body(event),
                ),
            )

        match = _PREFLIGHT_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _response(
                200,
                _publisher_preflight(
                    match.group(1),
                    match.group(2),
                ),
            )

        return _response(
            404,
            {"error": "not_found"},
        )

    except ManagerContractError as exc:
        return _response(
            400,
            {
                "error": "invalid_request",
                "message": str(exc),
            },
        )
    except PublisherPreflightError as exc:
        return _response(
            409,
            {
                "error": "not_publishable",
                "message": str(exc),
                "production_write_performed": False,
            },
        )
    except ManagerRuntimeError as exc:
        return _response(
            409,
            {
                "error": "manager_conflict",
                "message": str(exc),
            },
        )
    except Exception:
        return _response(
            500,
            {"error": "internal_error"},
        )
