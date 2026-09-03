"""Pure Ghost Content API page client for Content Sync V1.

This module performs request construction and strict response parsing only.

It deliberately has no concrete HTTP implementation. Runtime code supplies
a transport object implementing ``get_json(url)``. Unit tests therefore run
without making network requests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from .sync_models import (
    CatalogAuthor,
    CatalogTag,
    ContentSyncContractError,
    GhostCatalogPost,
)

GHOST_PAGE_SIZE = 100
GHOST_INCLUDE = "authors,tags"
GHOST_FORMATS = "html,plaintext"


class GhostContentClientError(ValueError):
    """Base error for Ghost request/response contract violations."""


class GhostContentResponseError(GhostContentClientError):
    """Raised when Ghost returns a structurally invalid Content API page."""


class GhostJsonTransport(Protocol):
    """Dependency boundary implemented by runtime HTTP code later."""

    def get_json(self, url: str) -> Mapping[str, Any]:
        """Return decoded JSON for one GET request."""


@dataclass(frozen=True)
class GhostPagination:
    """Validated Ghost Content API pagination metadata."""

    page: int
    limit: int
    pages: int
    total: int
    next: Optional[int]
    prev: Optional[int]

    def __post_init__(self) -> None:
        _require_int("pagination.page", self.page, minimum=1)
        _require_int("pagination.limit", self.limit, minimum=1)
        _require_int("pagination.pages", self.pages, minimum=1)
        _require_int("pagination.total", self.total, minimum=0)

        if self.page > self.pages:
            raise GhostContentResponseError(
                "pagination.page cannot exceed pagination.pages"
            )

        _require_optional_int(
            "pagination.next",
            self.next,
            minimum=1,
        )
        _require_optional_int(
            "pagination.prev",
            self.prev,
            minimum=1,
        )

        if self.next is not None and self.next > self.pages:
            raise GhostContentResponseError(
                "pagination.next cannot exceed pagination.pages"
            )

        if self.prev is not None and self.prev >= self.page:
            raise GhostContentResponseError(
                "pagination.prev must precede pagination.page"
            )


@dataclass(frozen=True)
class GhostCatalogPage:
    """One validated Content API page."""

    posts: tuple[GhostCatalogPost, ...]
    pagination: GhostPagination

    def __post_init__(self) -> None:
        if not isinstance(self.posts, tuple):
            raise GhostContentResponseError(
                "posts must be a tuple"
            )

        for post in self.posts:
            if not isinstance(post, GhostCatalogPost):
                raise GhostContentResponseError(
                    "posts must contain GhostCatalogPost values"
                )

        if len(self.posts) > self.pagination.limit:
            raise GhostContentResponseError(
                "page contains more posts than pagination.limit"
            )


class GhostContentClient:
    """Request builder and response parser for Ghost Content API V1 sync."""

    def __init__(
        self,
        *,
        base_url: str,
        content_api_key: str,
        transport: GhostJsonTransport,
    ) -> None:
        self._base_url = _normalize_base_url(base_url)

        if not isinstance(content_api_key, str) or not content_api_key.strip():
            raise GhostContentClientError(
                "content_api_key must be a non-empty string"
            )

        if transport is None:
            raise GhostContentClientError(
                "transport is required"
            )

        self._content_api_key = content_api_key
        self._transport = transport

    def build_posts_page_url(self, page: int) -> str:
        """Build one explicit V1 Content API browse request."""

        _require_int("page", page, minimum=1)

        query = urlencode(
            {
                "key": self._content_api_key,
                "limit": GHOST_PAGE_SIZE,
                "page": page,
                "include": GHOST_INCLUDE,
                "formats": GHOST_FORMATS,
            }
        )

        return (
            f"{self._base_url}/ghost/api/content/posts/"
            f"?{query}"
        )

    def fetch_posts_page(self, page: int) -> GhostCatalogPage:
        """Fetch and validate one explicit Content API page."""

        url = self.build_posts_page_url(page)

        try:
            payload = self._transport.get_json(url)
        except GhostContentClientError:
            raise
        except Exception as exc:
            raise GhostContentClientError(
                f"Ghost transport failed for page {page}"
            ) from exc

        result = parse_posts_page(
            _normalize_posts_payload_timestamps(
                payload
            )
        )

        if result.pagination.page != page:
            raise GhostContentResponseError(
                "Ghost pagination page does not match requested page"
            )

        if result.pagination.limit != GHOST_PAGE_SIZE:
            raise GhostContentResponseError(
                "Ghost pagination limit does not match V1 page size"
            )

        return result


def _normalize_ghost_utc_timestamp(
    value: Any,
) -> Any:
    """Canonicalize Ghost's equivalent +00:00 UTC representation."""

    if not isinstance(value, str):
        return value

    if value.endswith("+00:00"):
        return value[:-6] + "Z"

    return value


