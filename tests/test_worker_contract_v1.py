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
    "job_id": "job_contract_v1",
    "generation_id": "gen_contract_v1",
    "post_id": "post_contract_v1",
    "content_hash": CONTENT_HASH,
    "post": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            "ghost/post_contract_v1/"
            + CONTENT_HASH
            + ".html"
        ),
    },
    "voice": {
        "voice_id": "voice_contract_v1",
        "bucket": "pocket-tts-dev-test",
        "key": (
            "voices/voice_contract_v1/"
            "reference.wav"
        ),
    },
    "quote_mode": "preserve",
    "output": {
        "bucket": "pocket-tts-dev-test",
        "key": (
            "generations/gen_contract_v1/"
            "output.wav"
        ),
    },
}


def validate(job):
    return lf._validate_job(
        job,
        require_schema_v1=True,
    )


class WorkerV1ContractTests(unittest.TestCase):
    def test_valid_preserve_job_is_canonical(self):
        validated = validate(
            copy.deepcopy(BASE_JOB)
        )

        self.assertEqual(
            set(validated.keys()),
            {
                "schema_version",
                "job_id",
                "generation_id",
                "post_id",
                "content_hash",
                "post",
                "voice",
                "quote_mode",
                "output",
            },
        )

        self.assertEqual(
            set(validated["post"].keys()),
            {"bucket", "key"},
        )

        self.assertEqual(
            set(validated["voice"].keys()),
            {"voice_id", "bucket", "key"},
        )

        self.assertEqual(
            set(validated["output"].keys()),
            {"bucket", "key"},
        )

    def test_unknown_top_level_field_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["debug"] = False

        with self.assertRaisesRegex(
            ValueError,
            "job contains unknown fields: debug",
        ):
            validate(job)

    def test_unknown_post_field_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["post"]["etag"] = "example"

        with self.assertRaisesRegex(
            ValueError,
            "post contains unknown fields: etag",
        ):
            validate(job)

    def test_unknown_voice_field_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["voice"]["display_name"] = "Example"

        with self.assertRaisesRegex(
            ValueError,
            "voice contains unknown fields: display_name",
        ):
            validate(job)

    def test_unknown_output_field_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["output"]["content_type"] = "audio/wav"

        with self.assertRaisesRegex(
            ValueError,
            "output contains unknown fields: content_type",
        ):
            validate(job)

    def test_quote_mode_is_required_for_v1(self):
        job = copy.deepcopy(BASE_JOB)
        del job["quote_mode"]

        with self.assertRaisesRegex(
            ValueError,
            "quote_mode is required for schema_version 1",
        ):
            validate(job)

    def test_preserve_forbids_quote_voice(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_voice"] = {
            "voice_id": "voice_quote_v1",
            "bucket": "pocket-tts-dev-test",
            "key": (
                "voices/voice_quote_v1/"
                "reference.wav"
            ),
        }

        with self.assertRaisesRegex(
            ValueError,
            "quote_voice must be absent",
        ):
            validate(job)

    def test_preserve_forbids_null_quote_voice(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_voice"] = None

        with self.assertRaisesRegex(
            ValueError,
            "quote_voice must be absent",
        ):
            validate(job)

    def test_exclude_without_quote_voice_is_valid(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "exclude"

        validated = validate(job)

        self.assertEqual(
            validated["quote_mode"],
            "exclude",
        )

        self.assertNotIn(
            "quote_voice",
            validated,
        )

    def test_two_voice_requires_quote_voice(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "two_voice"

        with self.assertRaisesRegex(
            ValueError,
            "quote_voice is required",
        ):
            validate(job)

    def test_valid_two_voice_job_is_canonical(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "two_voice"
        job["quote_voice"] = {
            "voice_id": "voice_quote_v1",
            "bucket": "pocket-tts-dev-test",
            "key": (
                "voices/voice_quote_v1/"
                "reference.wav"
            ),
        }

        validated = validate(job)

        self.assertEqual(
            validated["quote_voice"],
            {
                "voice_id": "voice_quote_v1",
                "bucket": "pocket-tts-dev-test",
                "key": (
                    "voices/voice_quote_v1/"
                    "reference.wav"
                ),
            },
        )

    def test_unknown_quote_voice_field_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "two_voice"
        job["quote_voice"] = {
            "voice_id": "voice_quote_v1",
            "bucket": "pocket-tts-dev-test",
            "key": (
                "voices/voice_quote_v1/"
                "reference.wav"
            ),
            "person_name": "Example",
        }

        with self.assertRaisesRegex(
            ValueError,
            "quote_voice contains unknown fields: person_name",
        ):
            validate(job)

    def test_voice_key_must_be_exact_reference_wav(self):
        job = copy.deepcopy(BASE_JOB)
        job["voice"]["key"] = (
            "voices/voice_contract_v1/"
            "alternate.wav"
        )

        with self.assertRaisesRegex(
            ValueError,
            "V1 voice key must be exactly",
        ):
            validate(job)

    def test_quote_voice_key_must_be_exact_reference_wav(self):
        job = copy.deepcopy(BASE_JOB)
        job["quote_mode"] = "two_voice"
        job["quote_voice"] = {
            "voice_id": "voice_quote_v1",
            "bucket": "pocket-tts-dev-test",
            "key": (
                "voices/voice_quote_v1/"
                "alternate.wav"
            ),
        }

        with self.assertRaisesRegex(
            ValueError,
            "V1 quote_voice key must be exactly",
        ):
            validate(job)

    def test_schema_version_boolean_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["schema_version"] = True

        with self.assertRaisesRegex(
            ValueError,
            "SQS jobs must use schema_version 1",
        ):
            validate(job)

    def test_schema_version_float_is_rejected(self):
        job = copy.deepcopy(BASE_JOB)
        job["schema_version"] = 1.0

        with self.assertRaisesRegex(
            ValueError,
            "SQS jobs must use schema_version 1",
        ):
            validate(job)

    def test_bucket_whitespace_is_not_normalized(self):
        job = copy.deepcopy(BASE_JOB)
        job["voice"]["bucket"] = (
            " pocket-tts-dev-test "
        )

        with self.assertRaises(ValueError):
            validate(job)

    def test_fingerprint_is_stable_for_same_contract(self):
        first = validate(
            copy.deepcopy(BASE_JOB)
        )

        second_job = {
            "output": copy.deepcopy(BASE_JOB["output"]),
            "quote_mode": BASE_JOB["quote_mode"],
            "voice": copy.deepcopy(BASE_JOB["voice"]),
            "post": copy.deepcopy(BASE_JOB["post"]),
            "content_hash": BASE_JOB["content_hash"],
            "post_id": BASE_JOB["post_id"],
            "generation_id": BASE_JOB["generation_id"],
            "job_id": BASE_JOB["job_id"],
            "schema_version": BASE_JOB["schema_version"],
        }

        second = validate(second_job)

        self.assertEqual(
            lf._job_fingerprint(first),
            lf._job_fingerprint(second),
        )

    def test_legacy_direct_contract_still_defaults_preserve(self):
        legacy_job = {
            "post": {
                "bucket": "pocket-tts-dev-test",
                "key": "input/sample.html",
            },
            "voice": {
                "bucket": "pocket-tts-dev-test",
                "key": "voices/voice_anchor.wav",
            },
            "output": {
                "bucket": "pocket-tts-dev-test",
                "key": "test-results/legacy.wav",
            },
        }

        validated = lf._validate_job(
            legacy_job,
            require_schema_v1=False,
        )

        self.assertEqual(
            validated["quote_mode"],
            "preserve",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)