# Team Narration Studio V1

The Team Studio is the internal operator surface for generating and reviewing
DEV narration from published Ghost posts.

It intentionally hides rooms, document IDs, S3 keys, SQS and Lambda mechanics
from team members.

## Operator workflow

1. Sign in with the existing dashboard code.
2. Search published Ghost posts.
3. Open a post.
4. Review its Processor V3 canonical narration text.
5. Choose an ACTIVE narrator and quote mode.
6. Generate audio when execution is enabled.
7. Watch QUEUED / RUNNING / COMPLETED status.
8. Listen to the completed DEV WAV.
9. Mark the preferred generation SELECTED and then READY.

## Ghost content

Ghost Content API is read-only in the Studio.

The server obtains the existing Content API key from
`/pocket-tts/content-sync/ghost-content-api-key`. The browser never sees the
key.

Opening a post normalizes its current HTML through the shared Processor V3
normalizer. Creating a generation automatically creates/reuses the
deterministic Studio room and imports a new document revision when the
narration hash or processor version changes.

## Voice lifecycle

The Voice Library can:

- add a new immutable WAV reference, creating a new `voice_<uuid>` identity;
- play existing references;
- archive an ACTIVE voice.

"Archive" is intentionally implemented as `VoiceStatus.DISABLED`, not deletion.
The DynamoDB voice record and immutable S3 reference remain intact, so older
generations continue to retain their pinned voice identity and reference hash.
A DISABLED voice is excluded from new narration generation choices.

## Execution ownership

The Team Studio Lambda itself has no SQS permission.

When `EXECUTION_ENABLED=true`, the browser first asks Team Studio to create the
generation intent and then calls the existing authenticated App API enqueue
route:

`POST /rooms/<room_id>/generations/<generation_id>/enqueue`

This preserves App API ownership of dispatch, job pinning and FIFO publishing.

Initial frontend deployment uses `EXECUTION_ENABLED=false` while the Studio UI
is reviewed. The six-way worker mapping also remains DISABLED during that
deployment.

## Runtime profile

Prepared worker profile:

- 8192 MB memory
- reserved concurrency 6
- SQS BatchSize 1
- SQS MaximumConcurrency 6
- MessageGroupId = generation_id
- MessageDeduplicationId = generation_id

## Production boundary

Team Studio has no authority to:

- write production audio;
- consume the production queue;
- write NarrationPublications;
- access legacy NarrationJobs;
- publish a generation.

Ghost/Production Manager UI remains deferred.
