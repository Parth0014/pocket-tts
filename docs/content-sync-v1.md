# Pocket TTS Content Sync V1

## Purpose

Content Sync creates a resumable, authoritative full-catalog mirror of
the currently published Ghost publication without changing the existing
production webhook/SQS semantics.

It performs five responsibilities:

1. enumerate the complete Ghost Content API published-post catalog;
2. archive exact immutable Ghost HTML snapshots in DEV S3;
3. normalize those snapshots into Narration Document V1;
4. maintain sync-owned catalog observations in `NarrationPosts`; and
5. maintain resumable synchronization state in `pocket-tts-app`.

Content Sync does not generate narration, publish audio, or enqueue the
existing production narration queue.

---

## Current Ghost publication prerequisite

The Phase 0G publication audit verified the current publication state:

- 714 Content API posts;
- 714 unique Ghost post IDs;
- all 714 posts have `visibility = public`;
- all 714 posts have `access = true`;
- all 714 posts expose non-empty HTML;
- all 714 posts expose non-empty plaintext;
- the Ghost post sitemap contains 714 unique post URLs;
- Content API and sitemap URL sets have exact parity.

This is an observed publication-state prerequisite, not a permanent
assumption about Ghost.

Content Sync therefore fails closed if a future full run encounters a
published post with unsupported access state.

---

## Ghost Content API request contract

Content Sync uses the Ghost Content API only.

It does not use:

- Ghost Admin API;
- theme/webpage scraping;
- browser-rendered page HTML; or
- sitemap HTML as narration source.

The full catalog is explicitly paginated.

V1 page size:

`100`

The request includes:

`authors`

`tags`

and rendered Ghost post HTML.

The exact `html` string returned for a post is the source byte sequence
for `content_hash` calculation after UTF-8 encoding.

Plaintext is not canonical narration input.

---

## Required post conditions

Every post processed as part of a successful V1 synchronization must
have:

- non-empty exact Ghost `id`;
- non-empty `title`;
- non-empty `slug`;
- non-empty `url`;
- non-empty `published_at`;
- non-empty `updated_at`;
- non-empty rendered `html`;
- `visibility = public`; and
- `access = true`.

If a post violates the V1 access requirement, the full synchronization
must not be declared authoritative.

A supported failure code is:

`UNSUPPORTED_GHOST_ACCESS`

No reconciliation occurs for such a failed run.

---

## Hashes

### Catalog content hash

For each Content API post:

`catalog_content_hash = SHA256(exact Ghost HTML encoded as UTF-8)`

The digest is lowercase 64-character hexadecimal.

This is the same hashing algorithm used by Narration Document V1.

### Production webhook content_hash

The existing `NarrationPosts.content_hash` field remains owned by the
production webhook.

Content Sync must never create, replace, remove, or reconcile that field.

This rule is mandatory because the webhook compares its incoming
`content_hash` to `NarrationPosts.content_hash` to decide whether to emit
`NEW_POST` or `CONTENT_CHANGED`.

Writing that field from Content Sync could suppress a required production
queue event.

---

## Production webhook behavior preserved

The existing webhook:

- hashes exact webhook Ghost HTML;
- reads the existing item by `post_id`;
- considers absent items new;
- considers a changed `content_hash` content-changed;
- refreshes Ghost metadata even when content is unchanged;
- writes `last_webhook_at`;
- writes `ghost_status = PUBLISHED`;
- preserves `first_seen_at` with `if_not_exists`;
- skips metadata fields whose value is `None`; and
- preserves unrelated DynamoDB attributes.

For NEW_POST and CONTENT_CHANGED, it performs:

`SQS SendMessage`

before:

`NarrationPosts UpdateItem`

Content Sync does not modify that ordering or production webhook code in
Phase 2.

---

## NarrationPosts ownership

### Shared identity / constants

The following fields may be written by both components only with the same
meaning:

`post_id`

`schema_version`

`source`

V1 values:

`schema_version = 1`

`source = GHOST`

### Shared write-once field

`first_seen_at`

Both webhook and Content Sync use write-once semantics equivalent to:

`if_not_exists(first_seen_at, now)`

Neither component overwrites an existing `first_seen_at`.

### Webhook-owned fields

Content Sync must never write or remove:

`content_hash`

`ghost_status`

`last_webhook_at`

### Shared Ghost display metadata

Content Sync may initialize or refresh:

`title`

`slug`

`url`

`excerpt`

`feature_image`

`published_at`

`updated_at`

