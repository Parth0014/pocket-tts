import pytest

from narration_studio.status_contract import (
    StatusContractError,
    canonical_status_json,
    status_event_fingerprint,
    validate_status_event_v1,
)

GEN_ID = "gen_" + ("1" * 32)
JOB_ID = "job_" + ("2" * 32)
FP = "a" * 64
NOW = "2026-09-03T12:30:00Z"


def running():
    return {
        "schema_version": 1,
        "generation_id": GEN_ID,
        "job_id": JOB_ID,
        "job_fingerprint": FP,
        "status": "RUNNING",
        "attempt": 1,
        "occurred_at": NOW,
    }


def test_running_status_contract_is_exact():
    event = running()

    assert (
        validate_status_event_v1(
            event
        )
        == event
    )


def test_completed_requires_exact_generation_output():
    event = {
        **running(),
        "status": "COMPLETED",
        "output": {
            "bucket": "pocket-tts-dev-test",
            "key": (
                f"generations/{GEN_ID}/"
                "output.wav"
            ),
            "sha256": "b" * 64,
        },
    }

    assert (
        validate_status_event_v1(
            event
        )[
            "output"
        ][
            "sha256"
        ]
        == "b" * 64
    )


def test_failed_requires_machine_error_code():
    event = {
        **running(),
        "status": "FAILED",
        "error_code": (
            "WORKER_FINAL_ATTEMPT_FAILED"
        ),
    }

    assert (
        validate_status_event_v1(
            event
        )[
            "error_code"
        ]
        == "WORKER_FINAL_ATTEMPT_FAILED"
    )


def test_status_unknown_fields_fail_closed():
    event = running()
    event["debug"] = True

    with pytest.raises(
        StatusContractError
    ):
        validate_status_event_v1(
            event
        )


def test_status_fingerprint_is_canonical():
    event = running()
    reordered = {
        key: event[
            key
        ]
        for key in reversed(
            list(
                event
            )
        )
    }

    assert canonical_status_json(
        event
    ) == canonical_status_json(
        reordered
    )

    assert status_event_fingerprint(
        event
    ) == status_event_fingerprint(
        reordered
    )
