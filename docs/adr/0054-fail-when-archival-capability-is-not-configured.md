# Fail when archival capability is not configured

An Engine may be constructed without a Memory Extractor Capability for relationship, recall, temporal, and portability use, but `remember()` then raises a typed `ArchivalCapabilityError` before accepting a task and the REST boundary reports service unavailability. The production default no longer writes the Dummy `"Interaction logged"` timeline or reports `no_memory`, because no extraction occurred; tests that require a deliberate zero-artifact completion must inject an explicit extractor returning the valid `kind=no_memory` decision.
