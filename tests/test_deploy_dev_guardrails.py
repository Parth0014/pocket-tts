from pathlib import Path

DEPLOY = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "deploy-dev.ps1"
)


def deploy_text() -> str:
    return DEPLOY.read_text(
        encoding="utf-8",
    )


def test_deploy_preflight_requires_exact_dev_listbucket():
    text = deploy_text()

    assert "PocketTTSDevS3Access" in text
    assert "s3:ListBucket" in text
    assert (
        "arn:aws:s3:::pocket-tts-dev-test"
        in text
    )
    assert "Worker DEV ListBucket: PRESENT" in text


def test_listbucket_guard_precedes_docker_build():
    text = deploy_text()

    permission_check = text.index(
        "Worker DEV ListBucket: PRESENT"
    )
    docker_check = text.index(
        "docker version --format"
    )

    assert permission_check < docker_check


def test_deploy_guard_does_not_reference_production_audio():
    text = deploy_text()

    assert (
        "gratefulness-narration-audio"
        not in text
    )
