from pathlib import Path

ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)


def test_fifo_transport_groups_by_generation():
    source = (
        ROOT
        / "narration_studio"
        / "dispatch.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "MessageGroupId=pinned.generation_id" in source
    assert "MessageDeduplicationId=pinned.generation_id" in source
    assert "FIFO_MESSAGE_GROUP_ID" not in source


def test_message_body_contract_remains_v1():
    source = (
        ROOT
        / "docs"
        / "studio-job-contract-v1.md"
    ).read_text(
        encoding="utf-8"
    )

    assert "Must be integer `1`." in source
    assert "MessageGroupId = generation_id" in source
    assert "MessageDeduplicationId = generation_id" in source


def test_runtime_profile_is_six_way():
    source = (
        ROOT
        / "docs"
        / "studio-worker-concurrency-v1.md"
    ).read_text(
        encoding="utf-8"
    )

    assert "memory: 8192 MB" in source
    assert "reserved concurrency: 6" in source
    assert "BatchSize: 1" in source
    assert "MaximumConcurrency: 6" in source
