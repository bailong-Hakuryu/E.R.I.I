---
status: accepted
---

# Use strict versioned codecs for a8 portable records

a8 adds audit records whose identity and meaning must survive FileStorage,
SQLiteStorage and MemoryPack transfer. Permissive `dict.get()` defaults,
unknown-field tolerance and scalar coercion would let a damaged modern record
silently look like Legacy data or a different review branch.

Every new a8 portable object therefore has a strict, independently versioned
wire codec. Unknown versions, fields, enums and illegal union combinations fail
before persistence or import; only an explicit Legacy parser may apply old
defaults. Portable identities use domain-separated canonical UTF-8 JSON and
SHA-256 without Unicode normalization, while set-valued references have a
canonical order and reject duplicates.

Runtime models may share validation helpers, but they do not decide migration
semantics implicitly. SQLite continues storing complete JSON payloads, so this
decision requires no physical column migration. The executable field and
fingerprint contract is recorded in `docs/a8-implementation-contract.md`.

## Consequences

Modern corruption and unknown future formats fail closed instead of being
downgraded, and portable fingerprints are deterministic across adapters. New
fields require a deliberate version or migrator rather than a convenient
parser default.
