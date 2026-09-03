"""Pure Production Manager contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_VOICE_ID_RE = re.compile(r"^voice_[0-9a-f]{32}$")
_GEN_ID_RE = re.compile(r"^gen_[0-9a-f]{32}$")
_QUOTE_MODES = frozenset({"preserve", "exclude", "two_voice"})
_REVIEW_STATES = frozenset({"SELECTED", "READY", "OUTDATED"})


class ManagerContractError(ValueError):
    """Manager request violates its V1 contract."""


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    voice_id: str
    quote_mode: str
    quote_voice_id: str | None


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    review_status: str


def _post_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or _POST_ID_RE.fullmatch(value) is None
    ):
        raise ManagerContractError("post_id is invalid")
    return value


def _voice_id(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or _VOICE_ID_RE.fullmatch(value) is None
    ):
        raise ManagerContractError(f"{field} is invalid")
    return value


def generation_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or _GEN_ID_RE.fullmatch(value) is None
    ):
        raise ManagerContractError("generation_id is invalid")
    return value


def adoption_room_id(post_id: str) -> str:
    post_id = _post_id(post_id)
    digest = hashlib.sha256(
        ("manager-room-v1:" + post_id).encode("utf-8")
    ).hexdigest()
    return "room_" + digest[:32]


def adoption_doc_id(post_id: str) -> str:
    post_id = _post_id(post_id)
    digest = hashlib.sha256(
        ("manager-doc-v1:" + post_id).encode("utf-8")
    ).hexdigest()
    return "doc_" + digest[:32]


def parse_generation_request(
    value: Mapping[str, Any],
) -> GenerationRequest:
    if not isinstance(value, Mapping):
        raise ManagerContractError(
            "generation request must be an object"
        )

    allowed = {
        "voice_id",
        "quote_mode",
        "quote_voice_id",
    }
    if not set(value).issubset(allowed):
        raise ManagerContractError(
            "generation request contains unexpected fields"
        )

    if "voice_id" not in value or "quote_mode" not in value:
        raise ManagerContractError(
            "voice_id and quote_mode are required"
        )

    voice_id = _voice_id(
        value["voice_id"],
        field="voice_id",
    )
    quote_mode = value["quote_mode"]

    if quote_mode not in _QUOTE_MODES:
        raise ManagerContractError("quote_mode is invalid")

    quote_voice_id = value.get("quote_voice_id")

    if quote_mode == "two_voice":
        if quote_voice_id is None:
            raise ManagerContractError(
                "quote_voice_id is required for two_voice"
            )
        quote_voice_id = _voice_id(
            quote_voice_id,
            field="quote_voice_id",
        )
    elif quote_voice_id is not None:
        raise ManagerContractError(
            "quote_voice_id is forbidden for this quote_mode"
        )

    return GenerationRequest(
        voice_id=voice_id,
        quote_mode=quote_mode,
        quote_voice_id=quote_voice_id,
    )


def parse_review_request(
    value: Mapping[str, Any],
) -> ReviewRequest:
    if not isinstance(value, Mapping):
        raise ManagerContractError(
            "review request must be an object"
        )

    if set(value) != {"review_status"}:
        raise ManagerContractError(
            "review request must contain only review_status"
        )

    review_status = value["review_status"]

    if review_status not in _REVIEW_STATES:
        raise ManagerContractError(
            "review_status is invalid"
        )

    return ReviewRequest(
        review_status=review_status
    )
