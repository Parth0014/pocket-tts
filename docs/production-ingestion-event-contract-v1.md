# Production Ingestion Event Contract V1

## Purpose

This document freezes the message contract emitted by
`narration-webhook-handler` to the production
`narration-jobs-queue`.

The future `narration-studio-bridge` is the consumer of this contract.

This event means:

> A published Ghost post has narration-relevant content that is new or
> has changed.

It does not mean that narration should automatically be generated or
published.

---

## Producer

Producer:

`narration-webhook-handler`

Source of truth:

Ghost webhook payload.

The producer only emits this event for published posts whose exact HTML
content is new or has changed relative to `NarrationPosts`.

---

## V1 message body

A V1 message contains exactly these top-level fields:

~~~json
{
  "schema_version": 1,
  "post_id": "<ghost-post-id>",
  "content_hash": "<sha256-lowercase-hex>",
  "reason": "NEW_POST"
}
~~~

Allowed fields:

- `schema_version`
- `post_id`
- `content_hash`
- `reason`

No additional fields are part of V1.

---

## schema_version

`schema_version` is the event contract version.

For V1 its value is the integer:

`1`

It is not a publication version, generation version, post revision, or
business-state version.

---

## post_id

`post_id` is the exact external Ghost post identity.

Ghost owns this identifier.

Consumers must treat it as opaque identity and must not derive another
post identity from title, slug, URL, or other Ghost metadata.

---

## content_hash

`content_hash` is:

SHA-256(exact UTF-8 Ghost HTML)

It is represented as exactly 64 lowercase hexadecimal characters.

The hash identifies the narration-relevant Ghost content snapshot.

A metadata-only change that does not change the exact HTML does not
create another narration-needed event.

---

## reason

Allowed V1 values are exactly:

- `NEW_POST`
- `CONTENT_CHANGED`

`NEW_POST` means the producer had no existing `NarrationPosts` record
for the Ghost post.

`CONTENT_CHANGED` means a prior record existed and its stored
`content_hash` differs from the newly calculated hash.

`reason` describes why the producer emitted the event.

It is not event identity and consumers must not use it as an
idempotency key.

---

## Idempotency

The semantic identity of a narration-needed content state is:

`post_id + content_hash`

The future `narration-studio-bridge` must therefore be idempotent on
that pair.

Duplicate delivery of the same `post_id + content_hash` must not create
duplicate Studio rooms or duplicate narration intents.

A different `content_hash` for the same `post_id` represents a new
Ghost content state.

---

## Delivery semantics

The production queue is Standard SQS and consumers must assume
at-least-once delivery.

The producer intentionally sends the SQS message before persisting the
new Ghost state to `NarrationPosts`.

This failure preference is deliberate:

duplicate delivery is acceptable;
silently losing a narration-needed event is not.

Consumers must therefore be idempotent.

---

## Data not carried in V1

The ingestion event does not contain:

- Ghost HTML
- title
- slug
- URL
- excerpt
- feature image
- author/person metadata
- voice selection
- quote mode
- Studio folder/person assignment
- generation ID
- job ID
- publication version
- production audio location

Those values belong to their respective source systems.

The future bridge obtains Ghost/catalog state from `NarrationPosts`
and/or the approved Ghost Content API flow rather than treating SQS as
a complete Ghost document.

---

## Ownership

Ghost owns:

- post identity
- source HTML
- Ghost metadata

`narration-webhook-handler` owns:

- exact HTML hashing
- change detection
- ingestion event production
- production `NarrationPosts` Ghost-state updates

`narration-studio-bridge` owns:

- idempotent translation of a production ingestion event into Studio
  state

Studio/App API owns:

- Studio organization
- narration intent
- voices
- generations

The TTS worker does not consume this production event contract.

The Publisher does not consume this production event contract.

---

## Compatibility rule

V1 consumers must implement the exact V1 field and value semantics
defined here.

Adding, removing, renaming, or changing the meaning of an event field
requires an explicit contract-version decision rather than silently
changing V1.