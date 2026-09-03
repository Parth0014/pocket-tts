from urllib.parse import parse_qs, urlsplit

import pytest

from narration_content.ghost_client import (
    GHOST_FORMATS,
    GHOST_INCLUDE,
    GHOST_PAGE_SIZE,
    GhostContentClient,
    GhostContentClientError,
    GhostContentResponseError,
    parse_posts_page,
)
from narration_content.sync_models import (
    UnsupportedGhostAccessError,
)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.response


class FailingTransport:
    def get_json(self, url):
        raise RuntimeError("simulated network failure")


def raw_post(**overrides):
    value = {
        "id": "ghost-123",
        "title": "A grateful day",
        "slug": "a-grateful-day",
        "url": "https://example.test/a-grateful-day/",
        "published_at": "2026-09-01T10:00:00.000Z",
        "updated_at": "2026-09-02T11:00:00.000Z",
        "html": "<p>Gratitude — café 🌱</p>",
        "plaintext": "Gratitude — café 🌱",
        "visibility": "public",
        "access": True,
        "custom_excerpt": "A custom excerpt",
        "excerpt": "Generated excerpt",
        "feature_image": "https://example.test/image.jpg",
        "authors": [
            {
                "id": "author-1",
                "name": "Author",
                "slug": "author",
                "bio": "Not retained",
            }
        ],
        "tags": [
            {
                "id": "tag-1",
                "name": "Gratitude",
                "slug": "gratitude",
                "description": "Not retained",
            }
        ],
    }
    value.update(overrides)
    return value


def raw_page(
    *,
    posts=None,
    page=1,
    limit=100,
    pages=8,
    total=714,
    next_page=2,
    prev=None,
):
    if posts is None:
        posts = [raw_post()]

    return {
        "posts": posts,
        "meta": {
            "pagination": {
                "page": page,
                "limit": limit,
                "pages": pages,
                "total": total,
                "next": next_page,
                "prev": prev,
            }
        },
    }


def test_v1_request_constants_are_frozen():
    assert GHOST_PAGE_SIZE == 100
    assert GHOST_INCLUDE == "authors,tags"
    assert GHOST_FORMATS == "html,plaintext"


def test_client_builds_explicit_content_api_request():
    transport = FakeTransport(raw_page())

    client = GhostContentClient(
        base_url="https://gratitude.example/",
        content_api_key="test-content-key",
        transport=transport,
    )

    url = client.build_posts_page_url(3)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "gratitude.example"
    assert parsed.path == "/ghost/api/content/posts/"

    assert query == {
        "key": ["test-content-key"],
        "limit": ["100"],
        "page": ["3"],
        "include": ["authors,tags"],
        "formats": ["html,plaintext"],
    }


def test_base_url_path_is_preserved():
    client = GhostContentClient(
        base_url="https://example.test/publication/",
        content_api_key="test-key",
        transport=FakeTransport(raw_page()),
    )

    parsed = urlsplit(
        client.build_posts_page_url(1)
    )

    assert (
        parsed.path
        == "/publication/ghost/api/content/posts/"
    )


def test_fetch_page_uses_transport_exactly_once():
    transport = FakeTransport(raw_page())

    client = GhostContentClient(
        base_url="https://example.test",
        content_api_key="test-key",
        transport=transport,
    )

    result = client.fetch_posts_page(1)

    assert len(transport.urls) == 1
    assert result.pagination.page == 1
    assert len(result.posts) == 1


def test_page_parses_catalog_post_and_minimal_author_tag_metadata():
    result = parse_posts_page(raw_page())
    post = result.posts[0]

    assert post.post_id == "ghost-123"
    assert post.title == "A grateful day"
    assert post.visibility == "public"
    assert post.access is True
    assert post.excerpt == "A custom excerpt"
    assert (
        post.feature_image
        == "https://example.test/image.jpg"
    )

    assert len(post.authors) == 1
    assert post.authors[0].id == "author-1"
    assert post.authors[0].name == "Author"
    assert post.authors[0].slug == "author"

    assert len(post.tags) == 1
    assert post.tags[0].id == "tag-1"
    assert post.tags[0].name == "Gratitude"
    assert post.tags[0].slug == "gratitude"


