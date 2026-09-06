"""Team-facing internal Narration Studio.

The Studio reads published Ghost posts directly from the approved Ghost Content
API, normalizes them through Processor V3, and creates DEV Studio
room/document/generation intent automatically.

This Lambda never sends SQS messages. When execution is enabled, the browser
uses the already-authenticated App API enqueue route after this Lambda creates
the generation intent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from narration_content.document import build_document
from narration_content.normalizer import normalize_ghost_html
from narration_studio.auth import verify_session
from narration_studio.dispatch import WorkerVoiceS3Store
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
from narration_studio.worker_contract import (
    prepare_worker_voice_reference,
    worker_output_key,
)

APP_TABLE = os.environ["APP_TABLE"]
VOICE_TABLE = os.environ["VOICE_TABLE"]
DEV_BUCKET = os.environ["DEV_BUCKET"]
OWNER_ID = os.environ["OWNER_ID"]
SESSION_PARAMETER = os.environ["SESSION_SIGNING_SECRET_PARAMETER"]
GHOST_PARAMETER = os.environ["GHOST_CONTENT_API_KEY_PARAMETER"]
GHOST_BASE_URL = os.environ["GHOST_BASE_URL"].rstrip("/")
EXECUTION_ENABLED = os.environ.get("EXECUTION_ENABLED", "false").lower() == "true"

COOKIE_NAME = "pocket_tts_session"
STATIC_ROOT = Path(__file__).resolve().parent / "team_studio_web"
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
CATALOG_CACHE_SECONDS = 90

_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VOICE_ID_RE = re.compile(r"^voice_[0-9a-f]{32}$")
_GENERATION_ID_RE = re.compile(r"^gen_[0-9a-f]{32}$")
_POST_PATH = re.compile(r"^/studio-api/posts/([A-Za-z0-9_-]{1,128})$")
_GENERATIONS_PATH = re.compile(
    r"^/studio-api/posts/([A-Za-z0-9_-]{1,128})/generations$"
)
_REVIEW_PATH = re.compile(
    r"^/studio-api/posts/([A-Za-z0-9_-]{1,128})/"
    r"generations/(gen_[0-9a-f]{32})/review$"
)
_AUDIO_PATH = re.compile(
    r"^/studio-api/posts/([A-Za-z0-9_-]{1,128})/"
    r"generations/(gen_[0-9a-f]{32})/audio$"
)
_VOICE_AUDIO_PATH = re.compile(
    r"^/studio-api/voices/(voice_[0-9a-f]{32})/audio$"
)
_VOICE_ARCHIVE_PATH = re.compile(
    r"^/studio-api/voices/(voice_[0-9a-f]{32})/archive$"
)

_ddb = boto3.client("dynamodb")
_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")
_deserializer = TypeDeserializer()
_session_secret_cache: str | None = None
_ghost_key_cache: str | None = None
_catalog_cache: tuple[float, list[dict[str, Any]]] | None = None


class StudioError(RuntimeError):
    """Safe Studio API error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def _python_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: _plain(_deserializer.deserialize(value)) for key, value in item.items()}


def _json(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    }


def _text(status: int, body: str, content_type: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": f"{content_type}; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": (
                "default-src 'self'; "
                "img-src 'self' data:; "
                "style-src 'self' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; "
                "script-src 'self'; "
                "media-src 'self' https://*.amazonaws.com; "
                "connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
        "body": body,
    }


def _static(filename: str, content_type: str) -> dict[str, Any]:
    try:
        body = (STATIC_ROOT / filename).read_text(encoding="utf-8")
    except OSError:
        return _text(500, "Studio asset missing.", "text/plain")
    return _text(200, body, content_type)


def _session_secret() -> str:
    global _session_secret_cache
    if _session_secret_cache is None:
        _session_secret_cache = _ssm.get_parameter(
            Name=SESSION_PARAMETER,
            WithDecryption=True,
        )["Parameter"]["Value"]
    return _session_secret_cache


def _ghost_key() -> str:
    global _ghost_key_cache
    if _ghost_key_cache is None:
        _ghost_key_cache = _ssm.get_parameter(
            Name=GHOST_PARAMETER,
            WithDecryption=True,
        )["Parameter"]["Value"]
    return _ghost_key_cache


def _method(event: dict[str, Any]) -> str:
    return str(event.get("requestContext", {}).get("http", {}).get("method", ""))


def _cookies(event: dict[str, Any]) -> list[str]:
    if isinstance(event.get("cookies"), list):
        return [value for value in event["cookies"] if isinstance(value, str)]
    headers = event.get("headers") or {}
    value = headers.get("cookie") or headers.get("Cookie")
    return [value] if isinstance(value, str) else []


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
            signing_secret=_session_secret(),
        )
    except Exception:
        return None

    if not isinstance(claims, dict) or claims.get("sub") != OWNER_ID:
        return None
    return claims


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str):
        raise StudioError("request body must be JSON")

    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        except Exception:
            raise StudioError("request body encoding is invalid") from None

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise StudioError("request body is invalid JSON") from None

    if not isinstance(value, dict):
        raise StudioError("request body must be an object")
    return value


