import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HANDLER = (
    ROOT
    / "aws"
    / "pocket-tts-team-studio"
    / "lambda_function.py"
)


def _load_revision_helper():
    source = HANDLER.read_text(encoding="utf-8")
    tree = ast.parse(source)

    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_document_revision_number"
    )

    namespace = {
        "Any": object,
        "StudioError": RuntimeError,
    }

    module = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(module)

    exec(
        compile(module, str(HANDLER), "exec"),
        namespace,
    )

    return namespace["_document_revision_number"]


def test_document_revision_prefers_current_field():
    resolve = _load_revision_helper()
    assert resolve({"revision": 4, "document_revision": 2}) == 4


def test_document_revision_falls_back_when_current_field_is_none():
    resolve = _load_revision_helper()
    assert resolve({"revision": None, "document_revision": 7}) == 7


def test_document_revision_falls_back_when_current_field_is_absent():
    resolve = _load_revision_helper()
    assert resolve({"document_revision": 3}) == 3


def test_document_revision_supports_next_revision_default():
    resolve = _load_revision_helper()
    assert resolve({"revision": None}, default=0) == 0


def test_document_revision_rejects_missing_revision_without_default():
    resolve = _load_revision_helper()
    with pytest.raises(RuntimeError, match="document revision is missing"):
        resolve({"revision": None})


def test_team_studio_uses_compatibility_resolver_in_both_paths():
    source = HANDLER.read_text(encoding="utf-8")
    assert "revision=_document_revision_number(item)," in source
    assert "_document_revision_number(latest, default=0) + 1" in source
    assert (
        'int(item.get("revision", item.get("document_revision")))'
        not in source
    )
