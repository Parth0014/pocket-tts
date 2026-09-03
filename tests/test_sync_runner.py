from types import SimpleNamespace

from narration_content.sync_core import (
    ReconciliationResult,
)
from narration_content.sync_models import (
    SyncState,
    SyncStatus,
)
from narration_content.sync_runner import (
    ContentSyncRunner,
)

SYNC_ID = "sync_" + ("1" * 32)
START = "2026-09-03T12:00:00Z"
NOW = "2026-09-03T12:01:00Z"
DONE = "2026-09-03T12:05:00Z"


def make_state(
    *,
    status=SyncStatus.RUNNING,
    next_page=1,
    expected_total=None,
    expected_pages=None,
    completed_at=None,
    last_error_code=None,
):
    return SyncState(
        sync_id=SYNC_ID,
        status=status,
        next_page=next_page,
        started_at=START,
        updated_at=NOW,
        expected_total=expected_total,
        expected_pages=expected_pages,
        completed_at=completed_at,
        last_error_code=last_error_code,
    )


class FakeRuntimeStateStore:
    def __init__(
        self,
        current,
    ):
        self.current = current
        self.start_calls = []

    def get_current(self):
        return self.current

    def start_new(
        self,
        *,
        now,
    ):
        self.start_calls.append(
            now
        )

        self.current = make_state()

        return self.current


class FakeCore:
    def __init__(self):
        self.page_calls = []
        self.resume_calls = []
        self.verify_calls = []

        self.page_result = None
        self.resume_result = None
        self.verify_result = None

    def process_first_pass_page(
        self,
        *,
        state,
        page,
        seen_at,
    ):
        self.page_calls.append(
            (
                state,
                page,
                seen_at,
            )
        )

        return (
            self.page_result
            if self.page_result is not None
            else state
        )

    def resume_failed(
        self,
        *,
        state,
        now,
    ):
        self.resume_calls.append(
            (
                state,
                now,
            )
        )

        return self.resume_result

    def run_verification(
        self,
        *,
        state,
        pages,
        completed_at,
    ):
        self.verify_calls.append(
            (
                state,
                pages,
                completed_at,
            )
        )

        return self.verify_result


class SequenceClock:
    def __init__(
        self,
        *values,
    ):
        self.values = list(
            values
        )

    def __call__(self):
        if not self.values:
            return NOW

        return self.values.pop(0)


def test_missing_current_starts_and_processes_page_one():
    state_store = FakeRuntimeStateStore(
        None
    )

    core = FakeCore()

    next_state = make_state(
        next_page=2,
        expected_total=714,
        expected_pages=8,
    )

    core.page_result = next_state

    pages = []

    def fetch(page_number):
        pages.append(page_number)
        return f"page-{page_number}"

    runner = ContentSyncRunner(
        core=core,
        state_store=state_store,
        fetch_page=fetch,
        now=SequenceClock(
            START,
            NOW,
        ),
    )

    result = runner.run_once()

    assert state_store.start_calls == [
        START
    ]

    assert pages == [1]

    assert result.action == (
        "FIRST_PASS_PAGE"
    )

    assert result.state == next_state


def test_running_first_pass_is_bounded_to_one_page():
    current = make_state(
        next_page=4,
        expected_total=714,
        expected_pages=8,
    )

    state_store = FakeRuntimeStateStore(
        current
    )

    core = FakeCore()

    core.page_result = make_state(
        next_page=5,
        expected_total=714,
        expected_pages=8,
    )

    fetched = []

    runner = ContentSyncRunner(
        core=core,
        state_store=state_store,
        fetch_page=lambda number: (
            fetched.append(number)
            or f"page-{number}"
        ),
        now=lambda: NOW,
    )

    result = runner.run_once()

    assert fetched == [4]
    assert len(core.page_calls) == 1
    assert result.action == (
        "FIRST_PASS_PAGE"
    )


def test_failed_sync_does_not_resume_without_explicit_flag():
    failed = make_state(
        status=SyncStatus.FAILED,
        next_page=3,
        expected_total=714,
        expected_pages=8,
        last_error_code=(
            "CATALOG_CHANGED_DURING_SYNC"
        ),
    )

    core = FakeCore()

    result = ContentSyncRunner(
        core=core,
        state_store=FakeRuntimeStateStore(
            failed
        ),
        fetch_page=lambda number: (
            f"page-{number}"
        ),
        now=lambda: NOW,
    ).run_once()

    assert result.action == "FAILED"
    assert core.resume_calls == []
    assert core.page_calls == []


