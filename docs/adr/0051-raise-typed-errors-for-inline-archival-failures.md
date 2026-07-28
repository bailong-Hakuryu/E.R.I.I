# Raise typed errors for inline archival failures

Inline archival records a terminal Failed Archival Receipt and then raises a typed `ArchivalProcessingError` carrying that sanitized receipt; it never reports failure only through a return value or log entry that existing callers may ignore. Queued archival cannot raise a later worker failure to the original call, so hosts observe the same failure model by querying its receipt, while neither path exposes conversation text, raw model output, prompts, or secrets through the error surface.
