import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = (
    ROOT
    / "aws"
    / "pocket-tts-team-studio"
    / "lambda_function.py"
)


class StudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactRef:
    bucket: str
    key: str
    sha256: str


@dataclass(frozen=True)
class StudioDocumentRevision:
    room_id: str
    doc_id: str
    revision: int
    source_post_id: str
    source_content_hash: str
    source_narration_hash: str
    source_processor_version: int
    document: ArtifactRef
    created_at: str


def _load(*names: str):
    source = HANDLER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = set(names)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in wanted
    ]

    found = {node.name for node in nodes}
    assert found == wanted

    module = ast.Module(
        body=nodes,
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    namespace = {
        "Any": object,
        "ArtifactRef": ArtifactRef,
        "StudioDocumentRevision": StudioDocumentRevision,
        "StudioError": StudioError,
    }

    exec(
        compile(
            module,
            str(HANDLER),
            "exec",
        ),
        namespace,
    )

    return namespace


def test_current_revision_is_supported():
    namespace = _load("_document_revision_number")
    resolve = namespace["_document_revision_number"]

    assert resolve({"current_revision": 4}) == 4


def test_legacy_revision_fields_remain_supported():
    namespace = _load("_document_revision_number")
    resolve = namespace["_document_revision_number"]

    assert resolve({"revision": 3}) == 3
    assert resolve({"document_revision": 2}) == 2
    assert resolve({}, default=0) == 0


def test_current_document_pointer_artifact_is_supported():
    namespace = _load("_artifact")
    artifact = namespace["_artifact"](
        {
            "current_document_bucket": "pocket-tts-dev-test",
            "current_document_key": "studio-documents/example/v000002.json",
            "current_document_sha256": "a" * 64,
        },
        "document",
    )

    assert artifact == ArtifactRef(
        bucket="pocket-tts-dev-test",
        key="studio-documents/example/v000002.json",
        sha256="a" * 64,
    )


def test_legacy_document_artifact_shapes_remain_supported():
    namespace = _load("_artifact")
    artifact = namespace["_artifact"]

    assert artifact(
        {
            "document_bucket": "bucket-a",
            "document_key": "legacy.json",
            "document_sha256": "b" * 64,
        },
        "document",
    ) == ArtifactRef(
        bucket="bucket-a",
        key="legacy.json",
        sha256="b" * 64,
    )

    assert artifact(
        {
            "document": {
                "bucket": "bucket-b",
                "key": "nested.json",
                "sha256": "c" * 64,
            }
        },
        "document",
    ) == ArtifactRef(
        bucket="bucket-b",
        key="nested.json",
        sha256="c" * 64,
    )


def test_live_pointer_shape_hydrates_revision():
    namespace = _load(
        "_artifact",
        "_document_revision_number",
        "_revision",
    )

    value = namespace["_revision"](
        {
            "room_id": "room_" + ("1" * 32),
            "doc_id": "doc_" + ("2" * 32),
            "current_revision": 2,
            "source_post_id": "ghost-post-1",
            "source_content_hash": "a" * 64,
            "source_narration_hash": "b" * 64,
            "source_processor_version": 4,
            "current_document_bucket": "pocket-tts-dev-test",
            "current_document_key": (
                "studio-documents/room/doc/v000002.json"
            ),
            "current_document_sha256": "c" * 64,
            "created_at": "2026-09-04T00:00:00Z",
        }
    )

    assert value.revision == 2
    assert value.document.key.endswith("v000002.json")


def test_latest_document_uses_canonical_pointer_revision():
    namespace = _load(
        "_is_document_item",
        "_document_revision_number",
        "_latest_document",
    )

    older = {
        "doc_id": "doc_" + ("1" * 32),
        "source_narration_hash": "a" * 64,
        "current_revision": 1,
    }
    newer = {
        "doc_id": "doc_" + ("1" * 32),
        "source_narration_hash": "b" * 64,
        "current_revision": 2,
    }
    generation = {
        "generation_id": "gen_" + ("3" * 32),
        "doc_id": "doc_" + ("1" * 32),
        "document_revision": 2,
    }

    assert namespace["_latest_document"](
        [older, generation, newer]
    ) is newer


def test_team_studio_uses_stable_post_document_lineage():
    source = HANDLER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    ensure = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_ensure_room_document"
    )
    fragment = ast.get_source_segment(source, ensure)

    assert fragment is not None
    assert "doc_id = _doc_id(post_id)" in fragment
    assert 'item.get("doc_id") == doc_id' in fragment
    assert "_team_document_id" not in fragment


def test_processor_error_matches_required_version():
    source = HANDLER.read_text(encoding="utf-8")

    assert 'document.get("processor_version") != 4' in source
    assert "Studio requires Processor V4" in source
    assert "Studio requires Processor V3" not in source
