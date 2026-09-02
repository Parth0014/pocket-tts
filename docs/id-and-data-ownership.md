# Narration IDs and Data Ownership

Status: V1 canonical contract

## Core identifiers

### post_id
- Source: Ghost
- Opaque external identifier.
- Never regenerated or derived from slug/title.
- Safe path form required for V1: `^[A-Za-z0-9_-]{1,128}$`.

### content_hash
- SHA-256 of the exact Ghost HTML encoded as UTF-8.
- Format: 64 lowercase hexadecimal characters.
- `(post_id, content_hash)` identifies one immutable narration source snapshot.

### voice_id
- Created by Studio/App API.
- Canonical producer format: `voice_<uuid4 hex>`.
- Independent of display name/person name.
- Immutable.

### generation_id
- Created by Studio/App API.
- Canonical producer format: `gen_<uuid4 hex>`.
- Identifies one immutable generation intent/artifact.
- Any generation-affecting input change creates a new generation_id.

### test_id
- Created by Studio/App API for a voice-test artifact.
- Canonical producer format: `test_<uuid4 hex>`.
- Immutable for one voice-test artifact.

### job_id
- Created by Studio/App API.
- Canonical producer format: `job_<uuid4 hex>`.
- Identifies the logical dispatch for a generation.
- Must remain unchanged across SQS/Lambda retries of the same generation payload.
- Lambda request IDs and SQS delivery metadata identify execution attempts.

### publication_key
- Created only by the Publisher.
- Format: `v000001`, `v000002`, ...
- Six-digit monotonic version scoped to post_id.
- Never reused.

### schema_version
- Integer version of the specific storage/message/API contract.
- Not a publication, generation, or content version.

## Identity relationships

One post may have many content hashes.

One `(post_id, content_hash)` snapshot may have many generations.

A V1 generation normally has one stable job_id reused across retries.

One completed generation may be published at most deliberately through the Publisher.

A post may have many immutable publication versions.

## Worker ID boundary

For V1 worker inputs, path/correlation IDs must match:

`^[A-Za-z0-9_-]{1,128}$`

This applies to:

- post_id
- voice_id
- quote voice_id
- generation_id
- job_id

Producer-generated IDs use the stricter canonical prefixes documented above.
The worker intentionally accepts existing safe legacy/probe IDs for compatibility.

## Canonical S3 paths

DEV:

`ghost/<post_id>/<content_hash>.html`

`voices/<voice_id>/reference.wav`

`generations/<generation_id>/output.wav`

`voice-tests/<voice_id>/<test_id>.wav`

Production:

`narrations/<post_id>/<voice_id>/<publication_key>.wav`

## Ownership

### Ghost
Owns source post identity, HTML, and Ghost-authored metadata.

### Ghost ingestion / sync
Owns NarrationPosts source-mirror fields and content_hash.

### Studio/App API
Owns Studio organizational data, NarrationVoices metadata, generation intent,
generation_id, job_id, and Studio-facing state.

### pocket-tts-dev worker
Owns DEV generated audio bytes and execution outcomes.
It has no production publication authority.

### Publisher
Exclusively owns production publication allocation, NarrationPublications writes,
and production narration S3 writes.

## Retry rule

Retries of an identical generation reuse:

- generation_id
- job_id
- input snapshot
- voice IDs
- settings
- output key

Any change to generation-affecting inputs creates a new generation_id and job_id.