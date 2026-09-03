"""Pure Content Sync V1 models and deterministic key helpers.

This module deliberately contains no AWS SDK calls, no HTTP calls, and no
runtime environment access. It represents the frozen Content Sync V1
contract in testable Python types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from .hashing import compute_content_hash

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SYNC_ID_RE = re.compile(r"^sync_[0-9a-f]{32}$")


class ContentSyncContractError(ValueError):
    """Raised when a value violates the frozen Content Sync V1 contract."""


class UnsupportedGhostAccessError(ContentSyncContractError):
    """Raised when a Ghost post is not narratable under V1 access rules."""

    error_code = "UNSUPPORTED_GHOST_ACCESS"


class SyncStatus(str, Enum):
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


class CatalogStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    NOT_IN_PUBLISHED_CATALOG = "NOT_IN_PUBLISHED_CATALOG"


class SyncErrorCode(str, Enum):
    UNSUPPORTED_GHOST_ACCESS = "UNSUPPORTED_GHOST_ACCESS"
    CATALOG_CHANGED_DURING_SYNC = "CATALOG_CHANGED_DURING_SYNC"


def _require_nonblank(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContentSyncContractError(
            f"{name} must be a non-empty string"
        )


def _require_optional_string(name: str, value: Optional[str]) -> None:
    if value is None:
        return

    if not isinstance(value, str) or not value.strip():
        raise ContentSyncContractError(
            f"{name} must be None or a non-empty string"
        )


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise ContentSyncContractError(
            f"{name} must be lowercase 64-character SHA-256 hex"
        )


def _require_sync_id(value: str) -> None:
    if not isinstance(value, str) or not _SYNC_ID_RE.fullmatch(value):
        raise ContentSyncContractError(
            "sync_id must use sync_<uuid4hex>"
        )


def _require_processor_version(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > 999999
    ):
        raise ContentSyncContractError(
            "processor_version must be an integer from 1 through 999999"
        )


def _require_utc_rfc3339_z(name: str, value: str) -> None:
    _require_nonblank(name, value)

    if not value.endswith("Z"):
        raise ContentSyncContractError(
            f"{name} must be a UTC RFC 3339 timestamp ending in Z"
        )

    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00"
        )
    except ValueError as exc:
        raise ContentSyncContractError(
            f"{name} must be a valid RFC 3339 timestamp"
        ) from exc

    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ContentSyncContractError(
            f"{name} must represent UTC"
        )


def new_sync_id() -> str:
    """Return a new logical full-catalog synchronization identifier."""

    return f"sync_{uuid4().hex}"


def ghost_html_key(post_id: str, content_hash: str) -> str:
    """Return the immutable DEV S3 key for exact Ghost HTML."""

    _require_nonblank("post_id", post_id)
    _require_sha256("content_hash", content_hash)

    return f"ghost/{post_id}/{content_hash}.html"


def narration_document_key(
    post_id: str,
    content_hash: str,
    processor_version: int,
    narration_hash: str,
) -> str:
    """Return the immutable DEV S3 key for Narration Document V1."""

    _require_nonblank("post_id", post_id)
    _require_sha256("content_hash", content_hash)
    _require_processor_version(processor_version)
    _require_sha256("narration_hash", narration_hash)

    return (
        "narration-documents/"
        f"{post_id}/{content_hash}/"
        f"p{processor_version:06d}/{narration_hash}.json"
    )


@dataclass(frozen=True)
class CatalogAuthor:
    """Minimal narration/catalog author identity retained from Ghost."""

    name: str
    id: Optional[str] = None
    slug: Optional[str] = None

    def __post_init__(self) -> None:
        _require_nonblank("author.name", self.name)
        _require_optional_string("author.id", self.id)
        _require_optional_string("author.slug", self.slug)


@dataclass(frozen=True)
class CatalogTag:
    """Minimal narration/catalog tag identity retained from Ghost."""

    name: str
    id: Optional[str] = None
    slug: Optional[str] = None

    def __post_init__(self) -> None:
        _require_nonblank("tag.name", self.name)
        _require_optional_string("tag.id", self.id)
        _require_optional_string("tag.slug", self.slug)


@dataclass(frozen=True)
class GhostCatalogPost:
    """Validated Ghost Content API post before durable processing.

    This type can represent unsupported access states so the orchestration
    layer can fail the full synchronization with the documented V1 error
    instead of silently dropping protected content.
    """

    post_id: str
    title: str
    slug: str
    url: str
    published_at: str
    updated_at: str
    html: str
    visibility: str
    access: bool

    excerpt: Optional[str] = None
    feature_image: Optional[str] = None
    authors: tuple[CatalogAuthor, ...] = field(default_factory=tuple)
    tags: tuple[CatalogTag, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_nonblank("post_id", self.post_id)
        _require_nonblank("title", self.title)
        _require_nonblank("slug", self.slug)
        _require_nonblank("url", self.url)
        _require_utc_rfc3339_z(
            "published_at",
            self.published_at,
        )
        _require_utc_rfc3339_z(
            "updated_at",
            self.updated_at,
        )
        _require_nonblank("html", self.html)
        _require_nonblank("visibility", self.visibility)

        if not isinstance(self.access, bool):
            raise ContentSyncContractError(
                "access must be a boolean"
            )

        _require_optional_string("excerpt", self.excerpt)
        _require_optional_string(
            "feature_image",
            self.feature_image,
        )

        if not isinstance(self.authors, tuple):
            raise ContentSyncContractError(
                "authors must be a tuple"
            )

        if not isinstance(self.tags, tuple):
            raise ContentSyncContractError(
                "tags must be a tuple"
            )

        for author in self.authors:
            if not isinstance(author, CatalogAuthor):
                raise ContentSyncContractError(
                    "authors must contain CatalogAuthor values"
                )

        for tag in self.tags:
            if not isinstance(tag, CatalogTag):
                raise ContentSyncContractError(
                    "tags must contain CatalogTag values"
                )

    @property
    def catalog_content_hash(self) -> str:
        """SHA-256 of the exact UTF-8 Ghost HTML."""

        return compute_content_hash(self.html)

    @property
    def html_s3_key(self) -> str:
        return ghost_html_key(
            self.post_id,
            self.catalog_content_hash,
        )

    def document_s3_key(
        self,
        narration_hash: str,
        *,
        processor_version: int,
    ) -> str:
        return narration_document_key(
            self.post_id,
            self.catalog_content_hash,
            processor_version,
            narration_hash,
        )

    def require_v1_access(self) -> None:
        """Fail closed for content outside the verified V1 access class."""

        if self.visibility != "public" or self.access is not True:
            raise UnsupportedGhostAccessError(
                "Ghost post is outside the supported V1 access class"
            )


@dataclass(frozen=True)
class SyncState:
    """Pure representation of SYSTEM#GHOST_SYNC / CURRENT."""

    sync_id: str
    status: SyncStatus
    next_page: Optional[int]
    started_at: str
    updated_at: str

    expected_total: Optional[int] = None
    expected_pages: Optional[int] = None
    completed_at: Optional[str] = None
    last_error_code: Optional[str] = None

    def __post_init__(self) -> None:
        _require_sync_id(self.sync_id)
        _require_utc_rfc3339_z(
            "started_at",
            self.started_at,
        )
        _require_utc_rfc3339_z(
            "updated_at",
            self.updated_at,
        )

        if not isinstance(self.status, SyncStatus):
            raise ContentSyncContractError(
                "status must be a SyncStatus"
            )

        if (self.expected_total is None) != (
            self.expected_pages is None
        ):
            raise ContentSyncContractError(
                "expected_total and expected_pages must be set together"
            )

        if self.expected_total is not None:
            if (
                isinstance(self.expected_total, bool)
                or not isinstance(self.expected_total, int)
                or self.expected_total < 0
            ):
                raise ContentSyncContractError(
                    "expected_total must be a non-negative integer"
                )

            if (
                isinstance(self.expected_pages, bool)
                or not isinstance(self.expected_pages, int)
                or self.expected_pages < 1
            ):
                raise ContentSyncContractError(
                    "expected_pages must be a positive integer"
                )

        if self.status in {
            SyncStatus.RUNNING,
            SyncStatus.FAILED,
        }:
            if (
                isinstance(self.next_page, bool)
                or not isinstance(self.next_page, int)
                or self.next_page < 1
            ):
                raise ContentSyncContractError(
                    "RUNNING/FAILED next_page must be a positive integer"
                )

            if self.completed_at is not None:
                raise ContentSyncContractError(
                    "RUNNING/FAILED state cannot have completed_at"
                )

        if self.status is SyncStatus.COMPLETE:
            if self.next_page is not None:
                raise ContentSyncContractError(
                    "COMPLETE state must not contain next_page"
                )

            if self.completed_at is None:
                raise ContentSyncContractError(
                    "COMPLETE state requires completed_at"
                )

            _require_utc_rfc3339_z(
                "completed_at",
                self.completed_at,
            )

        if (
            self.expected_pages is not None
            and self.next_page is not None
            and self.next_page > self.expected_pages + 1
        ):
            raise ContentSyncContractError(
                "next_page cannot exceed expected_pages + 1"
            )

        if self.status is SyncStatus.FAILED:
            _require_nonblank(
                "last_error_code",
                self.last_error_code,
            )

        if (
            self.status is not SyncStatus.FAILED
            and self.last_error_code is not None
        ):
            raise ContentSyncContractError(
                "only FAILED state may contain last_error_code"
            )

    @property
    def verification_pending(self) -> bool:
        """True when first pass is durable and verification is required."""

        return (
            self.status in {
                SyncStatus.RUNNING,
                SyncStatus.FAILED,
            }
            and self.expected_pages is not None
            and self.next_page == self.expected_pages + 1
        )


def new_running_sync_state(
    now: str,
    *,
    sync_id: Optional[str] = None,
) -> SyncState:
    """Create the first CURRENT state for a real synchronization run."""

    _require_utc_rfc3339_z("now", now)

    return SyncState(
        sync_id=sync_id or new_sync_id(),
        status=SyncStatus.RUNNING,
        next_page=1,
        started_at=now,
        updated_at=now,
    )