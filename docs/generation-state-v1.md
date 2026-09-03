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

## Worker V2 status feedback

Worker execution feedback uses a separate DEV FIFO and updater Lambda.

Worker status transport:

- queue: `pocket-tts-dev-status-queue.fifo`
- producer: `pocket-tts-dev`
- consumer: `pocket-tts-dev-status-updater`
- `MessageGroupId = generation_id`
- `MessageDeduplicationId = SHA-256(canonical status event)`

The worker never writes `pocket-tts-app` directly.

Before enqueue, App API creates an immutable routing item:

- pk = `GEN#<generation_id>`
- sk = `ROUTE`
- room_id = the owning room
- generation_id = the immutable generation identity

The updater resolves that route and conditionally updates the authoritative
generation item only when `generation_id`, `job_id`, and
`worker_job_fingerprint` match the pinned dispatch.

Status events are exact V1 objects.

RUNNING carries:

- schema_version
- generation_id
- job_id
- job_fingerprint
- status = RUNNING
- attempt
- occurred_at

COMPLETED additionally carries exact DEV output bucket/key and output SHA-256.

FAILED additionally carries a bounded machine-readable error_code.

The updater does not modify `review_status`.

Retry semantics:

- RUNNING may be applied repeatedly for the same pinned job.
- Intermediate worker failures keep execution state RUNNING so SQS can retry.
- FAILED is emitted only on the final configured worker receive attempt.
- If output was committed but COMPLETED feedback fails, the worker retry
  detects the matching immutable output and republishes COMPLETED without
  running TTS again.
- COMPLETED is idempotent for the same pinned job and output.

The worker event-source mapping remains disabled except during a controlled
Studio E2E proof until a later activation decision.
