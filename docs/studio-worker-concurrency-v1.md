# Studio Worker Concurrency Profile V1

This profile changes transport/runtime concurrency only. The Worker JSON body
remains Studio TTS Job Contract V1 (`schema_version = 1`).

## FIFO transport

Queue: `pocket-tts-dev-jobs-queue.fifo`

- `MessageGroupId = generation_id`
- `MessageDeduplicationId = generation_id`

An identical retry keeps the same generation_id, body, FIFO group and
deduplication identity. Different generations use different FIFO groups and
may therefore execute concurrently.

## Worker Lambda

Function: `pocket-tts-dev`

- memory: 8192 MB
- timeout: 900 seconds
- reserved concurrency: 6
- architecture: x86_64

## SQS event-source mapping

- BatchSize: 1
- MaximumConcurrency: 6

The effective worker parallelism is capped at six simultaneous Lambda
invocations, provided there are enough distinct active FIFO message groups.

## Safety

Configuration alone does not enable execution. During this sprint the worker
mapping and production Bridge mapping remain DISABLED, no Studio FIFO message
is sent, no TTS runs, and production audio remains untouched.
