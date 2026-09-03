from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_real_generator_does_not_use_legacy_html_extractor():
    source = (ROOT / "generate_narration.py").read_text(encoding="utf-8")
    assert "from worker_document import extract_worker_blocks" in source
    assert "extracted_blocks = extract_worker_blocks(post_html)" in source
    assert "from extractor import extract_blocks" not in source


def test_worker_image_contains_shared_canonical_normalizer():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY worker_document.py ." in dockerfile
    assert "COPY narration_content ./narration_content" in dockerfile


def test_setuptools_package_contains_worker_adapter_and_canonical_package():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"worker_document"' in pyproject
    assert 'packages = ["narration_content"]' in pyproject
