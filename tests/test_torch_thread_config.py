import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_generator_restores_explicit_omp_threads_after_pocket_tts():
    environment = dict(os.environ)
    environment["OMP_NUM_THREADS"] = "3"
    environment["MKL_NUM_THREADS"] = "3"

    code = r"""
import torch
import pocket_tts

# Simulate/confirm the upstream import-time single-thread state.
torch.set_num_threads(1)

interop_before = torch.get_num_interop_threads()

import generate_narration

assert torch.get_num_threads() == 3
assert generate_narration.TORCH_INTRAOP_THREADS == 3
assert torch.get_num_interop_threads() == interop_before
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        result.stdout + "\n" + result.stderr
    )


def test_thread_restore_leaves_default_untouched_without_omp_setting(
    monkeypatch,
):
    import generate_narration

    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    before = generate_narration.torch.get_num_threads()

    restored = generate_narration._restore_torch_intraop_threads(
        {}
    )

    assert restored == before
    assert generate_narration.torch.get_num_threads() == before


def test_thread_restore_rejects_invalid_explicit_budget():
    import pytest
    import generate_narration

    with pytest.raises(
        ValueError,
        match="OMP_NUM_THREADS must be a positive integer",
    ):
        generate_narration._restore_torch_intraop_threads(
            {"OMP_NUM_THREADS": "0"}
        )
