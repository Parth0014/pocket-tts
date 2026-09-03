# Generation State V1

Generation execution state and human review state are separate state machines.

## Execution status

The worker-facing execution field is `generation_status`.

A generation has no execution status before it is submitted.

Execution transitions:

`<absent> -> QUEUED -> RUNNING -> COMPLETED`

`FAILED` is a terminal execution outcome from an attempted generation.

`SELECTED`, `READY`, and `OUTDATED` are never execution statuses.

## Review status

The Studio-facing review field is `review_status`.

Values frozen by this V1 implementation:

- `UNREVIEWED`
- `SELECTED`
- `READY`
- `OUTDATED`

A newly created generation starts as `UNREVIEWED`.

Review state does not replace worker execution state.

## Generation intent

The generation item pins generation-affecting inputs before enqueue:

- immutable generation_id
- document revision and SHA-256
- source post_id
- source content_hash
- source narration_hash
- main voice identity/version/reference SHA-256
- quote_mode
- optional quote voice identity/version/reference SHA-256
- immutable generation input artifact

Changing a generation-affecting input creates a new generation identity.

## Dispatch

The exact Worker V1 body and SHA-256 fingerprint are pinned to the generation before SQS submission.

FIFO transport remains:

- MessageGroupId = `tts`
- MessageDeduplicationId = `generation_id`

After SQS accepts the message, App API conditionally sets `generation_status = QUEUED`.

The same pinned job_id/body is reused for a retry of the same logical generation.

## Security boundary

App API receives only `sqs:SendMessage` to the exact Studio FIFO.

It receives no production S3 write permission.

The Worker event-source mapping remains disabled during the controlled transport test.