def _query_params(event: dict[str, Any]) -> dict[str, str]:
    value = event.get("queryStringParameters")
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _ghost_request(path: str, params: dict[str, str]) -> dict[str, Any]:
    query = {"key": _ghost_key(), **params}
    request = urllib.request.Request(
        f"{GHOST_BASE_URL}/ghost/api/content/{path}?{urllib.parse.urlencode(query)}",
        headers={
            "accept": "application/json",
            "user-agent": "pocket-tts-team-studio/1",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(20_000_001)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        raise StudioError("Ghost Content API request failed") from None

    if len(raw) > 20_000_000:
        raise StudioError("Ghost Content API response is unexpectedly large")

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StudioError("Ghost Content API returned invalid JSON") from None

    if not isinstance(value, dict):
        raise StudioError("Ghost Content API response shape is invalid")
    return value


def _catalog() -> list[dict[str, Any]]:
    global _catalog_cache

    now = time.time()
    if _catalog_cache is not None and now - _catalog_cache[0] < CATALOG_CACHE_SECONDS:
        return _catalog_cache[1]

    posts: list[dict[str, Any]] = []
    for page in range(1, 20):
        payload = _ghost_request(
            "posts/",
            {
                "formats": "html",
                "include": "authors",
                "limit": "100",
                "page": str(page),
                "order": "published_at desc",
            },
        )
        page_posts = payload.get("posts")
        if not isinstance(page_posts, list):
            raise StudioError("Ghost catalog response has no posts list")

        posts.extend(item for item in page_posts if isinstance(item, dict))

        pagination = payload.get("meta", {}).get("pagination", {})
        if not isinstance(pagination, dict) or pagination.get("next") is None:
            break

    _catalog_cache = (now, posts)
    return posts


def _post_by_id(post_id: str) -> dict[str, Any]:
    if _POST_ID_RE.fullmatch(post_id) is None:
        raise StudioError("post_id is invalid")

    payload = _ghost_request(
        f"posts/{urllib.parse.quote(post_id, safe='')}/",
        {
            "formats": "html",
            "include": "authors",
        },
    )
    posts = payload.get("posts")
    if not isinstance(posts, list) or len(posts) != 1 or not isinstance(posts[0], dict):
        raise StudioError("Ghost post was not found")
    return posts[0]


def _post_summary(post: dict[str, Any]) -> dict[str, Any]:
    authors = post.get("authors")
    author = None
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        author = authors[0].get("name")

    return {
        "id": post.get("id"),
        "title": post.get("title"),
        "slug": post.get("slug"),
        "url": post.get("url"),
        "excerpt": post.get("excerpt"),
        "published_at": post.get("published_at"),
        "updated_at": post.get("updated_at"),
        "primary_author": author,
    }


def _room_id(post_id: str) -> str:
    return "room_" + uuid.uuid5(uuid.NAMESPACE_URL, f"pocket-tts:ghost:{post_id}").hex


def _doc_id(post_id: str) -> str:
    return "doc_" + uuid.uuid5(uuid.NAMESPACE_OID, f"pocket-tts:ghost:{post_id}").hex


def _get_item(pk: str, sk: str) -> dict[str, Any] | None:
    item = _ddb.get_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": pk}, "sk": {"S": sk}},
        ConsistentRead=True,
    ).get("Item")
    return _python_item(item) if isinstance(item, dict) else None


def _query_room(room_id: str) -> list[dict[str, Any]]:
    response_items = []
    kwargs: dict[str, Any] = {
        "TableName": APP_TABLE,
        "KeyConditionExpression": "#pk = :pk",
        "ExpressionAttributeNames": {"#pk": "pk"},
        "ExpressionAttributeValues": {":pk": {"S": f"ROOM#{room_id}"}},
    }

    while True:
        response = _ddb.query(**kwargs)
        response_items.extend(_python_item(item) for item in response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if last is None:
            break
        kwargs["ExclusiveStartKey"] = last

    return response_items


def _scan_voices() -> list[dict[str, Any]]:
    items = []
    kwargs: dict[str, Any] = {"TableName": VOICE_TABLE}

    while True:
        response = _ddb.scan(**kwargs)
        items.extend(_python_item(item) for item in response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if last is None:
            break
        kwargs["ExclusiveStartKey"] = last

    items.sort(
        key=lambda item: (
            item.get("status") != "ACTIVE",
            str(item.get("display_name", "")).casefold(),
        )
    )
    return [
        {
            key: value
            for key, value in item.items()
            if key in {"voice_id", "display_name", "status", "version", "created_at", "updated_at"}
        }
        for item in items
    ]


def _service() -> tuple[StudioService, DynamoVoiceRepository]:
    studio_repository = DynamoStudioRepository(client=_ddb, table_name=APP_TABLE)
    voice_repository = DynamoVoiceRepository(client=_ddb, table_name=VOICE_TABLE)
    artifacts = StudioS3ArtifactStore(client=_s3, bucket_name=DEV_BUCKET)
    return (
        StudioService(
            artifacts=artifacts,
            studio_repository=studio_repository,
            voice_repository=voice_repository,
            bucket_name=DEV_BUCKET,
        ),
        voice_repository,
    )


def _canonical(post: dict[str, Any]) -> dict[str, Any]:
    post_id = post.get("id")
    raw_html = post.get("html")

    if not isinstance(post_id, str) or _POST_ID_RE.fullmatch(post_id) is None:
        raise StudioError("Ghost post identity is invalid")
    if not isinstance(raw_html, str) or not raw_html.strip():
        raise StudioError("Ghost post has no narration-relevant HTML")

    content_hash = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
    blocks = normalize_ghost_html(raw_html)
    if not blocks:
        raise StudioError("canonical normalizer produced no narration blocks")

    document = build_document(
        post_id=post_id,
        content_hash=content_hash,
        blocks=blocks,
    )

    if document.get("processor_version") != 4:
        raise StudioError("Studio requires Processor V4")

    return document


def _is_document_item(item: dict[str, Any]) -> bool:
    return (
        isinstance(item.get("doc_id"), str)
        and isinstance(item.get("source_narration_hash"), str)
        and not isinstance(item.get("generation_id"), str)
    )


def _is_generation_item(item: dict[str, Any]) -> bool:
    return isinstance(item.get("generation_id"), str)


def _latest_document(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    docs = [item for item in items if _is_document_item(item)]
    if not docs:
        return None
    return max(
        docs,
        key=lambda item: _document_revision_number(item, default=0),
    )


def _artifact(item: dict[str, Any], name: str) -> ArtifactRef:
    nested = item.get(name)
    if isinstance(nested, dict):
        bucket, key, sha = nested.get("bucket"), nested.get("key"), nested.get("sha256")
    else:
        bucket = item.get(f"{name}_bucket")
        key = item.get(f"{name}_key")
        sha = item.get(f"{name}_sha256")

        if (
            name == "document"
            and not all(
                isinstance(value, str) and value
                for value in (bucket, key, sha)
            )
        ):
            bucket = item.get("current_document_bucket")
            key = item.get("current_document_key")
            sha = item.get("current_document_sha256")

    if not all(isinstance(value, str) and value for value in (bucket, key, sha)):
        raise StudioError(f"{name} artifact metadata is incomplete")

    return ArtifactRef(bucket=bucket, key=key, sha256=sha)


def _document_revision_number(
    item: dict[str, Any],
    *,
    default: int | None = None,
) -> int:
    value = item.get("revision")

    if value is None:
        value = item.get("document_revision")

    if value is None:
        value = item.get("current_revision")

    if value is None:
        if default is not None:
            return default

        raise StudioError("document revision is missing")

    return int(value)


def _revision(item: dict[str, Any]) -> StudioDocumentRevision:
    return StudioDocumentRevision(
        room_id=str(item["room_id"]),
        doc_id=str(item["doc_id"]),
        revision=_document_revision_number(item),
        source_post_id=str(item["source_post_id"]),
        source_content_hash=str(item["source_content_hash"]),
        source_narration_hash=str(item["source_narration_hash"]),
        source_processor_version=int(item["source_processor_version"]),
        document=_artifact(item, "document"),
        created_at=str(item["created_at"]),
    )


def _ensure_room_document(post: dict[str, Any], document: dict[str, Any]) -> StudioDocumentRevision:
    post_id = str(post["id"])
    room_id = _room_id(post_id)
    doc_id = _doc_id(post_id)
    now = _now()
    service, _ = _service()

    room = _get_item(f"ROOM#{room_id}", "META")
    if room is None:
        service.create_room(
            RoomRecord(
                room_id=room_id,
                owner_id=OWNER_ID,
                title=str(post.get("title") or "Ghost narration"),
                status=RoomStatus.ACTIVE,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
    elif room.get("owner_id") != OWNER_ID or room.get("status") != "ACTIVE":
        raise StudioError("Studio room is not ACTIVE")

    items = _query_room(room_id)
    documents = [item for item in items if _is_document_item(item)]
    stable_documents = [
        item
        for item in documents
        if item.get("doc_id") == doc_id
    ]
    latest = _latest_document(stable_documents)

    if (
        latest is not None
        and latest.get("source_narration_hash") == document["narration_hash"]
        and int(latest.get("source_processor_version", 0)) == int(document["processor_version"])
    ):
        return _revision(latest)

    revision_number = (
        1
        if latest is None
        else _document_revision_number(latest, default=0) + 1
    )

    prepared = service.import_narration_document(
        room_id=room_id,
        doc_id=doc_id,
        revision=revision_number,
        narration_document=document,
        created_at=now,
    )

    if documents:
        for item in items:
            if not _is_generation_item(item):
                continue
            if item.get("review_status") in {"SELECTED", "READY"}:
                _ddb.update_item(
                    TableName=APP_TABLE,
                    Key={
                        "pk": {"S": f"ROOM#{room_id}"},
                        "sk": {"S": f"GEN#{item['generation_id']}"},
                    },
                    UpdateExpression="SET review_status = :outdated, updated_at = :now",
                    ExpressionAttributeValues={
                        ":outdated": {"S": "OUTDATED"},
                        ":now": {"S": now},
                    },
                )

    return prepared.revision


def _voice_name_map() -> dict[str, str]:
    return {
        str(voice.get("voice_id")): str(voice.get("display_name") or voice.get("voice_id"))
        for voice in _scan_voices()
        if isinstance(voice.get("voice_id"), str)
    }


def _generations(post_id: str) -> list[dict[str, Any]]:
    room_id = _room_id(post_id)
    if _get_item(f"ROOM#{room_id}", "META") is None:
        return []

    names = _voice_name_map()
    values = []

    for item in _query_room(room_id):
        if not _is_generation_item(item):
            continue

        if item.get("studio_origin") != "TEAM_STUDIO":
            continue

        values.append(
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "generation_id",
                    "voice_id",
                    "quote_mode",
                    "quote_voice_id",
                    "generation_status",
                    "review_status",
                    "created_at",
                    "updated_at",
                    "queued_at",
                    "started_at",
                    "completed_at",
                    "failed_at",
                    "source_content_hash",
                    "source_narration_hash",
                }
            }
        )
        values[-1]["tempo_percent"] = int(item.get("tempo_percent", 100) or 100)
        values[-1]["voice_name"] = names.get(str(item.get("voice_id")), str(item.get("voice_id", "")))

    values.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return values


def _post_detail(post_id: str) -> dict[str, Any]:
    post = _post_by_id(post_id)
    document = _canonical(post)
    return {
        "post": _post_summary(post),
        "document": document,
        "generations": _generations(post_id),
        "execution_enabled": EXECUTION_ENABLED,
    }



# ============================================================================
# Shared team Reference Audio Library
# Folder name is required. Alias/short_description/favorite are optional.
# Saved references only point at existing immutable voice IDs; no WAV is moved.
# ============================================================================
_REF_LIBRARY_PK = "REFLIB#TEAM"
_REF_FOLDER_ID_RE = re.compile(r"folder_[0-9a-f]{32}")
_REF_FOLDER_PATH = re.compile(r"^/studio-api/reference-folders/(folder_[0-9a-f]{32})$")
_REF_FOLDER_ARCHIVE_PATH = re.compile(r"^/studio-api/reference-folders/(folder_[0-9a-f]{32})/archive$")
_REF_FOLDER_REFERENCES_PATH = re.compile(r"^/studio-api/reference-folders/(folder_[0-9a-f]{32})/references$")
_REF_FOLDER_REFERENCE_PATH = re.compile(
    r"^/studio-api/reference-folders/(folder_[0-9a-f]{32})/references/(voice_[0-9a-f]{32})$"
)
_REF_FOLDER_REFERENCE_REMOVE_PATH = re.compile(
    r"^/studio-api/reference-folders/(folder_[0-9a-f]{32})/references/(voice_[0-9a-f]{32})/remove$"
)
_ref_deserializer = TypeDeserializer()


def _ref_decode(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: _ref_deserializer.deserialize(value) for key, value in raw.items()}


def _ref_query(pk: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: dict[str, Any] = {
            "TableName": APP_TABLE,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": pk}},
        }
        if start_key is not None:
            kwargs["ExclusiveStartKey"] = start_key
        response = _ddb.query(**kwargs)
        items.extend(_ref_decode(raw) for raw in response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if start_key is None:
            return items


def _ref_optional_text(value: Any, *, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StudioError(f"{label} must be text")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise StudioError(f"{label} must be {maximum} characters or fewer")
    return cleaned


def _ref_folder_name(value: Any) -> str:
    name = _ref_optional_text(value, label="folder_name", maximum=80)
    if name is None:
        raise StudioError("folder_name is required")
    return name


def _ref_folder_pk(folder_id: str) -> str:
    return f"REFFOLDER#{folder_id}"


def _ref_require_folder(folder_id: str) -> dict[str, Any]:
    if _REF_FOLDER_ID_RE.fullmatch(folder_id) is None:
        raise StudioError("folder_id is invalid")
    response = _ddb.get_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _REF_LIBRARY_PK}, "sk": {"S": f"FOLDER#{folder_id}"}},
        ConsistentRead=True,
    )
    raw = response.get("Item")
    if raw is None:
        raise StudioError("reference folder was not found")
    folder = _ref_decode(raw)
    if folder.get("status") != "ACTIVE":
        raise StudioError("reference folder is not ACTIVE")
    return folder


def _ref_active_entries(folder_id: str) -> list[dict[str, Any]]:
    return [
        item for item in _ref_query(_ref_folder_pk(folder_id))
        if item.get("entity_type") == "reference_audio" and item.get("status") == "ACTIVE"
    ]


def _ref_assert_unique_folder_name(name: str, *, ignore_folder_id: str | None = None) -> None:
    wanted = name.casefold()
    for item in _ref_query(_REF_LIBRARY_PK):
        if item.get("entity_type") != "reference_folder" or item.get("status") != "ACTIVE":
            continue
        if ignore_folder_id == item.get("folder_id"):
            continue
        existing = str(item.get("folder_name") or "").strip()
        if existing.casefold() == wanted:
            raise StudioError(f'a reference folder named "{name}" already exists')


def _ref_folder_public(folder: dict[str, Any], *, reference_count: int) -> dict[str, Any]:
    return {
        "folder_id": str(folder["folder_id"]),
        "folder_name": str(folder["folder_name"]),
        "reference_count": reference_count,
        "created_at": folder.get("created_at"),
        "updated_at": folder.get("updated_at"),
    }


def _reference_folders() -> dict[str, Any]:
    folders = [
        item for item in _ref_query(_REF_LIBRARY_PK)
        if item.get("entity_type") == "reference_folder" and item.get("status") == "ACTIVE"
    ]
    folders.sort(key=lambda item: str(item.get("folder_name") or "").casefold())
    return {
        "items": [
            _ref_folder_public(folder, reference_count=len(_ref_active_entries(str(folder["folder_id"]))))
            for folder in folders
        ]
    }


def _create_reference_folder(body: dict[str, Any]) -> dict[str, Any]:
    name = _ref_folder_name(body.get("folder_name"))
    _ref_assert_unique_folder_name(name)
    folder_id = "folder_" + uuid.uuid4().hex
    now = _now()
    _ddb.put_item(
        TableName=APP_TABLE,
        Item={
            "pk": {"S": _REF_LIBRARY_PK},
            "sk": {"S": f"FOLDER#{folder_id}"},
            "entity_type": {"S": "reference_folder"},
            "folder_id": {"S": folder_id},
            "folder_name": {"S": name},
            "status": {"S": "ACTIVE"},
            "created_at": {"S": now},
            "updated_at": {"S": now},
        },
        ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
    )
    return {
        "folder_id": folder_id,
        "folder_name": name,
        "reference_count": 0,
        "created_at": now,
        "updated_at": now,
    }


def _reference_folder_detail(folder_id: str) -> dict[str, Any]:
    folder = _ref_require_folder(folder_id)
    entries = _ref_active_entries(folder_id)
    _, voice_repository = _service()
    references: list[dict[str, Any]] = []
    for item in entries:
        voice_id = str(item["voice_id"])
        voice = voice_repository.get_voice(voice_id)
        references.append({
            "voice_id": voice_id,
            "voice_display_name": (voice.display_name if voice is not None else voice_id),
            "voice_status": (voice.status.value if voice is not None else "MISSING"),
            "alias": item.get("alias"),
            "short_description": item.get("short_description"),
            "favorite": bool(item.get("favorite", False)),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        })
    references.sort(key=lambda item: str(item.get("alias") or item.get("voice_display_name") or item["voice_id"]).casefold())
    return {
        "folder": _ref_folder_public(folder, reference_count=len(references)),
        "references": references,
    }


def _rename_reference_folder(folder_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _ref_require_folder(folder_id)
    name = _ref_folder_name(body.get("folder_name"))
    _ref_assert_unique_folder_name(name, ignore_folder_id=folder_id)
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _REF_LIBRARY_PK}, "sk": {"S": f"FOLDER#{folder_id}"}},
        ConditionExpression="#status = :active",
        UpdateExpression="SET folder_name = :name, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":active": {"S": "ACTIVE"}, ":name": {"S": name}, ":now": {"S": _now()}},
    )
    return _reference_folder_detail(folder_id)


def _archive_reference_folder(folder_id: str) -> dict[str, Any]:
    folder = _ref_require_folder(folder_id)
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _REF_LIBRARY_PK}, "sk": {"S": f"FOLDER#{folder_id}"}},
        ConditionExpression="#status = :active",
        UpdateExpression="SET #status = :archived, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":active": {"S": "ACTIVE"}, ":archived": {"S": "ARCHIVED"}, ":now": {"S": _now()}},
    )
    return {"folder_id": folder_id, "folder_name": folder.get("folder_name"), "archived": True}


