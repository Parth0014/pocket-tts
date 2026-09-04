import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = (
    ROOT
    / "aws"
    / "pocket-tts-team-studio"
    / "lambda_function.py"
)


def _helper():
    source = HANDLER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_team_document_id"
    )

    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {}
    exec(compile(module, str(HANDLER), "exec"), namespace)

    return namespace["_team_document_id"]


def _document(**overrides):
    value = {
        "post_id": "ghost-post-1",
        "content_hash": "a" * 64,
        "narration_hash": "b" * 64,
        "processor_version": 4,
    }
    value.update(overrides)
    return value


def test_team_document_identity_is_stable_and_worker_safe():
    helper = _helper()
    identity = helper(_document())

    assert re.fullmatch(r"doc_[0-9a-f]{32}", identity)
    assert identity == helper(_document())


def test_team_document_identity_changes_with_canonical_source_identity():
    helper = _helper()
    baseline = helper(_document())

    assert helper(_document(content_hash="c" * 64)) != baseline
    assert helper(_document(narration_hash="d" * 64)) != baseline
    assert helper(_document(processor_version=5)) != baseline
    assert helper(_document(post_id="ghost-post-2")) != baseline


def test_ensure_room_document_uses_content_addressed_doc_identity():
    source = HANDLER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    ensure = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_ensure_room_document"
    )

    fragment = ast.get_source_segment(source, ensure)

    assert fragment is not None
    assert "_team_document_id(document)" in fragment
