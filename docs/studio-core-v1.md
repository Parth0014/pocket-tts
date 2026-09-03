# Narration Studio Core V1

Status: implementation contract.

## Scope

Studio V1 manages internal editable narration work without publishing audio.

This layer does not:

- send SQS messages;
- invoke TTS;
- publish production audio;
- mutate the Ghost webhook pipeline.

## Shared app-table keys

Room metadata:

`pk = ROOM#<room_id>`
`sk = META`

Owner-room lookup:

`pk = OWNER#<owner_id>`
`sk = ROOM#<room_id>`

Document pointer:

`pk = ROOM#<room_id>`
`sk = DOC#<doc_id>`

Generation:

`pk = ROOM#<room_id>`
`sk = GEN#<generation_id>`

## IDs

V1 application IDs are lowercase, typed UUID4-hex identifiers:

`room_<32 lowercase hex>`
`doc_<32 lowercase hex>`
`gen_<32 lowercase hex>`
`voice_<32 lowercase hex>`

ID generation belongs to the future App API layer. Domain code validates IDs but does not generate them implicitly.

## Studio document revisions

A Ghost Narration Document V1 is validated using the canonical package before import.

An imported Studio revision stores the exact canonical Narration Document inside a Studio revision envelope.

S3 key:

`studio-documents/<room_id>/<doc_id>/v<revision:06d>.json`

Revisions are immutable.

The app table stores only the current revision pointer and source provenance.

Revision advancement is conditional:

- revision 1 requires no existing current revision;
- revision N requires current revision N-1.

## Generation snapshots

A generation is created only from:

- one immutable Studio document revision; and
- one ACTIVE voice version.

The generation pins:

- document revision;
- document S3 key;
- document SHA-256;
- voice ID;
- voice version;
- reference WAV key;
- reference WAV SHA-256.

Generation-input key:

`studio-generation-inputs/<room_id>/<generation_id>.json`

Generation creation initially stores status `READY`.

SQS transition from READY to QUEUED is intentionally outside this core.

## Voice registry

`NarrationVoices` remains the shared voice registry.

A V1 voice contains:

- voice_id;
- display_name;
- status;
- version;
- reference bucket/key/SHA-256;
- created_at;
- updated_at.

Reference WAV key:

`studio-voices/<voice_id>/v<version:06d>/reference.wav`

The reference object is immutable.

The current V1 boundary validates that reference bytes are a RIFF/WAVE container. Audio-model-specific sample-rate or duration constraints belong to the worker/model compatibility layer rather than this storage contract.

## Immutability

Studio S3 writes use conditional create semantics.

A retry against an existing key succeeds only when:

- content length matches;
- object metadata matches; and
- object bytes match.

Different bytes at the same immutable key are a conflict.

## Isolation

Studio Core V1 contains no production bucket name, no SQS send operation, and no delete operation.