def _add_reference_voices(folder_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _ref_require_folder(folder_id)
    voice_ids = body.get("voice_ids")
    if not isinstance(voice_ids, list) or not voice_ids:
        raise StudioError("voice_ids must contain at least one voice")
    if len(voice_ids) > 100:
        raise StudioError("voice_ids cannot contain more than 100 voices")
    normalized: list[str] = []
    for value in voice_ids:
        if not isinstance(value, str) or _VOICE_ID_RE.fullmatch(value) is None:
            raise StudioError("voice_ids contains an invalid voice_id")
        if value not in normalized:
            normalized.append(value)
    _, voice_repository = _service()
    for voice_id in normalized:
        voice = voice_repository.get_voice(voice_id)
        if voice is None:
            raise StudioError(f"voice {voice_id} was not found")
        if voice.status is not VoiceStatus.ACTIVE:
            raise StudioError(f"voice {voice_id} is not ACTIVE")
    now = _now()
    for voice_id in normalized:
        _ddb.update_item(
            TableName=APP_TABLE,
            Key={"pk": {"S": _ref_folder_pk(folder_id)}, "sk": {"S": f"VOICE#{voice_id}"}},
            UpdateExpression=(
                "SET entity_type = :entity, folder_id = :folder_id, voice_id = :voice_id, "
                "#status = :active, created_at = if_not_exists(created_at, :now), updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":entity": {"S": "reference_audio"}, ":folder_id": {"S": folder_id},
                ":voice_id": {"S": voice_id}, ":active": {"S": "ACTIVE"}, ":now": {"S": now},
            },
        )
    return _reference_folder_detail(folder_id)


def _ref_require_reference(folder_id: str, voice_id: str) -> dict[str, Any]:
    _ref_require_folder(folder_id)
    if _VOICE_ID_RE.fullmatch(voice_id) is None:
        raise StudioError("voice_id is invalid")
    response = _ddb.get_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _ref_folder_pk(folder_id)}, "sk": {"S": f"VOICE#{voice_id}"}},
        ConsistentRead=True,
    )
    raw = response.get("Item")
    if raw is None:
        raise StudioError("saved reference was not found")
    item = _ref_decode(raw)
    if item.get("status") != "ACTIVE":
        raise StudioError("saved reference is not ACTIVE")
    return item


