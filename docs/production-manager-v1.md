# Production Manager V1

The Production Manager is an authenticated operator-control surface between
Studio Bridge intake and TTS/publication.

## Fast-track scope

Implemented together:

- manager intake list and detail;
- immutable Bridge revision history;
- explicit adoption of current intake into a Studio room/document;
- explicit voice + quote-mode generation intent;
- generation history;
- review controls: SELECTED, READY, OUTDATED;
- stale-content checks before selection/READY;
- Publisher dry-run preflight;
- next publication-key calculation;
- production audio target calculation.

## Hard safety boundary

The Manager never sends the Studio FIFO.

Creating a generation creates only Studio intent. `generation_status` remains
absent until the existing App API enqueue endpoint is explicitly called.

The Manager never writes `NarrationPublications` and never writes the
production S3 bucket.

Publisher preflight reads completed DEV output and current metadata only. It
returns a proposed immutable production target and `production_write_performed
= false`.

## Manager intake index

Studio Bridge maintains:

- `pk = MANAGER#INTAKE`
- `sk = POST#<post_id>`

The item mirrors current verified bridge intake fields for efficient Manager
listing.

Authoritative detail remains:

- `pk = POST#<post_id>`
- `sk = BRIDGE#CURRENT`

Immutable intake receipts remain:

- `pk = POST#<post_id>`
- `sk = BRIDGE#<content_hash>`

## Adoption

A post maps idempotently to one Manager-owned Studio room and document ID.
A changed verified content hash becomes the next Studio document revision.

No generation is created by Bridge delivery or adoption.

## Review

Execution and review remain separate.

SELECTED and READY require COMPLETED execution and current content. READY also
requires SELECTED first and an ACTIVE voice.

OUTDATED may be applied without changing execution status.

## Publication

Publisher remains the only future production writer.

Production key contract:

`narrations/<post_id>/<voice_id>/<publication_key>.wav`

`publication_key` is six-digit monotonic `v000001`, `v000002`, ... scoped to
`post_id` and never reused.
