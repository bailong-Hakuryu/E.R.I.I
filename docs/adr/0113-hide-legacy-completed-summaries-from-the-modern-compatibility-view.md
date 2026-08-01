---
status: accepted
---

# Hide Legacy completed summaries from the modern compatibility view

An a7 Turn may contain a summary-only `ReplyContinuityAssessment` with
`status=completed` and an `aligned` verdict but no Findings, exact reply binding,
resolvable evidence, evaluator descriptor or aggregation-policy receipt. a8
truthfully migrates it into `ContinuityReviewRecord(kind=legacy_unavailable)`;
the historical summary is not deleted or rewritten.

The deprecated `TurnRecord.continuity_assessment` compatibility view returns a
derived assessment for modern `reviewed`, `not_evaluated` and `failed` records,
but returns `None` for `legacy_unavailable`. A caller must explicitly inspect
`ContinuityReviewRecord.legacy_summary` to read the old result and can therefore
label it as an unauditable historical judgment. The Legacy summary remains in
FileStorage, SQLiteStorage, MemoryPack, export and front-end inspection.

The kernel does not convert the old result to `not_evaluated`, because that
would deny that an older evaluator ran. It also does not return the old
`completed` value through the modern compatibility property, because callers
could mistake it for an a8 success Receipt and grant authority it never earned.

## Consequences

Migration preserves historical information while failing closed at the legacy
API seam. Existing callers that treated any completed summary as proof of a
modern review must adopt `ContinuityReviewRecord`; no compatibility projection
can authorize continuity, persona, relationship or memory writes from Legacy
data.
