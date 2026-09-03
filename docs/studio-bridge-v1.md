# Production Ingestion → Studio Bridge V1

Status: internal manager-intake contract.

## Purpose

The bridge consumes Production Ingestion Event V1 from the existing Standard
SQS queue and translates a current published Ghost content state into
idempotent Studio manager-intake state.

The event is a pointer, not an authoritative Ghost document.

## Authoritative verification

For each event the bridge:

1. validates the exact V1 event fields and values;
2. re-fetches the published post from Ghost Content API by exact `post_id`;
3. computes `SHA-256(exact UTF-8 Ghost HTML)`;
4. compares that current hash with the event `content_hash`;
5. treats a mismatch as a stale Standard-SQS event and performs no Studio
   state mutation for that event;
6. normalizes current Ghost HTML through Narration Document V1;
7. validates the canonical Narration Document;
8. conditionally creates or verifies the immutable DEV raw/document artifacts;
9. records bridge-owned manager-intake state.

## Idempotency

Semantic event identity is:

`post_id + content_hash`

Bridge receipt key:

`pk = POST#<post_id>`

`sk = BRIDGE#<content_hash>`

Current manager-intake pointer:

`pk = POST#<post_id>`

`sk = BRIDGE#CURRENT`

Duplicate delivery of the same pair verifies/reuses the existing receipt.
It never creates a second receipt for the same pair.

A later current content hash creates a new immutable receipt and advances the
current pointer only after Ghost re-verification.

## Deliberately outside the bridge

The bridge does not:

- choose a voice;
- choose quote mode;
- create a Studio room;
- create a generation_id or job_id;
- send the Studio FIFO;
- invoke TTS;
- select or READY a generation;
- allocate a publication version;
- write NarrationPublications;
- write production audio.

Those actions remain owned by later Production Manager / Studio / Publisher
phases.

## AWS boundary

The production ingestion queue remains Standard SQS.

The initial event-source mapping is deployed Disabled. Direct smoke testing is
used before any production queue consumption is enabled.

The bridge may read its Ghost Content API key from the existing SSM Standard
SecureString, write only canonical artifacts to DEV S3, and write only
bridge-owned intake items in `pocket-tts-app`.

It receives no production S3 permission and no Studio worker FIFO
`SendMessage` permission.
