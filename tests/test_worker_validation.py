import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lambda_function as lf


CONTENT_HASH = "a" * 64


BASE_JOB = {
    "schema_version": 1,
    "job_id": "job_validation_v1",
    "generation_id": "gen_validation_v1",
    "post_id": "post_validation_v1",
    "content_hash": CONTENT_HASH,
    "post": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            "ghost/post_validation_v1/"
            + CONTENT_HASH
            + ".html"
        ),
    },
    "voice": {
        "voice_id": "voice_validation_v1",
        "bucket": "pocket-tts-dev-test",
        "key": (
            "voices/voice_validation_v1/"
            "reference.wav"
        ),
    },
    "quote_mode": "preserve",
    "output": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            "generations/gen_validation_v1/"
            "output.wav"
        ),
    },
}


def validate(job):
    return lf._validate_job(
        job,
        require_schema_v1=True,
    )


class WorkerIdentifierValidationTests(unittest.TestCase):
    def test_valid_existing_style_ids_are_accepted(self):
        validated = validate(
            copy.deepcopy(BASE_JOB)
        )

        self.assertEqual(
            validated["job_id"],
            "job_validation_v1",
        )

    def test_job_id_rejects_slash(self):
        job = copy.deepcopy(BASE_JOB)
        job["job_id"] = "job/bad"

        with self.assertRaisesRegex(
            ValueError,
            "job_id must match",
        ):
            validate(job)

    def test_job_id_rejects_surrounding_whitespace(self):
        job = copy.deepcopy(BASE_JOB)
        job["job_id"] = " job_validation_v1 "

        with self.assertRaisesRegex(
            ValueError,
            "leading or trailing whitespace",
        ):
            validate(job)

    def test_post_id_rejects_slash_even_with_matching_key(self):
        job = copy.deepcopy(BASE_JOB)
        job["post_id"] = "post/bad"
        job["post"]["key"] = (
            "ghost/post/bad/"
            + CONTENT_HASH
            + ".html"
        )

        with self.assertRaisesRegex(
            ValueError,
            "post_id must match",
        ):
            validate(job)

    def test_generation_id_rejects_slash_even_with_matching_key(self):
        job = copy.deepcopy(BASE_JOB)
        job["generation_id"] = "gen/bad"
        job["output"]["key"] = (
            "generations/gen/bad/output.wav"
        )

        with self.assertRaisesRegex(
            ValueError,
            "generation_id must match",
        ):
            validate(job)

    def test_voice_id_rejects_slash_even_with_matching_key(self):
        job = copy.deepcopy(BASE_JOB)
        job["voice"]["voice_id"] = "voice/bad"
        job["voice"]["key"] = (
            "voices/voice/bad/reference.wav"
        )

        with self.assertRaisesRegex(
            ValueError,
            "voice_id must match",
        ):
            validate(job)

    def test_quote_voice_id_rejects_slash(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "two_voice"
        job["quote_voice"] = {
            "voice_id": "quote/bad",
            "bucket": "pocket-tts-dev-test",
            "key": "voices/quote/bad/reference.wav",
        }

        with self.assertRaisesRegex(
            ValueError,
            "voice_id must match",
        ):
            validate(job)

    def test_identifier_rejects_more_than_128_characters(self):
        job = copy.deepcopy(BASE_JOB)
        job["job_id"] = "j" * 129

        with self.assertRaisesRegex(
            ValueError,
            "job_id must match",
        ):
            validate(job)

    def test_uppercase_content_hash_is_rejected(self):
        uppercase_hash = "A" * 64

        job = copy.deepcopy(BASE_JOB)
        job["content_hash"] = uppercase_hash
        job["post"]["key"] = (
            "ghost/post_validation_v1/"
            + uppercase_hash
            + ".html"
        )

        with self.assertRaisesRegex(
            ValueError,
            "content_hash must be a SHA-256 lowercase hex digest",
        ):
            validate(job)

    def test_lowercase_content_hash_is_accepted(self):
        validated = validate(
            copy.deepcopy(BASE_JOB)
        )

        self.assertEqual(
            validated["content_hash"],
            CONTENT_HASH,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)