def test_explicit_resume_continues_same_page():
    failed = make_state(
        status=SyncStatus.FAILED,
        next_page=3,
        expected_total=714,
        expected_pages=8,
        last_error_code=(
            "CATALOG_CHANGED_DURING_SYNC"
        ),
    )

    resumed = make_state(
        status=SyncStatus.RUNNING,
        next_page=3,
        expected_total=714,
        expected_pages=8,
    )

    advanced = make_state(
        status=SyncStatus.RUNNING,
        next_page=4,
        expected_total=714,
        expected_pages=8,
    )

    core = FakeCore()
    core.resume_result = resumed
    core.page_result = advanced

    fetched = []

    result = ContentSyncRunner(
        core=core,
        state_store=FakeRuntimeStateStore(
            failed
        ),
        fetch_page=lambda number: (
            fetched.append(number)
            or f"page-{number}"
        ),
        now=lambda: NOW,
    ).run_once(
        resume_failed=True
    )

    assert len(core.resume_calls) == 1
    assert fetched == [3]
    assert result.state == advanced


def test_verification_fetches_exact_expected_pages_and_completes():
    verification_state = make_state(
        status=SyncStatus.RUNNING,
        next_page=9,
        expected_total=714,
        expected_pages=8,
    )

    completed = make_state(
        status=SyncStatus.COMPLETE,
        next_page=None,
        expected_total=714,
        expected_pages=8,
        completed_at=DONE,
    )

    reconciliation = ReconciliationResult(
        marked_post_ids=(
            "old-post",
        ),
        webhook_race_skipped_post_ids=(
            "race-post",
        ),
    )

    core = FakeCore()

    core.verify_result = (
        SimpleNamespace(
            completed_state=completed,
            reconciliation=reconciliation,
        )
    )

    fetched = []

    result = ContentSyncRunner(
        core=core,
        state_store=FakeRuntimeStateStore(
            verification_state
        ),
        fetch_page=lambda number: (
            fetched.append(number)
            or f"page-{number}"
        ),
        now=lambda: DONE,
    ).run_once()

    assert fetched == list(
        range(1, 9)
    )

    assert len(core.verify_calls) == 1

    _, pages, completed_at = (
        core.verify_calls[0]
    )

    assert pages == tuple(
        f"page-{number}"
        for number in range(
            1,
            9,
        )
    )

    assert completed_at == DONE

    assert result.action == (
        "VERIFIED_COMPLETE"
    )

    assert result.state == completed
    assert (
        result.reconciliation
        == reconciliation
    )


def test_complete_sync_is_noop():
    completed = make_state(
        status=SyncStatus.COMPLETE,
        next_page=None,
        expected_total=714,
        expected_pages=8,
        completed_at=DONE,
    )

    core = FakeCore()

    result = ContentSyncRunner(
        core=core,
        state_store=FakeRuntimeStateStore(
            completed
        ),
        fetch_page=lambda number: (
            f"page-{number}"
        ),
        now=lambda: NOW,
    ).run_once()

    assert result.action == "COMPLETE"
    assert core.page_calls == []
    assert core.verify_calls == []


def test_explicit_new_sync_delegates_to_guarded_state_store():
    completed = make_state(
        status=SyncStatus.COMPLETE,
        next_page=None,
        expected_total=714,
        expected_pages=8,
        completed_at=DONE,
    )

    state_store = FakeRuntimeStateStore(
        completed
    )

    core = FakeCore()

    core.page_result = make_state(
        next_page=2,
        expected_total=714,
        expected_pages=8,
    )

    result = ContentSyncRunner(
        core=core,
        state_store=state_store,
        fetch_page=lambda number: (
            f"page-{number}"
        ),
        now=SequenceClock(
            START,
            NOW,
        ),
    ).run_once(
        start_new=True
    )

    assert state_store.start_calls == [
        START
    ]

    assert result.action == (
        "FIRST_PASS_PAGE"
    )