def _update_reference_metadata(folder_id: str, voice_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _ref_require_reference(folder_id, voice_id)
    allowed = {"alias", "short_description", "favorite"}
    unknown = set(body) - allowed
    if unknown:
        raise StudioError("unsupported reference metadata: " + ", ".join(sorted(unknown)))
    if not body:
        raise StudioError("reference metadata update is empty")
    sets = ["updated_at = :now"]
    removes: list[str] = []
    names = {"#status": "status"}
    values: dict[str, dict[str, Any]] = {":active": {"S": "ACTIVE"}, ":now": {"S": _now()}}
    if "alias" in body:
        alias = _ref_optional_text(body.get("alias"), label="alias", maximum=80)
        names["#alias"] = "alias"
        if alias is None:
            removes.append("#alias")
        else:
            sets.append("#alias = :alias")
            values[":alias"] = {"S": alias}
    if "short_description" in body:
        desc = _ref_optional_text(body.get("short_description"), label="short_description", maximum=280)
        names["#description"] = "short_description"
        if desc is None:
            removes.append("#description")
        else:
            sets.append("#description = :description")
            values[":description"] = {"S": desc}
    if "favorite" in body:
        favorite = body.get("favorite")
        if not isinstance(favorite, bool):
            raise StudioError("favorite must be true or false")
        sets.append("favorite = :favorite")
        values[":favorite"] = {"BOOL": favorite}
    expression = "SET " + ", ".join(sets)
    if removes:
        expression += " REMOVE " + ", ".join(removes)
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _ref_folder_pk(folder_id)}, "sk": {"S": f"VOICE#{voice_id}"}},
        ConditionExpression="#status = :active",
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    return _reference_folder_detail(folder_id)


