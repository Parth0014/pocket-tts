# Pocket TTS App Table V1

## Purpose

`pocket-tts-app` is the internal application-state DynamoDB table for
Narration Studio and Production Manager orchestration.

It does not replace the existing production `NarrationPosts`,
`NarrationVoices`, or `NarrationPublications` tables.

The first production use of this table is the resumable Ghost full-sync
checkpoint. ROOM, DOC, and GEN key namespaces are reserved here and their
full attribute contracts are defined in later phases.

---

## Table contract

Table name:

`pocket-tts-app`

Primary key:

- partition key: `pk`, String
- sort key: `sk`, String

Configuration:

- billing mode: `PAY_PER_REQUEST`
- table class: `STANDARD`
- deletion protection: enabled
- encryption: DynamoDB default encryption at rest
- no customer-managed KMS key
- point-in-time recovery: disabled initially
- DynamoDB Streams: disabled
- TTL: disabled initially
- global secondary indexes: none
- local secondary indexes: none
- global-table replication: none

No optional paid backup, stream, index, or customer-managed encryption
feature is required for V1.

---

## Verified AWS deployment

The V1 table was created and independently verified in `us-east-1` on
2026-09-03.

Observed table state:

- table name: `pocket-tts-app`
- status: `ACTIVE`
- partition key: `pk`, String, HASH
- sort key: `sk`, String, RANGE
- billing mode: `PAY_PER_REQUEST`
- table class: `STANDARD`
- deletion protection: enabled
- global secondary indexes: none
- local secondary indexes: none
- DynamoDB Streams: disabled
- global-table replicas: none
- encryption: DynamoDB default AWS-owned encryption
- account KMS key association: none
- point-in-time recovery: disabled
- TTL: disabled
- initial item count: zero

The table was intentionally left empty after creation.

In particular, the following synchronization item was not pre-seeded:

`SYSTEM#GHOST_SYNC / CURRENT`

The Content Sync implementation creates synchronization state only when a
real full-catalog synchronization begins.

No synthetic `sync_id`, ROOM, DOC, GEN, or other application-state item is
inserted during table provisioning.
---

## Reserved item namespaces

### Ghost sync state

`pk = SYSTEM#GHOST_SYNC`

`sk = CURRENT`

### Studio room

`pk = POST#<post_id>`

`sk = ROOM`

### Canonical Narration Document

`pk = POST#<post_id>`

`sk = DOC#<content_hash>#<narration_hash>`

### Generation

`pk = POST#<post_id>`

`sk = GEN#<generation_id>`

Ghost `post_id` remains the exact opaque Ghost identifier.

`content_hash` and `narration_hash` are lowercase 64-character SHA-256
hexadecimal strings governed by Narration Document V1.

Generation identifiers use the existing canonical form:

`gen_<uuid4hex>`

The key namespace does not place voices in this table. Shared voice
registry identity remains in `NarrationVoices`.

---

## Ghost sync CURRENT item

Example RUNNING item:

~~~json
{
  "pk": "SYSTEM#GHOST_SYNC",
  "sk": "CURRENT",
  "entity_type": "ghost_sync_state",
  "schema_version": 1,
  "sync_id": "sync_<uuid4hex>",
  "status": "RUNNING",
  "next_page": 1,
  "started_at": "2026-09-03T00:00:00Z",
  "updated_at": "2026-09-03T00:00:00Z"
}
~~~

### Required identity fields

`pk`

Must equal:

`SYSTEM#GHOST_SYNC`

`sk`

Must equal:

`CURRENT`

`entity_type`

Must equal:

`ghost_sync_state`

`schema_version`

Must equal integer:

`1`

`sync_id`

One logical full-catalog synchronization run.

Format:

`sync_<uuid4hex>`

A resumed execution keeps the same `sync_id`.

A newly started full synchronization receives a new `sync_id`.

---

## Sync status

Allowed V1 values:

`RUNNING`

`FAILED`

`COMPLETE`

There is no separate `IDLE` item state.

Before the first run, the item may simply not exist.

---

## Pagination checkpoint

`next_page` is the next Ghost Content API page that has not yet been
durably processed.

A new synchronization begins with:

`next_page = 1`

The checkpoint is advanced only after the complete current page has been:

1. fetched successfully;
2. validated;
3. normalized or catalogued as required;
4. durably persisted; and
5. associated with the current `sync_id`.

For RUNNING and FAILED states, `next_page` remains present so work can
resume without returning to page 1.

For COMPLETE state, `next_page` is removed.

---

## Timestamps

All application timestamps are UTC RFC 3339 strings using `Z`.

RUNNING requires:

`started_at`

`updated_at`

FAILED requires:

`started_at`

`updated_at`

COMPLETE requires:

`started_at`

`updated_at`

`completed_at`

A failed state may contain a bounded non-secret:

`last_error_code`

Raw API responses, Content API keys, webhook tokens, authorization
headers, session secrets, and arbitrary exception payloads must not be
stored in this item.

---

## Concurrency and stale-execution rule

Checkpoint advancement must be conditional on the invocation still owning
the expected logical state.

At minimum, a page-advance write must verify the expected:

`sync_id`

`status`

`next_page`

A delayed invocation from another run must not be able to advance or
complete the current synchronization.

Exact DynamoDB ConditionExpressions are implementation details frozen in
the Content Sync phase.

---

## Reconciliation invariant

Each successfully processed catalog post records the current:

`last_seen_sync_id`

Reconciliation of posts not seen in the current full synchronization may
occur only after every page of that synchronization has completed
successfully.

A partial, timed-out, or FAILED synchronization must not reconcile unseen
posts as stale, removed, or unpublished.

Therefore:

`FAILED != authoritative complete catalog`

and:

`COMPLETE == eligible for reconciliation`

This rule is mandatory.

---

## Cross-table boundary

The sync checkpoint belongs in `pocket-tts-app`.

The existing `NarrationPosts` table remains the production-side local
Ghost catalog.

Phase 2 defines the exact metadata fields added or updated on
`NarrationPosts`, including `last_seen_sync_id`.

Phase 3 defines complete ROOM, DOC, and GEN item attribute contracts.

---

## V1 exclusions

This table design does not enable:

- PITR
- Streams
- TTL
- GSIs
- LSIs
- global tables
- customer-managed KMS keys
- DynamoDB autoscaling
- provisioned capacity

A later requirement must justify enabling any of those features.