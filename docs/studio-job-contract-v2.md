
# Studio TTS Job Contract V2

Status: configurable Narration pace execution contract.

V2 extends the frozen Studio TTS Job Contract V1 with one
generation-affecting field: `tempo_percent`.

## Backward compatibility

Worker V1 remains valid and unchanged. Historical or retried V1 payloads do
not contain `tempo_percent` and are interpreted as 100 percent / 1.00x pace.

New Team Studio generations use schema_version 2.

## tempo_percent

Required for schema_version 2.

It must be an integer exactly equal to one of:

- 80
- 82
- 84
- 86
- 88
- 90
- 92
- 94
- 96
- 98
- 100

The worker divides the value by 100 to obtain the global narration pace
multiplier. It changes the actual generated WAV; it is not browser playback
speed.

The multiplier is applied after raw Pocket TTS synthesis during chunk
rendering. Existing role-specific render pace remains intact and is multiplied
by this setting. Raw synthesis/cache identity is not changed.

## Identity and idempotency

`tempo_percent` is pinned in the immutable generation-input artifact and the
generation DynamoDB row. Changing it creates a new generation_id.

The complete validated V2 payload, including `tempo_percent`, participates in
canonical JSON and the worker-job SHA-256 fingerprint.

Retries of one generation reuse the identical generation_id, job_id and body.

## V2 top-level fields

V2 permits the frozen V1 fields plus `tempo_percent`.
`quote_voice` remains conditional exactly as in V1.
Unknown top-level fields are rejected.

## FIFO transport

Queue: `pocket-tts-dev-jobs-queue.fifo`

- MessageGroupId = `generation_id`
- MessageDeduplicationId = `generation_id`

This preserves up to six independent generation groups in the current worker
profile while retaining retry ordering for one generation.

## Production boundary

This setting is part of DEV / Team Studio generation intent. It does not grant
production S3 write authority and does not alter publication ownership.