def _remove_reference_voice(folder_id: str, voice_id: str) -> dict[str, Any]:
    _ref_require_reference(folder_id, voice_id)
    _ddb.update_item(
        TableName=APP_TABLE,
        Key={"pk": {"S": _ref_folder_pk(folder_id)}, "sk": {"S": f"VOICE#{voice_id}"}},
        ConditionExpression="#status = :active",
        UpdateExpression="SET #status = :removed, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":active": {"S": "ACTIVE"}, ":removed": {"S": "REMOVED"}, ":now": {"S": _now()}},
    )
    return {"folder_id": folder_id, "voice_id": voice_id, "removed": True}


def _create_generation(post_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if not EXECUTION_ENABLED:
        raise StudioError("generation execution is currently paused")

    voice_id = body.get("voice_id")
    quote_mode = body.get("quote_mode")
    quote_voice_id = body.get("quote_voice_id")
    tempo_percent = body.get("tempo_percent", 100)
    if (
        type(tempo_percent) is not int
        or tempo_percent not in {80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100}
    ):
        raise StudioError(
            "tempo_percent must be one of: 80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100"
        )

    if not isinstance(voice_id, str) or _VOICE_ID_RE.fullmatch(voice_id) is None:
        raise StudioError("voice_id is invalid")
    if quote_mode not in {"preserve", "exclude", "two_voice"}:
        raise StudioError("quote_mode is invalid")
    if quote_mode == "two_voice":
        if not isinstance(quote_voice_id, str) or _VOICE_ID_RE.fullmatch(quote_voice_id) is None:
            raise StudioError("two_voice requires quote_voice_id")
    elif quote_voice_id is not None:
        raise StudioError("quote_voice_id is forbidden for this quote_mode")

    post = _post_by_id(post_id)
    document = _canonical(post)
    revision = _ensure_room_document(post, document)

    service, voice_repository = _service()
    voice = voice_repository.get_voice(voice_id)
    if voice is None or voice.status is not VoiceStatus.ACTIVE:
        raise StudioError("narrator voice is not ACTIVE")

    quote_voice = None
    if quote_voice_id is not None:
        quote_voice = voice_repository.get_voice(quote_voice_id)
        if quote_voice is None or quote_voice.status is not VoiceStatus.ACTIVE:
            raise StudioError("quote voice is not ACTIVE")

    generation_id = "gen_" + uuid.uuid4().hex
    prepared = service.create_generation(
        room_id=revision.room_id,
        generation_id=generation_id,
        revision=revision,
        voice=voice,
        quote_mode=quote_mode,
        quote_voice=quote_voice,
        created_at=_now(),
        tempo_percent=tempo_percent,
    )

    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {"S": f"ROOM#{revision.room_id}"},
            "sk": {"S": f"GEN#{generation_id}"},
        },
        UpdateExpression="SET studio_origin = :origin",
        ExpressionAttributeValues={
            ":origin": {"S": "TEAM_STUDIO"},
        },
    )

    return {
        "room_id": revision.room_id,
        "generation_id": prepared.generation.generation_id,
        "review_status": prepared.generation.review_status.value,
        "generation_status": None,
        "tempo_percent": prepared.generation.tempo_percent,
        "enqueue_path": (
            f"/rooms/{revision.room_id}/generations/"
            f"{prepared.generation.generation_id}/enqueue"
        ),
    }