A sync metadata refresh must not overwrite a clearly newer Ghost metadata
observation already stored on the item.

When `updated_at` already exists, the sync refresh is permitted only when
the incoming Ghost `updated_at` is equal to or newer than the stored
value.

The Content API and webhook timestamps use Ghost UTC timestamp strings.
Phase 2 does not change the existing production webhook to add its own
stale-event condition.

### Sync-owned fields

Content Sync owns:

`catalog_content_hash`

`catalog_updated_at`

`catalog_status`

`visibility`

`access`

`authors`

`tags`

`last_seen_sync_id`

`last_seen_at`

`last_reconciled_sync_id`

`last_reconciled_at`

The production webhook does not depend on these fields.

---

## Sync-owned catalog field semantics

### catalog_content_hash

Exact SHA-256 of the current Content API HTML observed by this sync.

This field is separate from webhook-owned `content_hash`.

### catalog_updated_at

The Ghost `updated_at` belonging to the observation represented by
`catalog_content_hash`.

### catalog_status

Allowed V1 values:

`PUBLISHED`

`NOT_IN_PUBLISHED_CATALOG`

A post encountered in the successful crawl receives:

`PUBLISHED`

A previously known Ghost source item that is safely reconciled as absent
receives:

`NOT_IN_PUBLISHED_CATALOG`

Absence is deliberately not named `DELETED` or `UNPUBLISHED` because the
public Content API alone does not prove the exact reason for absence.

### visibility

Current Content API visibility value.

V1 authoritative completion currently requires:

`public`

### access

Current Content API access boolean.

V1 authoritative completion currently requires:

`true`

### authors

Ordered Ghost authors from the Content API.

Each retained author record contains only narration/catalog-relevant
metadata such as:

`id`

`name`

`slug`

Null optional values are omitted.

### tags

Ordered Ghost tags from the Content API.

Each retained tag record contains only narration/catalog-relevant
metadata such as:

`id`

`name`

`slug`

Null optional values are omitted.

### last_seen_sync_id

The logical full-sync ID in which this post was durably processed.

Format:

`sync_<uuid4hex>`

### last_seen_at

UTC RFC 3339 time at which the sync durably completed this post
observation.

---

## Immutable DEV S3 source archive

Bucket:

`pocket-tts-dev-test`

Exact Ghost HTML snapshot key:

`ghost/<post_id>/<content_hash>.html`

For Content Sync, `<content_hash>` in this key is the computed
`catalog_content_hash` for the exact Content API HTML.

The object is content-addressed and immutable.

A retry must not intentionally overwrite an object at the same key.

The writer uses conditional create semantics.

If the object already exists, the retry treats it as an existing
immutable snapshot after verifying expected object metadata.

No Ghost Content API key is stored in the object or its metadata.

---

## Immutable Narration Document archive

The exact Content API HTML is normalized once with the shared
`normalize_ghost_html()` implementation.

Narration Document V1 is then built and validated with the shared
canonical package.

Object key:

`narration-documents/<post_id>/<content_hash>/<narration_hash>.json`

The document bytes are canonical UTF-8 JSON.

The object is immutable and uses conditional create semantics.

A retry does not overwrite an existing object at the same
content-addressed key.

The frontend and future TTS V2 consume the same Narration Document
semantics. They do not independently parse Ghost HTML.

---

## Per-post durable processing order

A post is not marked seen for the current sync until its canonical source
artifacts are durable.

Required order:

1. validate required Ghost fields and access state;
2. compute exact `catalog_content_hash`;
3. normalize Ghost HTML;
4. build and validate Narration Document V1;
5. conditionally create or verify immutable raw Ghost HTML in DEV S3;
6. conditionally create or verify immutable Narration Document JSON;
7. update sync-owned `NarrationPosts` catalog observation;
8. set `last_seen_sync_id` and `last_seen_at`;
9. optionally refresh shared Ghost display metadata under the freshness
   guard.

A crash after S3 creation but before DynamoDB observation is safe.

A retry recreates no mutable source object and repeats the idempotent
DynamoDB observation.

---

## Page checkpoint rule

The synchronization state item is:

`pk = SYSTEM#GHOST_SYNC`

`sk = CURRENT`

A new run receives:

`sync_<uuid4hex>`

and starts:

`status = RUNNING`

`next_page = 1`

The first successful page records the Content API pagination totals used
by the run:

`expected_total`

`expected_pages`

`next_page` means the next first-pass Content API page not yet durably
processed.

