"""Canonical narration-content contracts shared by backend components."""

from .document import (
    PROCESSOR_VERSION,
    SCHEMA_VERSION,
    build_document,
)
from .hashing import (
    canonical_json_bytes,
    compute_content_hash,
    compute_narration_hash,
    semantic_hash_payload,
)
from .normalizer import normalize_ghost_html
from .validation import (
    NarrationDocumentValidationError,
    validate_document,
)

__all__ = [
    "PROCESSOR_VERSION",
    "SCHEMA_VERSION",
    "NarrationDocumentValidationError",
    "build_document",
    "canonical_json_bytes",
    "compute_content_hash",
    "compute_narration_hash",
    "normalize_ghost_html",
    "semantic_hash_payload",
    "validate_document",
]