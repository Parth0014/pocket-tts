import pytest

from narration_manager.contracts import (
    ManagerContractError,
    adoption_doc_id,
    adoption_room_id,
    parse_generation_request,
    parse_review_request,
)

VOICE = "voice_" + ("1" * 32)
QUOTE = "voice_" + ("2" * 32)


def test_adoption_ids_are_stable_and_canonical():
    room_a = adoption_room_id("ghost123")
    room_b = adoption_room_id("ghost123")
    doc = adoption_doc_id("ghost123")

    assert room_a == room_b
    assert room_a.startswith("room_")
    assert len(room_a) == 37
    assert doc.startswith("doc_")
    assert len(doc) == 36


def test_preserve_generation_request():
    value = parse_generation_request(
        {
            "voice_id": VOICE,
            "quote_mode": "preserve",
        }
    )

    assert value.voice_id == VOICE
    assert value.quote_voice_id is None


def test_two_voice_requires_quote_voice():
    value = parse_generation_request(
        {
            "voice_id": VOICE,
            "quote_mode": "two_voice",
            "quote_voice_id": QUOTE,
        }
    )

    assert value.quote_voice_id == QUOTE


@pytest.mark.parametrize(
    "payload",
    [
        {
            "voice_id": VOICE,
            "quote_mode": "preserve",
            "quote_voice_id": QUOTE,
        },
        {
            "voice_id": VOICE,
            "quote_mode": "two_voice",
        },
        {
            "voice_id": "bad",
            "quote_mode": "preserve",
        },
        {
            "voice_id": VOICE,
            "quote_mode": "other",
        },
        {
            "voice_id": VOICE,
            "quote_mode": "preserve",
            "extra": True,
        },
    ],
)
def test_invalid_generation_request_rejected(
    payload,
):
    with pytest.raises(
        ManagerContractError
    ):
        parse_generation_request(payload)


@pytest.mark.parametrize(
    "status",
    [
        "SELECTED",
        "READY",
        "OUTDATED",
    ],
)
def test_review_contract(status):
    assert (
        parse_review_request(
            {"review_status": status}
        ).review_status
        == status
    )
