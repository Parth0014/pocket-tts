import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from narration_content.sync_models import (
    SyncState,
    SyncStatus,
)

PATH = (
    Path(__file__).parents[1]
    / "aws"
    / "pocket-tts-content-sync"
    / "lambda_function.py"
)

SPEC = importlib.util.spec_from_file_location(
    "content_sync_lambda_test",
    PATH,
)

MODULE = importlib.util.module_from_spec(
    SPEC
)

assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


SYNC_ID = "sync_" + ("1" * 32)


def make_state():
    return SyncState(
        sync_id=SYNC_ID,
        status=SyncStatus.RUNNING,
        next_page=3,
        started_at="2026-09-03T12:00:00Z",
        updated_at="2026-09-03T12:01:00Z",
        expected_total=714,
        expected_pages=8,
        completed_at=None,
        last_error_code=None,
    )


class FakeStateStore:
    def __init__(
        self,
        state,
    ):
        self.state = state
        self.calls = 0

    def get_current(self):
        self.calls += 1
        return self.state


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_once(
        self,
        *,
        start_new=False,
        resume_failed=False,
    ):
        self.calls.append(
            (
                start_new,
                resume_failed,
            )
        )

        return SimpleNamespace(
            action="FIRST_PASS_PAGE",
            state=make_state(),
            reconciliation=None,
        )


def reset():
    MODULE._STATE_STORE = None
    MODULE._RUNNER = None


def test_status_reads_current_without_building_runner():
    reset()

    store = FakeStateStore(
        make_state()
    )

    MODULE._STATE_STORE = store

    result = MODULE.lambda_handler(
        {
            "action": "status",
        },
        None,
    )

    assert result["ok"] is True
    assert result["action"] == "STATUS"
    assert result["state"]["next_page"] == 3
    assert isinstance(
        result["state"][
            "verification_pending"
        ],
        bool,
    )
    assert store.calls == 1
    assert MODULE._RUNNER is None


def test_status_empty_table():
    reset()

    MODULE._STATE_STORE = FakeStateStore(
        None
    )

    assert MODULE.lambda_handler(
        {},
        None,
    ) == {
        "ok": True,
        "action": "STATUS",
        "state": None,
    }


def test_run_passes_flags_to_runner():
    reset()

    runner = FakeRunner()
    MODULE._RUNNER = runner

    result = MODULE.lambda_handler(
        {
            "action": "run",
            "start_new": True,
            "resume_failed": False,
        },
        None,
    )

    assert runner.calls == [
        (
            True,
            False,
        )
    ]

    assert result["action"] == (
        "FIRST_PASS_PAGE"
    )


@pytest.mark.parametrize(
    "event",
    [
        {
            "action": "invalid",
        },
        {
            "action": "run",
            "start_new": "true",
        },
        {
            "action": "run",
            "resume_failed": 1,
        },
    ],
)
def test_invalid_events_are_rejected(
    event,
):
    reset()

    with pytest.raises(ValueError):
        MODULE.lambda_handler(
            event,
            None,
        )


def test_real_ghost_client_exposes_expected_page_method():
    from narration_content.ghost_client import (
        GhostContentClient,
    )

    assert callable(
        getattr(
            GhostContentClient,
            "fetch_posts_page",
            None,
        )
    )

    assert not hasattr(
        GhostContentClient,
        "fetch_page",
    )


def test_verification_pending_payload_is_boolean():
    payload = MODULE._state_payload(
        make_state()
    )

    assert payload is not None
    assert isinstance(
        payload[
            "verification_pending"
        ],
        bool,
    )