## Summary

Describe the observable change and its owning layer: Core, Host Integration,
Adapter, or Labs.

## Evidence

- Linked issue or design decision:
- Full commit SHA used for verification:
- Commands and results:

Use synthetic fixtures and reproductions. Do not commit real chats, private
persona data, a production database, API keys, access tokens, or copyrighted
source material.

## Review checklist

- [ ] Tests added or updated for the changed behavior.
- [ ] Documentation updated when public behavior or setup changed.
- [ ] Contract snapshots updated when a frozen interface or data format
      intentionally changed.
- [ ] Existing `(agent_id, user_id)` relationship isolation remains intact,
      or the deliberate change is documented and tested.
- [ ] Lifecycle behavior follows the documented
      `record_turn → archive/process → recall → export` path.
- [ ] Failure, retry, restart, and migration behavior are covered where
      relevant.
- [ ] Test data is synthetic; no private persona data, real chats, production
      database, or API key is present.
- [ ] `python scripts/check_docs.py` passes.

## Compatibility

State Python-version, storage, API, schema, and MemoryPack consequences. If
there are none, write “none” rather than leaving this section blank.
