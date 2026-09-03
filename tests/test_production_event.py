import pytest

from narration_studio.production_event import (
    ProductionEventError,
    validate_production_event_v1,
)

HASH = "a" * 64


def valid_event():
    return {
        "schema_version": 1,
        "post_id": "60bdb2d2609e29003bef5486",
        "content_hash": HASH,
        "reason": "NEW_POST",
    }


def test_exact_v1_event_is_accepted():
    event = validate_production_event_v1(
        valid_event()
    )

    assert event.post_id == (
        "60bdb2d2609e29003bef5486"
    )
    assert event.content_hash == HASH
    assert event.reason == "NEW_POST"


@pytest.mark.parametrize(
    "reason",
    [
        "NEW_POST",
        "CONTENT_CHANGED",
    ],
)
def test_exact_reason_values(
    reason,
):
    value = valid_event()
    value["reason"] = reason

    assert (
        validate_production_event_v1(
            value
        ).reason
        == reason
    )


def test_reason_is_not_idempotency_identity():
    first = valid_event()
    second = valid_event()
    second[
        "reason"
    ] = "CONTENT_CHANGED"

    a = validate_production_event_v1(
        first
    )
    b = validate_production_event_v1(
        second
    )

    assert (
        a.post_id,
        a.content_hash,
    ) == (
        b.post_id,
        b.content_hash,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(
            extra="forbidden"
        ),
        lambda value: value.update(
            schema_version=2
        ),
        lambda value: value.update(
            post_id="bad post"
        ),
        lambda value: value.update(
            content_hash="A" * 64
        ),
        lambda value: value.update(
            reason="OTHER"
        ),
    ],
)
def test_invalid_or_drifted_v1_is_rejected(
    mutation,
):
    value = valid_event()
    mutation(value)

    with pytest.raises(
        ProductionEventError
    ):
        validate_production_event_v1(
            value
        )
