import os
from pathlib import Path

import generate_narration as narration


def test_global_model_cache_reuses_identical_loader_call(
    monkeypatch,
):
    narration._MODEL_CACHE.clear()

    calls = []

    def fake_loader(**kwargs):
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(
        narration.TTSModel,
        "load_model",
        fake_loader,
    )

    first = narration._load_model_cached(
        language="english_2026-04",
        temp=0.5,
        lsd_decode_steps=5,
        quantize=False,
    )

    second = narration._load_model_cached(
        language="english_2026-04",
        temp=0.5,
        lsd_decode_steps=5,
        quantize=False,
    )

    assert first is second
    assert len(calls) == 1

    third = narration._load_model_cached(
        language="english_2026-04",
        temp=0.7,
        lsd_decode_steps=5,
        quantize=False,
    )

    assert third is not first
    assert len(calls) == 2

    narration._MODEL_CACHE.clear()


def test_model_cache_key_includes_decode_settings(
    monkeypatch,
):
    narration._MODEL_CACHE.clear()

    calls = []

    def fake_loader(**kwargs):
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(
        narration.TTSModel,
        "load_model",
        fake_loader,
    )

    narration._load_model_cached(
        language="english_2026-04",
        temp=0.5,
        lsd_decode_steps=4,
    )

    narration._load_model_cached(
        language="english_2026-04",
        temp=0.5,
        lsd_decode_steps=5,
    )

    assert len(calls) == 2

    narration._MODEL_CACHE.clear()


def test_local_cache_paths_remain_caller_paths(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(
        "AWS_LAMBDA_FUNCTION_NAME",
        raising=False,
    )

    monkeypatch.delenv(
        "POCKET_TTS_WARM_CACHE_DIR",
        raising=False,
    )

    fallback = str(
        tmp_path / "caller-cache"
    )

    assert (
        narration._warm_cache_path(
            "raw_cache",
            fallback,
        )
        == fallback
    )


def test_lambda_cache_paths_use_existing_tmp_allocation(
    monkeypatch,
):
    monkeypatch.setenv(
        "AWS_LAMBDA_FUNCTION_NAME",
        "pocket-tts-dev",
    )

    monkeypatch.delenv(
        "POCKET_TTS_WARM_CACHE_DIR",
        raising=False,
    )

    root = narration._warm_cache_root()

    assert root == (
        "/tmp/"
        "pocket-tts-warm-cache-v2"
    )

    assert os.path.normpath(
        narration._warm_cache_path(
            "raw_cache",
            "fallback",
        )
    ) == os.path.normpath(
        "/tmp/"
        "pocket-tts-warm-cache-v2/"
        "raw_cache"
    )

    assert os.path.normpath(
        narration._warm_cache_path(
            "voice_states",
            "fallback",
        )
    ) == os.path.normpath(
        "/tmp/"
        "pocket-tts-warm-cache-v2/"
        "voice_states"
    )


def test_warm_cache_eviction_is_bounded(
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "warm"

    monkeypatch.setenv(
        "POCKET_TTS_WARM_CACHE_DIR",
        str(root),
    )

    monkeypatch.delenv(
        "AWS_LAMBDA_FUNCTION_NAME",
        raising=False,
    )

    raw = root / "raw_cache"
    raw.mkdir(parents=True)

    oldest = raw / "old.bin"
    newest = raw / "new.bin"

    oldest.write_bytes(
        b"a" * 80
    )

    newest.write_bytes(
        b"b" * 80
    )

    os.utime(
        oldest,
        (1, 1),
    )

    os.utime(
        newest,
        (2, 2),
    )

    result = narration._prune_warm_cache(
        max_bytes=100
    )

    assert result["before_bytes"] == 160
    assert result["after_bytes"] <= 100
    assert result["removed_files"] >= 1

    assert newest.exists()


def test_source_routes_worker_caches_to_warm_storage():
    source = Path(
        narration.__file__
    ).read_text(
        encoding="utf-8"
    )

    assert (
        '_warm_cache_path('
        '"raw_cache"'
        in source
    )

    assert (
        '_warm_cache_path('
        '"voice_states"'
        in source
    )

    assert (
        '"reference_anchors"'
        in source
    )

    assert (
        "_load_model_cached("
        in source
    )
