# Report archival artifacts through a sanitized manifest

A full Archival Receipt carries an immutable Artifact Manifest containing only each committed artifact's stable type and ID, with artifact counts derived from that manifest rather than stored independently. The manifest contains no memory text, scores, embeddings, mutable storage paths, model output, or conversation payload. It lets hosts identify exactly what one atomic Archival Batch created, while receipt compaction clears both the manifest and its derived counts; the long-term artifacts continue to retain `source_archival_id`.