def _generation_item(post_id: str, generation_id: str) -> tuple[str, dict[str, Any]]:
    if _GENERATION_ID_RE.fullmatch(generation_id) is None:
        raise StudioError("generation_id is invalid")

    room_id = _room_id(post_id)
    item = _get_item(f"ROOM#{room_id}", f"GEN#{generation_id}")
    if (
        item is None
        or item.get("generation_id") != generation_id
        or item.get("room_id") != room_id
    ):
        raise StudioError("generation was not found")
    return room_id, item


def _review(post_id: str, generation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    target = body.get("review_status")
    if target not in {"SELECTED", "READY", "OUTDATED"}:
        raise StudioError("review_status is invalid")

    room_id, item = _generation_item(post_id, generation_id)

    if target in {"SELECTED", "READY"} and item.get("generation_status") != "COMPLETED":
        raise StudioError("generation must be COMPLETED")

    if target == "READY" and item.get("review_status") != GenerationReviewStatus.SELECTED.value:
        raise StudioError("generation must be SELECTED before READY")

    now = _now()

    if target == "SELECTED":
        for other in _query_room(room_id):
            if (
                _is_generation_item(other)
                and other.get("generation_id") != generation_id
                and other.get("review_status") in {"SELECTED", "READY"}
            ):
                _ddb.update_item(
                    TableName=APP_TABLE,
                    Key={
                        "pk": {"S": f"ROOM#{room_id}"},
                        "sk": {"S": f"GEN#{other['generation_id']}"},
                    },
                    UpdateExpression="SET review_status = :outdated, updated_at = :now",
                    ExpressionAttributeValues={
                        ":outdated": {"S": "OUTDATED"},
                        ":now": {"S": now},
                    },
                )

    _ddb.update_item(
        TableName=APP_TABLE,
        Key={
            "pk": {"S": f"ROOM#{room_id}"},
            "sk": {"S": f"GEN#{generation_id}"},
        },
        UpdateExpression="SET review_status = :review, updated_at = :now",
        ExpressionAttributeValues={
            ":review": {"S": target},
            ":now": {"S": now},
        },
    )

    return {"generation_id": generation_id, "review_status": target}


def _audio(post_id: str, generation_id: str) -> dict[str, Any]:
    _, item = _generation_item(post_id, generation_id)
    if item.get("generation_status") != "COMPLETED":
        raise StudioError("generation is not COMPLETED")

    key = worker_output_key(generation_id)
    try:
        _s3.head_object(Bucket=DEV_BUCKET, Key=key)
    except ClientError:
        raise StudioError("generation audio is unavailable") from None

    return {
        "url": _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": DEV_BUCKET, "Key": key},
            ExpiresIn=900,
        ),
        "expires_in_seconds": 900,
    }


