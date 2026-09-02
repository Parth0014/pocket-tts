# Studio TTS Job Contract V1

Status: Frozen V1 execution contract

This document defines the message body consumed from the Studio FIFO
queue by `pocket-tts-dev`.

## Transport

Queue:

`pocket-tts-dev-jobs-queue.fifo`

Producer:

Studio/App API

Consumer:

`pocket-tts-dev`

FIFO producer values:

`MessageGroupId = "tts"`

`MessageDeduplicationId = generation_id`

SQS/Lambda retries of the same logical generation must preserve the
same message payload, generation_id, and job_id.

## Top-level fields

V1 permits exactly these top-level fields:

- schema_version
- job_id
- generation_id
- post_id
- content_hash
- post
- voice
- quote_mode
- output
- quote_voice, only when quote_mode is `two_voice`

Unknown top-level fields are rejected.

## schema_version

Required.

Must be integer `1`.

## job_id

Required.

Logical dispatch identifier.

Worker-safe syntax:

`^[A-Za-z0-9_-]{1,128}$`

Studio/App API canonical producer format:

`job_<uuid4 hex>`

The same job_id is reused across retries of an identical generation.

## generation_id

Required.

Immutable generation identity.

Worker-safe syntax:

`^[A-Za-z0-9_-]{1,128}$`

Studio/App API canonical producer format:

`gen_<uuid4 hex>`

The output location is derived from generation_id.

## post_id

Required.

Exact Ghost post identity.

Worker-safe syntax:

`^[A-Za-z0-9_-]{1,128}$`

## content_hash

Required.

SHA-256 of the exact Ghost HTML encoded as UTF-8.

Must be exactly 64 lowercase hexadecimal characters.

## post

Required object.

Permitted fields exactly:

- bucket
- key

bucket must be:

`pocket-tts-dev-test`

key must be exactly:

`ghost/<post_id>/<content_hash>.html`

Unknown post fields are rejected.

## voice

Required object.

Permitted fields exactly:

- voice_id
- bucket
- key

voice_id must satisfy the worker-safe ID syntax.

bucket must be:

`pocket-tts-dev-test`

key must be exactly:

`voices/<voice_id>/reference.wav`

Unknown voice fields are rejected.

## quote_mode

Required.

Permitted values:

- preserve
- exclude
- two_voice

V1 does not default quote_mode. The producer must state it explicitly.

## quote_voice

Conditional object.

It MUST be absent when quote_mode is:

- preserve
- exclude

It MUST be present when quote_mode is:

- two_voice

When present, permitted fields exactly:

- voice_id
- bucket
- key

bucket must be:

`pocket-tts-dev-test`

key must be exactly:

`voices/<voice_id>/reference.wav`

Unknown quote_voice fields are rejected.

## output

Required object.

Permitted fields exactly:

- bucket
- key

bucket must be:

`pocket-tts-dev-test`

key must be exactly:

`generations/<generation_id>/output.wav`

Unknown output fields are rejected.

## Generation settings

V1 has no message-level model tuning settings.

Temperature, LSD steps, noise values, EOS thresholds, model language,
and similar implementation settings are not part of this contract.

Adding generation-affecting settings requires an explicit future
contract decision and must not be introduced as arbitrary V1 fields.

## Unknown fields

Unknown fields are rejected rather than ignored.

This prevents producer drift and ensures that the idempotency
fingerprint represents only the frozen V1 generation contract.

## Idempotency

The worker fingerprints the complete validated V1 payload using
canonical JSON and SHA-256.

An identical retry must therefore preserve the identical validated
payload.

Existing output with the same fingerprint is idempotent success.

Existing output with a different fingerprint is a generation conflict.

## Example: preserve quotes

~~~json
{
  "schema_version": 1,
  "job_id": "job_0123456789abcdef0123456789abcdef",
  "generation_id": "gen_0123456789abcdef0123456789abcdef",
  "post_id": "ghostpost123",
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "post": {
    "bucket": "pocket-tts-dev-test",
    "key": "ghost/ghostpost123/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html"
  },
  "voice": {
    "voice_id": "voice_0123456789abcdef0123456789abcdef",
    "bucket": "pocket-tts-dev-test",
    "key": "voices/voice_0123456789abcdef0123456789abcdef/reference.wav"
  },
  "quote_mode": "preserve",
  "output": {
    "bucket": "pocket-tts-dev-test",
    "key": "generations/gen_0123456789abcdef0123456789abcdef/output.wav"
  }
}
~~~

## Example: two voices

The top-level structure is identical except:

~~~json
{
  "quote_mode": "two_voice",
  "quote_voice": {
    "voice_id": "voice_fedcba9876543210fedcba9876543210",
    "bucket": "pocket-tts-dev-test",
    "key": "voices/voice_fedcba9876543210fedcba9876543210/reference.wav"
  }
}
~~~