import ast
from pathlib import Path


HANDLER = Path(__file__).resolve().parents[1] / "aws/pocket-tts-team-studio/lambda_function.py"


def test_history_includes_all_pages_and_both_voice_roles():
    calls = []

    def generation(identifier, voice, quote=None, origin="TEAM_STUDIO", date="2026-01-01"):
        return dict(generation_id=identifier, voice_id=voice, quote_voice_id=quote,
                    studio_origin=origin, created_at=date, source_post_id="post1")

    pages = [
        {"Items": [], "LastEvaluatedKey": {"pk": {"S": "page1"}}},
        {"Items": [generation("narrator", "target"),
                   generation("quote", "other", "target", date="2026-02-01"),
                   generation("unrelated", "other"),
                   generation("foreign", "target", origin="OTHER")]},
    ]

    class Database:
        def scan(self, **kwargs):
            calls.append(dict(kwargs))
            return pages.pop(0)

    tree = ast.parse(HANDLER.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_voice_generations")
    namespace = dict(Any=object, APP_TABLE="app", _ddb=Database(),
                     _python_item=lambda item: item,
                     _is_generation_item=lambda item: isinstance(item.get("generation_id"), str),
                     _catalog=lambda: [{"id": "post1", "title": "A story"}])
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(HANDLER), "exec"), namespace)
    result = namespace["_voice_generations"]("target")
    assert [item["generation_id"] for item in result["items"]] == ["quote", "narrator"]
    assert result["items"][0]["post_title"] == "A story"
    assert calls[1]["ExclusiveStartKey"] == {"pk": {"S": "page1"}}
    assert calls[0]["ExpressionAttributeValues"][":voice"] == {"S": "target"}
