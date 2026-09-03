from pathlib import Path

BRIDGE = (
    Path(__file__).resolve().parents[1]
    / "aws"
    / "pocket-tts-studio-bridge"
    / "lambda_function.py"
)


def test_bridge_maintains_manager_index():
    source = BRIDGE.read_text(
        encoding="utf-8"
    )

    assert "MANAGER#INTAKE" in source
    assert "studio_manager_intake_index" in source


def test_bridge_still_has_no_tts_dispatch():
    source = BRIDGE.read_text(
        encoding="utf-8"
    )

    assert "send_message(" not in source
    assert (
        "gratefulness-narration-audio"
        not in source
    )
