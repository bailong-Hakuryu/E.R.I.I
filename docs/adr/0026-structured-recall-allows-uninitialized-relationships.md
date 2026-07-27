# Structured recall allows uninitialized relationships

`recall_structured()` returns an explicitly marked legacy-only Recall Result when an Agent × User relationship has not been initialized, rather than failing or inventing a default persona. This keeps existing `remember()` and `recall()` integrations usable while preserving the rule that Character Blueprint and Persona Instance creation require an explicit host choice; relationship snapshots, reflections, and relationship-derived signals remain absent until initialization.
