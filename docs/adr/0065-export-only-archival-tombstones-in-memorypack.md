# Export only archival tombstones in MemoryPack

MemoryPack carries Archival Tombstones in a separate `archival_ledger` so an imported relationship preserves accepted archival identities, terminal outcomes, and no-memory decisions without treating them as memories. It does not export complete Archival Receipts, queue tasks, payloads, attempt history, retry timing, or failure summaries. Imported tombstones preserve their original `archival_id`, never enter recall or relationship projection, and are deleted with the relationship.