def _create_voice(body: dict[str, Any]) -> dict[str, Any]:
    display_name = body.get("display_name")
    encoded = body.get("wav_base64")

    if not isinstance(display_name, str) or not display_name.strip() or len(display_name.strip()) > 160:
        raise StudioError("display_name must be 1..160 characters")
    if not isinstance(encoded, str):
        raise StudioError("wav_base64 is required")

    try:
        wav_bytes = base64.b64decode(encoded, validate=True)
    except Exception:
        raise StudioError("wav_base64 is invalid") from None

    if len(wav_bytes) > MAX_REFERENCE_BYTES:
        raise StudioError("reference WAV exceeds 10 MB")
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise StudioError("reference must be RIFF/WAVE")

    service, _ = _service()
    voice_id = "voice_" + uuid.uuid4().hex
    now = _now()

    voice = service.register_voice(
        voice_id=voice_id,
        display_name=display_name.strip(),
        version=1,
        wav_bytes=wav_bytes,
        status=VoiceStatus.ACTIVE,
        created_at=now,
    )

    WorkerVoiceS3Store(client=_s3, bucket_name=DEV_BUCKET).put_immutable(
        prepare_worker_voice_reference(voice=voice, wav_bytes=wav_bytes)
    )

    return {
        "voice_id": voice.voice_id,
        "display_name": voice.display_name,
        "status": voice.status.value,
        "version": voice.version,
        "created_at": now,
    }