def test_custom_excerpt_wins_over_generated_excerpt():
    result = parse_posts_page(
        raw_page(
            posts=[
                raw_post(
                    custom_excerpt="Editorial",
                    excerpt="Generated",
                )
            ]
        )
    )

    assert result.posts[0].excerpt == "Editorial"


def test_generated_excerpt_used_when_custom_excerpt_is_null():
    result = parse_posts_page(
        raw_page(
            posts=[
                raw_post(
                    custom_excerpt=None,
                    excerpt="Generated",
                )
            ]
        )
    )

    assert result.posts[0].excerpt == "Generated"


def test_optional_author_and_tag_ids_may_be_null():
    result = parse_posts_page(
        raw_page(
            posts=[
                raw_post(
                    authors=[
                        {
                            "id": None,
                            "name": "Author",
                            "slug": None,
                        }
                    ],
                    tags=[
                        {
                            "id": None,
                            "name": "Tag",
                            "slug": None,
                        }
                    ],
                )
            ]
        )
    )

    assert result.posts[0].authors[0].id is None
    assert result.posts[0].authors[0].slug is None
    assert result.posts[0].tags[0].id is None
    assert result.posts[0].tags[0].slug is None


def test_missing_authors_and_tags_become_empty_tuples():
    post = raw_post()
    post.pop("authors")
    post.pop("tags")

    result = parse_posts_page(
        raw_page(posts=[post])
    )

    assert result.posts[0].authors == ()
    assert result.posts[0].tags == ()


def test_nonpublic_content_is_parsed_not_silently_filtered():
    result = parse_posts_page(
        raw_page(
            posts=[
                raw_post(
                    visibility="members",
                    access=False,
                )
            ]
        )
    )

    post = result.posts[0]

    assert post.visibility == "members"
    assert post.access is False

    with pytest.raises(
        UnsupportedGhostAccessError
    ):
        post.require_v1_access()


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "title",
        "slug",
        "url",
        "published_at",
        "updated_at",
        "html",
        "visibility",
    ],
)
def test_required_post_string_fields_fail_closed(field):
    post = raw_post()
    post[field] = None

    with pytest.raises(
        GhostContentResponseError
    ):
        parse_posts_page(
            raw_page(posts=[post])
        )


def test_access_must_be_real_boolean():
    with pytest.raises(
        GhostContentResponseError,
        match="access",
    ):
        parse_posts_page(
            raw_page(
                posts=[
                    raw_post(access="true")
                ]
            )
        )


def test_posts_must_be_array():
    payload = raw_page()
    payload["posts"] = {}

    with pytest.raises(
        GhostContentResponseError,
        match="posts",
    ):
        parse_posts_page(payload)


def test_meta_is_required():
    payload = raw_page()
    payload.pop("meta")

    with pytest.raises(
        GhostContentResponseError,
        match="meta",
    ):
        parse_posts_page(payload)


def test_pagination_is_required():
    payload = raw_page()
    payload["meta"] = {}

    with pytest.raises(
        GhostContentResponseError,
        match="pagination",
    ):
        parse_posts_page(payload)


def test_requested_page_must_match_response_page():
    client = GhostContentClient(
        base_url="https://example.test",
        content_api_key="test-key",
        transport=FakeTransport(
            raw_page(
                page=2,
                prev=1,
                next_page=3,
            )
        ),
    )

    with pytest.raises(
        GhostContentResponseError,
        match="requested page",
    ):
        client.fetch_posts_page(1)


def test_response_limit_must_match_v1_page_size():
    client = GhostContentClient(
        base_url="https://example.test",
        content_api_key="test-key",
        transport=FakeTransport(
            raw_page(limit=50)
        ),
    )

    with pytest.raises(
        GhostContentResponseError,
        match="page size",
    ):
        client.fetch_posts_page(1)


