
from pathlib import Path
from types import SimpleNamespace

import pytest

from narration_studio.models import GenerationRecord, StudioContractError
from narration_studio.worker_contract import (
    build_worker_job_for_generation,
    build_worker_job_v1,
    build_worker_job_v2,
    validate_worker_job,
    validate_worker_job_v2,
)

ROOT = Path(__file__).resolve().parents[1]
JOB_ID = "job_" + ("1" * 32)
GEN_ID = "gen_" + ("2" * 32)
VOICE_ID = "voice_" + ("3" * 32)
POST_ID = "ghostpost123"
CONTENT_HASH = "a" * 64


def test_generation_record_tempo_default_is_100():
    assert GenerationRecord.__dataclass_fields__["tempo_percent"].default == 100


def test_v1_remains_tempo_free():
    job = build_worker_job_v1(
        job_id=JOB_ID,
        generation_id=GEN_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
    )
    assert job["schema_version"] == 1
    assert "tempo_percent" not in job
    assert validate_worker_job(job) == job


@pytest.mark.parametrize("tempo", [80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100])
def test_v2_accepts_frozen_tempo_steps(tempo):
    job = build_worker_job_v2(
        job_id=JOB_ID,
        generation_id=GEN_ID,
        post_id=POST_ID,
        content_hash=CONTENT_HASH,
        voice_id=VOICE_ID,
        quote_mode="preserve",
        tempo_percent=tempo,
    )
    assert job["schema_version"] == 2
    assert job["tempo_percent"] == tempo
    assert validate_worker_job_v2(job) == job


@pytest.mark.parametrize(
    "tempo",
    [79, 81, 101, 100.0, "100", None, True],
)
def test_v2_rejects_invalid_tempo(tempo):
    with pytest.raises(StudioContractError):
        build_worker_job_v2(
            job_id=JOB_ID,
            generation_id=GEN_ID,
            post_id=POST_ID,
            content_hash=CONTENT_HASH,
            voice_id=VOICE_ID,
            quote_mode="preserve",
            tempo_percent=tempo,
        )


def test_generation_builder_emits_v2_with_pinned_tempo():
    document = SimpleNamespace(sha256="b" * 64)
    generation = SimpleNamespace(
        generation_id=GEN_ID,
        doc_id="doc_" + ("5" * 32),
        document_revision=1,
        document=document,
        voice_id=VOICE_ID,
        quote_mode="preserve",
        quote_voice_id=None,
        tempo_percent=96,
    )
    revision = SimpleNamespace(
        doc_id=generation.doc_id,
        revision=1,
        document=document,
        source_post_id=POST_ID,
        source_content_hash=CONTENT_HASH,
    )

    job = build_worker_job_for_generation(
        job_id=JOB_ID,
        generation=generation,
        revision=revision,
        quote_mode="preserve",
    )
    assert job["schema_version"] == 2
    assert job["tempo_percent"] == 96



def test_app_api_reads_generation_tempo_and_builds_v2():
    source = (
        ROOT
        / "aws"
        / "pocket-tts-app-api"
        / "lambda_function.py"
    ).read_text(encoding="utf-8")

    assert "build_worker_job_v2" in source
    assert "build_worker_job_v1" not in source
    assert 'tempo_attr = generation.get("tempo_percent")' in source
    assert 'tempo_percent = int(tempo_attr["N"])' in source
    assert "tempo_percent=tempo_percent" in source


def test_frontend_pace_contract():
    html = (
        ROOT / "team_studio_web" / "index.html"
    ).read_text(encoding="utf-8")
    js = (
        ROOT / "team_studio_web" / "app.js"
    ).read_text(encoding="utf-8")
    css = (
        ROOT / "team_studio_web" / "styles.css"
    ).read_text(encoding="utf-8")

    assert 'id="tempo-percent"' in html
    assert 'min="80"' in html
    assert 'max="100"' in html
    assert 'step="2"' in html
    assert 'value="100"' in html
    assert "tempo_percent: tempoPercent" in js
    assert "formatTempo(gen.tempo_percent ?? 100)" in js
    assert "Narration pace control" in css
    assert 'style="' not in html


def test_team_api_pins_and_returns_tempo():
    source = (
        ROOT
        / "aws"
        / "pocket-tts-team-studio"
        / "lambda_function.py"
    ).read_text(encoding="utf-8")

    assert 'tempo_percent = body.get("tempo_percent", 100)' in source
    assert "tempo_percent=tempo_percent" in source
    assert (
        'values[-1]["tempo_percent"] = '
        'int(item.get("tempo_percent", 100) or 100)'
        in source
    )
    assert '"tempo_percent": prepared.generation.tempo_percent' in source


def test_storage_pins_tempo():
    core = (
        ROOT / "narration_studio" / "core.py"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "narration_studio" / "service.py"
    ).read_text(encoding="utf-8")
    ddb = (
        ROOT / "narration_studio" / "dynamodb.py"
    ).read_text(encoding="utf-8")

    assert '"tempo_percent": tempo_percent' in core
    assert "tempo_percent=tempo_percent" in core
    assert "tempo_percent=tempo_percent" in service
    assert '"tempo_percent": _n(' in ddb
    assert "generation.tempo_percent" in ddb



def test_generation_adapter_persists_tempo_as_number():
    source = (
        ROOT / "narration_studio" / "dynamodb.py"
    ).read_text(encoding="utf-8")

    expected = (
        '"tempo_percent": _n(\n'
        '                generation.tempo_percent\n'
        '            ),'
    )
    assert expected in source


def test_worker_routes_tempo_to_render():
    worker = (
        ROOT / "lambda_function.py"
    ).read_text(encoding="utf-8")
    generator = (
        ROOT / "generate_narration.py"
    ).read_text(encoding="utf-8")

    assert "Worker V2 compatibility layer" in worker
    assert (
        'speed=float(job.get("tempo_percent", 100)) / 100.0'
        in worker
    )
    assert "Narration pace multiplier validation" in generator
    assert "* speed" in generator


def test_v2_doc_and_fifo_concurrency_contract():
    doc = (
        ROOT / "docs" / "studio-job-contract-v2.md"
    ).read_text(encoding="utf-8")
    dispatch = (
        ROOT / "narration_studio" / "dispatch.py"
    ).read_text(encoding="utf-8")

    assert "tempo_percent" in doc
    assert "MessageGroupId = `generation_id`" in doc
    assert "MessageDeduplicationId = `generation_id`" in doc
    assert "MessageGroupId=pinned.generation_id" in dispatch
    assert "MessageDeduplicationId=pinned.generation_id" in dispatch
