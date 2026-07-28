# Query full receipts and tombstones through one interface

The same scoped receipt lookup returns both full Archival Receipts and imported or locally compacted Archival Tombstones, distinguished by `retention_state: full | compacted`. A compacted result retains only its archival identity, scope, terminal status, outcome code, acceptance time, and terminal time; cleared attempt details, failure summary, and artifact counts are `null`, never fabricated as zero. Only a scope mismatch, unknown identity, or deleted relationship is reported as not found.
