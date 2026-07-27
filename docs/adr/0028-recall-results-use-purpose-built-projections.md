# Recall results use purpose-built projections

Recall Result is composed from immutable, purpose-built recall projections rather than exposing `MemoryNode`, `RelationshipSnapshot`, `RelationshipEvent`, or adjudication records directly. The projections preserve stable source identity, provenance, time, visibility, and selection reasons while excluding mutable storage state and unrelated audit data, allowing storage and domain schemas to evolve without making Renderers depend on internal representations.