A page checkpoint advances only after every post from that page has
completed the per-post durable processing order.

The checkpoint update must condition on the expected:

`sync_id`

`status = RUNNING`

`next_page`

If the invocation no longer owns that exact state, it must not advance
the checkpoint.

---

## Failure and resume

Allowed synchronization statuses remain:

`RUNNING`

`FAILED`

`COMPLETE`

A processing failure records a bounded non-secret `last_error_code` and
retains the current `next_page`.

Raw exception payloads, API keys, authorization headers, and Ghost
response bodies are not persisted in the synchronization item.

Resuming a failed run keeps the same:

`sync_id`

`expected_total`

`expected_pages`

`next_page`

Resume transitions the same logical run back to RUNNING using a
conditional update.

Starting an unrelated new run while the CURRENT item is RUNNING is
forbidden.

A FAILED run is not authoritative and cannot trigger absence
reconciliation.

---

## Verification crawl

Completing the first page pass is not sufficient to declare the catalog
authoritative.

After the final first-pass page succeeds, the checkpoint advances to:

`next_page = expected_pages + 1`

That value means the first pass is durable and the run requires
verification/reconciliation.

The sync then performs a verification crawl of the current Content API
catalog.

For every verification-crawl post, it recomputes the exact HTML SHA-256
and verifies that the corresponding `NarrationPosts` item has:

- `last_seen_sync_id` equal to the current `sync_id`;
- identical `catalog_content_hash`;
- identical `catalog_updated_at`;
- supported `visibility`; and
- `access = true`.

The verification crawl must also have the same total number of unique
post IDs as the first pass.

Duplicate IDs are a failure.

A new post, removed post, content change, timestamp change, unsupported
access change, or other catalog-set difference detected between the two
passes fails the run with:

`CATALOG_CHANGED_DURING_SYNC`

A failed verification run does not reconcile absent posts.

A later resume may rerun verification from the already durable
first-pass state.

Immutable S3 artifacts created by the failed run remain valid historical
content-addressed snapshots.

---

## Reconciliation

Absence reconciliation begins only after a successful verification crawl.

Reconciliation applies only to `NarrationPosts` items whose:

`source = GHOST`

Items without that source marker are outside V1 full-sync absence
reconciliation.

This prevents unrelated/synthetic legacy records from being
reclassified merely because they do not appear in Ghost.

For a `source = GHOST` item whose:

`last_seen_sync_id != current sync_id`

Content Sync may mark:

`catalog_status = NOT_IN_PUBLISHED_CATALOG`

and write:

`last_reconciled_sync_id`

`last_reconciled_at`

However, it must not mark the item absent if a production webhook has
observed it since this full synchronization began.

At minimum, absence reconciliation must skip an item when:

`last_webhook_at >= sync started_at`

This prevents a post published through the realtime webhook during the
full crawl from being immediately classified as absent by an older
catalog snapshot.

Reconciliation never changes:

`content_hash`

`ghost_status`

`last_webhook_at`

`first_seen_at`

and never deletes a `NarrationPosts` item.

---

## Completion

Only after:

1. every first-pass page is durable;
2. the verification crawl succeeds; and
3. reconciliation succeeds

may the CURRENT synchronization item become:

`status = COMPLETE`

Completion:

- writes `completed_at`;
- writes `updated_at`;
- removes `next_page`;
- removes `last_error_code` if present; and
- preserves the completed `sync_id`.

A synchronization that times out or fails before all three conditions
remains non-authoritative.

---

## No production queue side effects

Content Sync must not send messages to:

`narration-jobs-queue`

or any production narration-generation queue.

It must not call the production publisher.

It must not generate audio.

It must not modify production narration objects.

Its purpose is catalog synchronization and canonical source preparation
only.

---

## Initial-state behavior

Before the first real synchronization:

`pocket-tts-app` contains zero items.

The DEV prefixes:

`ghost/`

and:

`narration-documents/`

contain no objects.

The current `NarrationPosts` baseline contains three legacy/synthetic
items.

The first Content Sync run therefore performs a real catalog bootstrap
rather than migrating an existing canonical archive.

---

## V1 non-goals

Phase 2 does not:

- change the production webhook implementation;
- change the production webhook event schema;
- change the production SQS contract;
- enable DynamoDB Streams;
- create a GSI or LSI;
- enable PITR;
- enable TTL;
- create a customer-managed KMS key;
- create or publish narration audio;
- create Studio generations; or
- expose an HTTP frontend API.

Those belong to later phases.