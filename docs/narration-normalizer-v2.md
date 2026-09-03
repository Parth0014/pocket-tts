# Narration Normalizer Processor V2

Processor V2 adds conservative trailing-site-footer trimming while keeping the
Narration Document schema at V1.

The rule is suffix-only. It removes only a contiguous recognized footer suffix,
so similar language inside the editorial body remains narration.

Recognized terminal patterns currently include exact `Share this story`,
`Every story is a reminder that ...`, and `I would love to hear your story ...`
when the latter is clearly a contact solicitation.

Because affected semantic narration projections change, `PROCESSOR_VERSION`
advances from 1 to 2. Existing p000001 documents remain immutable historical
artifacts; new/reprocessed documents use p000002.

Bridge ingestion identity remains `post_id + content_hash`. Production Manager
adoption additionally compares narration hash and processor version so a
processor-only correction can become a new Studio document revision.
