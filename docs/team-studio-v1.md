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


## Team-facing cleanup

The visible Studio intentionally omits Lambda memory, concurrency, queue,
processor implementation and production-boundary rollout details. Operators
see product concepts only: posts, narration text, voices, generation state,
audio and review.

New generations created through this surface are stamped
`studio_origin = TEAM_STUDIO`. The Team Studio generation history filters on
that marker. Earlier integration and quality-baseline generation rows remain
intact for audit but do not appear in the team interface.

The Voice Library opens on Active voices and has an Archived filter with counts.
Archived/DISABLED voices retain their reference preview and generation history.
Restore voice returns them to ACTIVE through the authenticated POST
`/studio-api/voices/<voice_id>/restore` route. Restoration conditionally changes
only status and updated_at, preserves voice identity and audio, and is idempotent.
Only ACTIVE voices appear in narrator and quote-voice selection.

## Shared Reference Audio Library

Team Studio has a shared folder layer on top of the immutable voice registry.
Folder name is the only required user-facing field. A folder can contain many
existing ACTIVE voices. Alias, short description, and favorite are optional per
saved reference. The same voice can be saved in multiple folders.

The underlying voice ID and S3 reference WAV are never moved or duplicated.
Folder archiving and reference removal are soft state updates rather than
DynamoDB/S3 deletes.

DynamoDB layout in `pocket-tts-app`:
- Folder: `pk=REFLIB#TEAM`, `sk=FOLDER#folder_<id>`.
- Saved voice: `pk=REFFOLDER#folder_<id>`, `sk=VOICE#voice_<id>`.

The current dashboard-code session has no stable per-person identity, so these
folders are intentionally shared by the Team Studio. Personal/private folders
can be added later when team-member identity exists.

This feature does not change the worker job/status contracts, TTS container,
worker queues, or production audio paths.

## Voice restore deployment route

Deploying the Lambda handler alone is insufficient: this HTTP API has explicit
Studio routes and a default route pointing to another backend. Ensure
`POST /studio-api/voices/{voice_id}/restore` targets the same integration as
`POST /studio-api/voices/{voice_id}/archive`.

On API `za7yry02le`, both routes target `integrations/syg2x48` (Team Studio).
The `$default` stage auto-deploys route changes. Authentication remains enforced
by the Studio Lambda session check, as with the archive route.

Verify an authenticated restore request and then confirm ACTIVE status from the
voice list. An unauthenticated 401 alone does not prove correct routing because
the default backend also requires authentication.
