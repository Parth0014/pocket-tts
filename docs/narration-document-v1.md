# Narration Document V1

## Purpose

Narration Document V1 is the canonical narration-content representation
produced from Ghost Content API `post.html`.

It is the single content source consumed by:

- Narration Studio / Production Manager frontend
- future Pocket TTS worker V2

Neither consumer independently parses Ghost HTML.

---

## Source identities

A document belongs to one exact Ghost source state.

Required document fields:

~~~json
{
  "schema_version": 1,
  "post_id": "<ghost-post-id>",
  "content_hash": "<64-lowercase-hex>",
  "narration_hash": "<64-lowercase-hex>",
  "processor_version": 1,
  "blocks": []
}
~~~

### schema_version

Exactly integer `1`.

This versions the Narration Document contract.

### post_id

Exact external Ghost post identity.

Consumers treat it as opaque.

### content_hash

Exactly:

`SHA256(exact UTF-8 Ghost post.html)`

represented as 64 lowercase hexadecimal characters.

### processor_version

Integer identifying the normalizer implementation/rules that produced
the document.

Changing processor code does not by itself require a different
`narration_hash`. The hash changes only when narration-semantic output
changes.

---

## Block identity

Blocks are ordered exactly as narration content appears in the Ghost
article.

Every block has:

~~~json
{
  "block_id": "b000001",
  "type": "paragraph",
  "role": "narration"
}
~~~

`block_id` is deterministic within one document and assigned after
normalization in document order:

- `b000001`
- `b000002`
- `b000003`
- ...

It is not a global identity.

---

## Supported block types

V1 supports:

- `paragraph`
- `heading`
- `quote`
- `list`
- `callout`
- `caption`
- `prompt`

Every block has a `role`.

Allowed V1 roles:

- `narration`
- `quote`

Only `quote` blocks use role `quote`.

All other V1 block types use role `narration`.

---

## Paragraph

~~~json
{
  "block_id": "b000001",
  "type": "paragraph",
  "role": "narration",
  "text": "Gratitude can change the way we experience ordinary moments."
}
~~~

Blank paragraphs are omitted.

---

## Heading

Ghost headings `h1` through `h6` are supported.

~~~json
{
  "block_id": "b000002",
  "type": "heading",
  "role": "narration",
  "level": 3,
  "text": "Practices That Helped Me Soften"
}
~~~

`level` is exactly an integer from 1 through 6.

---

## Quote

~~~json
{
  "block_id": "b000003",
  "type": "quote",
  "role": "quote",
  "text": "Gratitude has always been like the star which is guiding me.",
  "speaker": "Megha"
}
~~~

`speaker` is either a non-empty string or null.

For Ghost blockquotes, a final line beginning with one of:

- `~`
- `-`
- `–`
- `—`

may be extracted as attribution only when the remaining payload passes
the speaker-name validation rules.

An inline dash inside quote prose is not attribution.

The attribution is removed from `text` when successfully extracted.

After attribution extraction, surrounding presentation quote characters
(`"`, `'`, `“`, `”`, `‘`, and `’`) are removed from the beginning and end
of the complete quote text. Interior quotation marks remain part of the
content.

---

## List

Lists preserve list semantics rather than being treated as navigation
markup by default.

~~~json
{
  "block_id": "b000004",
  "type": "list",
  "role": "narration",
  "ordered": false,
  "items": [
    "Write this letter to offer the forgiveness you have withheld.",
    "Remind your younger self that they did their best."
  ]
}
~~~

`ordered` is boolean.

`items` is a non-empty ordered array of non-empty normalized strings.

Nested list items are flattened into the same `items` array in document
order.

V1 excludes a list as article navigation / table-of-contents UI only when
the entire list tree can be proven to contain same-document fragment
navigation.

For every direct list item at every nested list level:

- exactly one anchor must belong to that list level;
- its `href` must begin with `#`;
- the item's own visible text must be exactly the anchor's visible text; and
- every nested list must independently satisfy the same rule.

If any part is ambiguous, contains additional editorial text, uses a
non-fragment link, or contains a nested list that does not satisfy the rule,
the complete list is retained as narration content.

---

## Callout

Ghost `kg-callout-card` content is narration-relevant article content.

~~~json
{
  "block_id": "b000005",
  "type": "callout",
  "role": "narration",
  "text": "There are studies suggesting whether there are more or less basic emotions."
}
~~~

The decorative callout emoji is not spoken content.

---

## Caption

Image and gallery media themselves are not narration content.

A caption may be retained only when it contains substantive editorial
content rather than media-credit metadata.

~~~json
{
  "block_id": "b000006",
  "type": "caption",
  "role": "narration",
  "text": "Jadwiga shared photos of herself from 2019 to 2026, showing a beautiful transformation."
}
~~~

Captions consisting only of credit/source metadata such as:

`Image Credit`

are excluded.

V1 uses a deterministic, case-insensitive credit/source classifier.
A caption is considered a credit/source candidate when it begins with one
of these forms:

- `image`, `photo`, `photograph`, `picture`, `illustration`, or `graphic`
  followed by `credit`, `credits`, or `source`;
- `credit`, `credits`, or `source` followed by `:`, `-`, `–`, or `—`;
- `photo`, `image`, or `illustration` followed by `by`; or
- `courtesy of`.

After that prefix is removed:

- an empty remainder is excluded;
- a remainder containing sentence punctuation (`.`, `!`, or `?`) is
  retained;
- otherwise a remainder of at most eight whitespace-separated words is
  treated as short credit/source metadata and excluded;
- a longer remainder is retained conservatively as possible editorial
  content.