def _archive_voice(voice_id: str) -> dict[str, Any]:
    if _VOICE_ID_RE.fullmatch(voice_id) is None:
        raise StudioError("voice_id is invalid")

    _, repo = _service()
    voice = repo.get_voice(voice_id)

    if voice is None:
        raise StudioError("voice was not found")

    if voice.status is VoiceStatus.DISABLED:
        return {
            "voice_id": voice.voice_id,
            "display_name": voice.display_name,
            "status": VoiceStatus.DISABLED.value,
            "archived": True,
            "already_archived": True,
        }

    if voice.status is not VoiceStatus.ACTIVE:
        raise StudioError("voice is not ACTIVE")

    now = _now()

    try:
        _ddb.update_item(
            TableName=VOICE_TABLE,
            Key={
                "voice_id": {"S": voice_id},
            },
            ConditionExpression="#status = :active",
            UpdateExpression="SET #status = :disabled, updated_at = :now",
            ExpressionAttributeNames={
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":active": {"S": VoiceStatus.ACTIVE.value},
                ":disabled": {"S": VoiceStatus.DISABLED.value},
                ":now": {"S": now},
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise StudioError("voice status changed; refresh and retry") from None
        raise

    return {
        "voice_id": voice.voice_id,
        "display_name": voice.display_name,
        "status": VoiceStatus.DISABLED.value,
        "archived": True,
        "already_archived": False,
    }


def _voice_audio(voice_id: str) -> dict[str, Any]:
    if _VOICE_ID_RE.fullmatch(voice_id) is None:
        raise StudioError("voice_id is invalid")

    _, repo = _service()
    voice = repo.get_voice(voice_id)
    if voice is None:
        raise StudioError("voice was not found")

    reference = voice.reference_audio
    if reference.bucket != DEV_BUCKET or not reference.key.startswith("studio-voices/"):
        raise StudioError("voice reference is outside Studio DEV storage")

    return {
        "url": _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": reference.bucket, "Key": reference.key},
            ExpiresIn=900,
        ),
        "expires_in_seconds": 900,
    }


def _posts(event: dict[str, Any]) -> dict[str, Any]:
    params = _query_params(event)
    try:
        page = max(1, int(params.get("page", "1")))
        limit = min(48, max(1, int(params.get("limit", "24"))))
    except ValueError:
        raise StudioError("page/limit must be integers") from None

    search = params.get("search", "").strip().casefold()
    posts = _catalog()

    if search:
        posts = [
            post
            for post in posts
            if search in str(post.get("title", "")).casefold()
            or search in str(post.get("excerpt", "")).casefold()
            or search in str(post.get("slug", "")).casefold()
        ]

    total = len(posts)
    pages = max(1, math.ceil(total / limit))
    page = min(page, pages)
    start = (page - 1) * limit
    selected = posts[start : start + limit]

    items = []
    for post in selected:
        summary = _post_summary(post)
        post_id = summary.get("id")
        state = "Not generated"

        if isinstance(post_id, str):
            room_id = _room_id(post_id)
            room = _get_item(f"ROOM#{room_id}", "META")
            if room is not None:
                generations = [
                    item for item in _query_room(room_id) if _is_generation_item(item)
                ]
                if generations:
                    latest = max(generations, key=lambda item: str(item.get("created_at", "")))
                    state = (
                        latest.get("review_status")
                        if latest.get("review_status") in {"READY", "SELECTED", "OUTDATED"}
                        else latest.get("generation_status")
                        or "Prepared"
                    )
                else:
                    state = "Prepared"

        summary["studio_state"] = state
        items.append(summary)

    return {
        "items": items,
        "page": page,
        "pages": pages,
        "total": total,
        "execution_enabled": EXECUTION_ENABLED,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    path = event.get("rawPath")
    method = _method(event)

    if not isinstance(path, str):
        return _json(404, {"error": "not_found"})

    if method == "GET" and path in {"/studio", "/studio/"}:
        return _static("index.html", "text/html")
    if method == "GET" and path == "/studio/styles.css":
        return _static("styles.css", "text/css")
    if method == "GET" and path == "/studio/app.js":
        return _static("app.js", "text/javascript")
    if method == "GET" and path == "/studio/reference-library.js":
        return _static("reference-library.js", "text/javascript")

    if not path.startswith("/studio-api/"):
        return _json(404, {"error": "not_found"})

    if _session(event) is None:
        return _json(401, {"error": "unauthorized"})

    try:
        if method == "GET" and path == "/studio-api/runtime":
            return _json(
                200,
                {
                    "execution_enabled": EXECUTION_ENABLED,
                },
            )

        if method == "GET" and path == "/studio-api/posts":
            return _json(200, _posts(event))

        if method == "GET" and path == "/studio-api/voices":
            return _json(200, {"items": _scan_voices()})

        if method == "POST" and path == "/studio-api/voices":
            return _json(201, _create_voice(_body(event)))

        if method == "GET" and path == "/studio-api/reference-folders":
            return _json(200, _reference_folders())

        if method == "POST" and path == "/studio-api/reference-folders":
            return _json(201, _create_reference_folder(_body(event)))

        match = _REF_FOLDER_ARCHIVE_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(200, _archive_reference_folder(match.group(1)))

        match = _REF_FOLDER_REFERENCES_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(200, _add_reference_voices(match.group(1), _body(event)))

        match = _REF_FOLDER_REFERENCE_REMOVE_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(200, _remove_reference_voice(match.group(1), match.group(2)))

        match = _REF_FOLDER_REFERENCE_PATH.fullmatch(path)
        if method == "PATCH" and match is not None:
            return _json(200, _update_reference_metadata(match.group(1), match.group(2), _body(event)))

        match = _REF_FOLDER_PATH.fullmatch(path)
        if method == "GET" and match is not None:
            return _json(200, _reference_folder_detail(match.group(1)))
        if method == "PATCH" and match is not None:
            return _json(200, _rename_reference_folder(match.group(1), _body(event)))

        match = _VOICE_ARCHIVE_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(200, _archive_voice(match.group(1)))

        match = _VOICE_AUDIO_PATH.fullmatch(path)
        if method == "GET" and match is not None:
            return _json(200, _voice_audio(match.group(1)))

        match = _POST_PATH.fullmatch(path)
        if method == "GET" and match is not None:
            return _json(200, _post_detail(match.group(1)))

        match = _GENERATIONS_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(201, _create_generation(match.group(1), _body(event)))

        match = _REVIEW_PATH.fullmatch(path)
        if method == "POST" and match is not None:
            return _json(200, _review(match.group(1), match.group(2), _body(event)))

        match = _AUDIO_PATH.fullmatch(path)
        if method == "GET" and match is not None:
            return _json(200, _audio(match.group(1), match.group(2)))

        return _json(404, {"error": "not_found"})

    except StudioError as exc:
        return _json(409, {"error": "studio_conflict", "message": str(exc)})
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "team_studio_internal_error",
                    "method": method,
                    "path": path,
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        traceback.print_exc()
        return _json(500, {"error": "internal_error"})
