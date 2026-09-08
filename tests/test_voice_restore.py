import ast
from enum import Enum
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

HANDLER = Path(__file__).resolve().parents[1] / 'aws/pocket-tts-team-studio/lambda_function.py'
VOICE_ID = 'voice_' + 'a' * 32

class Status(str, Enum):
    ACTIVE = 'ACTIVE'
    DISABLED = 'DISABLED'

class Conflict(Exception):
    def __init__(self):
        self.response = {'Error': {'Code': 'ConditionalCheckFailedException'}}


def restore_function(status, *, missing=False, conflict=False):
    calls = []
    voice = None if missing else SimpleNamespace(voice_id=VOICE_ID, display_name='Sample', status=status)
    def update(**kwargs):
        calls.append(kwargs)
        if conflict:
            raise Conflict()
    scope = dict(Any=object, _VOICE_ID_RE=re.compile(r'voice_[0-9a-f]{32}'),
                 StudioError=ValueError, VoiceStatus=Status, ClientError=Conflict,
                 _service=lambda: (None, SimpleNamespace(get_voice=lambda _: voice)),
                 _ddb=SimpleNamespace(update_item=update), VOICE_TABLE='voices', _now=lambda: 'now')
    tree = ast.parse(HANDLER.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_restore_voice')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(HANDLER), 'exec'), scope)
    return scope['_restore_voice'], calls


def test_restore_changes_only_status_and_timestamp_with_concurrency_guard():
    restore, calls = restore_function(Status.DISABLED)
    result = restore(VOICE_ID)
    assert result['status'] == 'ACTIVE'
    assert result['already_restored'] is False
    assert len(calls) == 1
    assert calls[0]['Key'] == {'voice_id': {'S': VOICE_ID}}
    assert calls[0]['ConditionExpression'] == '#status = :disabled'
    assert calls[0]['UpdateExpression'] == 'SET #status = :active, updated_at = :now'
    assert calls[0]['ExpressionAttributeValues'][':active'] == {'S': 'ACTIVE'}
    assert calls[0]['ExpressionAttributeValues'][':disabled'] == {'S': 'DISABLED'}


def test_restore_is_idempotent():
    restore, calls = restore_function(Status.ACTIVE)
    assert restore(VOICE_ID)['already_restored'] is True
    assert not calls


@pytest.mark.parametrize('identifier,missing,message', [('bad', False, 'invalid'), (VOICE_ID, True, 'not found')])
def test_restore_rejects_invalid_or_missing_voice(identifier, missing, message):
    restore, calls = restore_function(Status.DISABLED, missing=missing)
    with pytest.raises(ValueError, match=message):
        restore(identifier)
    assert not calls


def test_restore_reports_concurrent_change():
    restore, _ = restore_function(Status.DISABLED, conflict=True)
    with pytest.raises(ValueError, match='refresh and retry'):
        restore(VOICE_ID)