A `figcaption` may contribute narration when it is outside a Ghost card or
inside a `kg-image-card`. Captions belonging to other Ghost card types
remain silent with their parent card.

Image URLs, srcset values, dimensions, and media metadata are never
narration content.

---

## Prompt

A Ghost `kg-button-card` becomes narration only under the deterministic V1
prompt rule.

The visible button text first has surrounding presentation quote
characters (`"`, `'`, `“`, `”`, `‘`, and `’`) removed. It then becomes a
`prompt` only when:

1. the resulting text ends with `?`; and
2. the text does not match a V1 navigation/promotional CTA prefix.

A qualifying prompt becomes:

~~~json
{
  "block_id": "b000007",
  "type": "prompt",
  "role": "narration",
  "text": "What am I still carrying that isn't mine to carry?"
}
~~~

V1 CTA prefixes are case-insensitive:

- `continue reading`
- `read more`
- `learn more`
- `explore`
- `download`
- `subscribe`
- `sign up`
- `join now`
- `get started`
- `click here`
- `visit`
- `view more`

A button is treated as CTA text when its complete normalized text equals one
of those prefixes, or begins with one of them followed by a space or colon.

Navigation and promotional CTA button text such as:

`Continue Reading: Inspirational Gratitude Stories`

is excluded.

The destination URL is never spoken content.

---

## Silent Ghost structures

The following structures do not contribute narration blocks in V1:

- images
- galleries
- embed cards
- iframes
- bookmark cards
- file/download cards
- scripts
- styles
- standalone code elements
- forms
- player UI
- navigation
- footer content
- generic unknown Ghost card UI

Unknown `kg-card` types default to silence until explicitly supported by
a later processor rule.

---

## Inline markup

Inline formatting does not create separate blocks.

Examples:

- `strong`
- `em`
- `i`
- `b`
- `u`
- ordinary links
- inline code

Their visible textual content remains part of the containing supported
block unless a more specific exclusion rule applies.

URLs themselves are not inserted into narration text merely because
content is linked.

---

## Text normalization

Normalization happens after structural boundaries are identified.

V1:

- preserves semantic ordering
- prevents adjacent elements from joining words
- collapses insignificant whitespace
- omits blank blocks
- decodes HTML entities through HTML parsing
- does not invent missing text
- does not invent speakers

---

## Narration hash

`narration_hash` identifies the exact narration-semantic contract output.

The hash input is not the persisted Narration Document object verbatim.

The exact V1 hash payload contains only:

1. `schema_version`
2. the ordered semantic projection of `blocks`

Canonical hash payload:

~~~json
{
  "schema_version": 1,
  "blocks": [
    "... exact semantic block projections ..."
  ]
}
~~~

### Exact semantic block projection

`block_id` is deliberately excluded from every block before hashing.

The V1 projection for each block type is exactly:

#### paragraph

~~~json
{
  "type": "paragraph",
  "role": "narration",
  "text": "..."
}
~~~

#### heading

~~~json
{
  "type": "heading",
  "role": "narration",
  "level": 3,
  "text": "..."
}
~~~

#### quote

~~~json
{
  "type": "quote",
  "role": "quote",
  "text": "...",
  "speaker": "..."
}
~~~

`speaker` may be `null`.

#### list

~~~json
{
  "type": "list",
  "role": "narration",
  "ordered": false,
  "items": [
    "...",
    "..."
  ]
}
~~~

#### callout

~~~json
{
  "type": "callout",
  "role": "narration",
  "text": "..."
}
~~~

#### caption

~~~json
{
  "type": "caption",
  "role": "narration",
  "text": "..."
}
~~~

#### prompt

~~~json
{
  "type": "prompt",
  "role": "narration",
  "text": "..."
}
~~~

The V1 narration hash therefore includes:

- block `type`
- block `role`
- block order
- text
- quote `speaker`
- heading `level`
- list `ordered`
- list item content
- list item order

It explicitly excludes:

- `block_id`
- `post_id`
- `content_hash`
- `narration_hash`
- `processor_version`
- timestamps
- S3 bucket names
- S3 object keys
- storage metadata
- runtime metadata

`block_id` is deterministic document identity metadata, not narration-semantic content.

A processor implementation change that produces the same semantic projection therefore produces the same `narration_hash`.

Before SHA-256:

1. serialize the hash payload as JSON;
2. sort object keys;
3. use compact JSON separators;
4. preserve array order;
5. preserve Unicode rather than ASCII-escaping it;
6. encode the resulting JSON as UTF-8 bytes.

Conceptually:

`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`

then:

`SHA256(UTF-8 bytes)`

Therefore:

- identical semantic projections => identical `narration_hash`
- changed semantic projections => different `narration_hash`
- changed `block_id` only => identical `narration_hash`
- changed `post_id` only => identical `narration_hash`
- changed `content_hash` only => identical `narration_hash`
- changed `processor_version` only => identical `narration_hash`

---
## Generation policy is outside this document

Narration Document V1 contains source narration semantics.

It does not contain:

- voice selection
- quote voice selection
- quote mode
- generation ID
- job ID
- output location
- publication state

Those belong to generation/application contracts.

Quote policy remains:

- `preserve`
- `exclude`
- `two_voice`

and is applied after this document is loaded.

---

## Compatibility

The existing V1 HTML worker remains supported during migration.

Narration Document V1 is the source contract for future worker job
schema version 2.

The existing worker V1 contract and this document contract are different
version namespaces.

Changing Narration Document V1 field meaning, required fields, block
shapes, or hash semantics requires an explicit contract-version
decision.