def _normalize_posts_payload_timestamps(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Canonicalize Ghost timestamps without mutating transport JSON."""

    posts = payload.get("posts")

    if not isinstance(posts, list):
        return payload

    normalized_posts: list[Any] = []
    changed = False

    for raw_post in posts:
        if not isinstance(raw_post, Mapping):
            normalized_posts.append(raw_post)
            continue

        post = dict(raw_post)

        for field in (
            "published_at",
            "updated_at",
        ):
            before = post.get(field)

            after = _normalize_ghost_utc_timestamp(
                before
            )

            if before != after:
                post[field] = after
                changed = True

        normalized_posts.append(post)

    if not changed:
        return payload

    normalized_payload = dict(payload)
    normalized_payload["posts"] = normalized_posts

    return normalized_payload

def parse_posts_page(
    payload: Mapping[str, Any],
) -> GhostCatalogPage:
    """Parse one decoded Ghost Content API posts response."""

    if not isinstance(payload, Mapping):
        raise GhostContentResponseError(
            "Ghost response must be an object"
        )

    posts_raw = payload.get("posts")

    if not isinstance(posts_raw, list):
        raise GhostContentResponseError(
            "Ghost response posts must be an array"
        )

    meta = payload.get("meta")

    if not isinstance(meta, Mapping):
        raise GhostContentResponseError(
            "Ghost response meta must be an object"
        )

    pagination_raw = meta.get("pagination")

    if not isinstance(pagination_raw, Mapping):
        raise GhostContentResponseError(
            "Ghost response pagination must be an object"
        )

    pagination = GhostPagination(
        page=_read_required_int(
            pagination_raw,
            "page",
        ),
        limit=_read_required_int(
            pagination_raw,
            "limit",
        ),
        pages=_read_required_int(
            pagination_raw,
            "pages",
        ),
        total=_read_required_int(
            pagination_raw,
            "total",
        ),
        next=_read_optional_int(
            pagination_raw,
            "next",
        ),
        prev=_read_optional_int(
            pagination_raw,
            "prev",
        ),
    )

    posts = tuple(
        _parse_post(raw_post)
        for raw_post in posts_raw
    )

    return GhostCatalogPage(
        posts=posts,
        pagination=pagination,
    )


def _parse_post(raw: Any) -> GhostCatalogPost:
    if not isinstance(raw, Mapping):
        raise GhostContentResponseError(
            "Ghost post must be an object"
        )

    authors_raw = raw.get("authors", [])

    if authors_raw is None:
        authors_raw = []

    if not isinstance(authors_raw, list):
        raise GhostContentResponseError(
            "Ghost post authors must be an array"
        )

    tags_raw = raw.get("tags", [])

    if tags_raw is None:
        tags_raw = []

    if not isinstance(tags_raw, list):
        raise GhostContentResponseError(
            "Ghost post tags must be an array"
        )

    authors = tuple(
        _parse_author(author)
        for author in authors_raw
    )

    tags = tuple(
        _parse_tag(tag)
        for tag in tags_raw
    )

    excerpt = raw.get("custom_excerpt")

    if excerpt is None:
        excerpt = raw.get("excerpt")

    try:
        return GhostCatalogPost(
            post_id=_required_string(raw, "id"),
            title=_required_string(raw, "title"),
            slug=_required_string(raw, "slug"),
            url=_required_string(raw, "url"),
            published_at=_required_string(
                raw,
                "published_at",
            ),
            updated_at=_required_string(
                raw,
                "updated_at",
            ),
            html=_required_string(raw, "html"),
            visibility=_required_string(
                raw,
                "visibility",
            ),
            access=_required_bool(raw, "access"),
            excerpt=_optional_string_value(
                excerpt,
                "excerpt",
            ),
            feature_image=_optional_string(
                raw,
                "feature_image",
            ),
            authors=authors,
            tags=tags,
        )
    except ContentSyncContractError as exc:
        raise GhostContentResponseError(
            f"Ghost post violates Content Sync contract: {exc}"
        ) from exc


def _parse_author(raw: Any) -> CatalogAuthor:
    if not isinstance(raw, Mapping):
        raise GhostContentResponseError(
            "Ghost author must be an object"
        )

    try:
        return CatalogAuthor(
            id=_optional_string(raw, "id"),
            name=_required_string(raw, "name"),
            slug=_optional_string(raw, "slug"),
        )
    except ContentSyncContractError as exc:
        raise GhostContentResponseError(
            f"Ghost author violates Content Sync contract: {exc}"
        ) from exc


def _parse_tag(raw: Any) -> CatalogTag:
    if not isinstance(raw, Mapping):
        raise GhostContentResponseError(
            "Ghost tag must be an object"
        )

    try:
        return CatalogTag(
            id=_optional_string(raw, "id"),
            name=_required_string(raw, "name"),
            slug=_optional_string(raw, "slug"),
        )
    except ContentSyncContractError as exc:
        raise GhostContentResponseError(
            f"Ghost tag violates Content Sync contract: {exc}"
        ) from exc


def _normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GhostContentClientError(
            "base_url must be a non-empty absolute URL"
        )

    stripped = value.strip().rstrip("/")
    parts = urlsplit(stripped)

    if parts.scheme not in {"http", "https"}:
        raise GhostContentClientError(
            "base_url must use http or https"
        )

    if not parts.netloc:
        raise GhostContentClientError(
            "base_url must contain a host"
        )

    if parts.query or parts.fragment:
        raise GhostContentClientError(
            "base_url must not contain query or fragment"
        )

    path = parts.path.rstrip("/")

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            "",
            "",
        )
    )


def _required_string(
    mapping: Mapping[str, Any],
    key: str,
) -> str:
    value = mapping.get(key)

    if not isinstance(value, str) or not value.strip():
        raise GhostContentResponseError(
            f"Ghost field {key} must be a non-empty string"
        )

    return value


def _optional_string(
    mapping: Mapping[str, Any],
    key: str,
) -> Optional[str]:
    return _optional_string_value(
        mapping.get(key),
        key,
    )


def _optional_string_value(
    value: Any,
    name: str,
) -> Optional[str]:
    if value is None:
        return None

    if not isinstance(value, str) or not value.strip():
        raise GhostContentResponseError(
            f"Ghost field {name} must be null or a non-empty string"
        )

    return value


def _required_bool(
    mapping: Mapping[str, Any],
    key: str,
) -> bool:
    value = mapping.get(key)

    if not isinstance(value, bool):
        raise GhostContentResponseError(
            f"Ghost field {key} must be a boolean"
        )

    return value


def _read_required_int(
    mapping: Mapping[str, Any],
    key: str,
) -> int:
    if key not in mapping:
        raise GhostContentResponseError(
            f"Ghost pagination field {key} is required"
        )

    value = mapping[key]
    _require_int(
        f"pagination.{key}",
        value,
        minimum=0 if key == "total" else 1,
    )

    return value


def _read_optional_int(
    mapping: Mapping[str, Any],
    key: str,
) -> Optional[int]:
    value = mapping.get(key)

    _require_optional_int(
        f"pagination.{key}",
        value,
        minimum=1,
    )

    return value


def _require_int(
    name: str,
    value: Any,
    *,
    minimum: int,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise GhostContentResponseError(
            f"{name} must be an integer >= {minimum}"
        )


def _require_optional_int(
    name: str,
    value: Any,
    *,
    minimum: int,
) -> None:
    if value is None:
        return

    _require_int(
        name,
        value,
        minimum=minimum,
    )