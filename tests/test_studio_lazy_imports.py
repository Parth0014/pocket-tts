import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_auth_import_does_not_require_narration_content(
    tmp_path,
):
    repo = Path(__file__).resolve().parents[1]

    package_copy = (
        tmp_path
        / "narration_studio"
    )

    shutil.copytree(
        repo / "narration_studio",
        package_copy,
    )

    for pycache in package_copy.rglob(
        "__pycache__"
    ):
        shutil.rmtree(
            pycache,
        )

    code = (
        "from narration_studio.auth import "
        "sign_session, verify_session; "
        "token = sign_session("
        "subject='internal-dashboard', "
        "signing_secret='s' * 64, "
        "now=1000, ttl_seconds=3600); "
        "assert verify_session("
        "token, signing_secret='s' * 64, now=1001"
        ")['sub'] == 'internal-dashboard'"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(
        tmp_path
    )
    env["PYTHONNOUSERSITE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(tmp_path)!r}); "
                + code
            ),
        ],
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, (
        result.stdout
        + result.stderr
    )


def test_package_root_lazy_exports_still_resolve():
    import narration_studio

    assert (
        narration_studio.RoomRecord.__name__
        == "RoomRecord"
    )

    assert callable(
        narration_studio.prepare_generation_input
    )
