"""Lightweight immutable artifact value objects for Narration Studio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PreparedArtifact:
    key: str
    body: bytes
    metadata: Mapping[str, str]