def test_transport_error_is_wrapped_without_request_url():
    client = GhostContentClient(
        base_url="https://example.test",
        content_api_key="secret-value-not-for-logs",
        transport=FailingTransport(),
    )

    with pytest.raises(
        GhostContentClientError
    ) as exc_info:
        client.fetch_posts_page(4)

    message = str(exc_info.value)

    assert "page 4" in message
    assert "secret-value-not-for-logs" not in message


@pytest.mark.parametrize(
    "page",
    [0, -1, True, 1.5, "1"],
)
def test_requested_page_must_be_positive_integer(page):
    client = GhostContentClient(
        base_url="https://example.test",
        content_api_key="test-key",
        transport=FakeTransport(raw_page()),
    )

    with pytest.raises(
        GhostContentResponseError
    ):
        client.build_posts_page_url(page)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "example.test",
        "ftp://example.test",
        "https:///missing-host",
        "https://example.test?x=1",
        "https://example.test#fragment",
    ],
)
def test_invalid_base_urls_are_rejected(base_url):
    with pytest.raises(
        GhostContentClientError
    ):
        GhostContentClient(
            base_url=base_url,
            content_api_key="test-key",
            transport=FakeTransport(raw_page()),
        )


def test_empty_content_api_key_is_rejected():
    with pytest.raises(
        GhostContentClientError,
        match="content_api_key",
    ):
        GhostContentClient(
            base_url="https://example.test",
            content_api_key="",
            transport=FakeTransport(raw_page()),
        )


def test_page_cannot_contain_more_posts_than_limit():
    payload = raw_page(
        posts=[
            raw_post(id="one"),
            raw_post(id="two"),
        ],
        limit=1,
        pages=2,
        total=2,
        next_page=2,
    )

    with pytest.raises(
        GhostContentResponseError,
        match="more posts",
    ):
        parse_posts_page(payload)


def test_final_page_may_have_null_next():
    result = parse_posts_page(
        raw_page(
            page=8,
            pages=8,
            total=714,
            next_page=None,
            prev=7,
            posts=[raw_post()],
        )
    )

    assert result.pagination.page == 8
    assert result.pagination.next is None
    assert result.pagination.prev == 7


def test_pagination_rejects_boolean_as_integer():
    payload = raw_page()
    payload["meta"]["pagination"]["page"] = True

    with pytest.raises(
        GhostContentResponseError,
        match="integer",
    ):
        parse_posts_page(payload)

def test_live_ghost_plus_00_timestamp_is_normalized():
    import narration_content.ghost_client as module

    payload = {
        "posts": [
            {
                "published_at": "2026-09-03T08:00:00.000+00:00",
                "updated_at": "2026-09-03T08:01:02.345+00:00",
            }
        ]
    }

    normalized = module._normalize_posts_payload_timestamps(
        payload
    )

    assert normalized["posts"][0]["published_at"] == (
        "2026-09-03T08:00:00.000Z"
    )

    assert normalized["posts"][0]["updated_at"] == (
        "2026-09-03T08:01:02.345Z"
    )

    assert payload["posts"][0]["published_at"] == (
        "2026-09-03T08:00:00.000+00:00"
    )


def test_existing_ghost_z_timestamp_is_not_rewritten():
    import narration_content.ghost_client as module

    payload = {
        "posts": [
            {
                "published_at": "2026-09-03T08:00:00.000Z",
                "updated_at": "2026-09-03T08:01:00.000Z",
            }
        ]
    }

    assert (
        module._normalize_posts_payload_timestamps(
            payload
        )
        is payload
    )


def test_non_utc_offset_is_not_reinterpreted_as_utc():
    import narration_content.ghost_client as module

    assert (
        module._normalize_ghost_utc_timestamp(
            "2026-09-03T10:00:00+02:00"
        )
        == "2026-09-03T10:00:00+02:00"
    )