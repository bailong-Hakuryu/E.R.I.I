---
status: accepted
---

# Freeze the Delivery Exception audit vocabulary

`DeliveryExceptionRecord` explains why a host explicitly displayed a reply
outside the normal Continuity Delivery Gate. It does not judge whether the
reply was kind, compliant, angry, rejecting or harmful, and it does not repeat
why continuity evaluation was absent or failed.

Version 1 uses a closed `actor_kind` vocabulary:

- `host_policy` identifies a deterministic configured host policy;
- `human_operator` identifies an out-of-band human operator; and
- `data_owner` identifies the host-declared owner of the isolated relationship
  data.

The opaque stable `actor_id` identifies the relevant policy or host principal.
The record does not prove that E.R.I.I. authenticated or authorized that
identity. An Agent, evaluator or claim made inside a chat message cannot be an
exception actor.

Version 1 uses a closed `reason_code` vocabulary:

- `availability_fallback` preserves interaction availability;
- `configured_delivery_policy` applies an explicit host product policy;
- `out_of_band_judgment` records an explicit decision outside the chat;
- `preexisting_visible_exchange` imports an exchange that was already visible
  before `record_turn()` could persist it; and
- `legacy_turn_completion` explicitly completes a Legacy open Turn that has no
  original Turn Context Baseline.

`overridden` permits only `availability_fallback`,
`configured_delivery_policy` and `out_of_band_judgment`, because it requires a
completed evaluation of the same delivered text. `shown_unreviewed` permits all
five reasons. `configured_delivery_policy` requires actor kind `host_policy`;
`out_of_band_judgment` requires `human_operator` or `data_owner`. Other reasons
may be declared by any of the three actor kinds, subject to the host's later
authorization boundary. Ordinary `shown` rejects every Delivery Exception.

The `ContinuityReviewRecord` separately preserves technical classifications
such as an unconfigured evaluator, evaluator failure, invalidated authority or
a missing Legacy context baseline. A technical review cause never substitutes
for the host's delivery reason, and the delivery reason never fabricates a
review result. No migration may synthesize `legacy_turn_completion`; it must
come from an explicit completion operation.

Unknown actor kinds, reason codes and illegal actor, reason or disposition
combinations fail before persistence or MemoryPack import. Extensions require
a new supported record version rather than free-text or host-specific enum
values.

## Consequences

Portable Turn data can distinguish who declared a delivery exception, why the
reply was displayed and why formal review was unavailable without turning the
audit vocabulary into a moral classifier. Hosts retain product freedom while
old MemoryPacks keep stable, deterministic meanings.
