---
status: accepted
---

# Authorize continuity-exception resolution with host-issued capabilities

E.R.I.I. runs both as a trusted local library for one data owner and as a
potential future service with authentication, tenants and delegated roles.
Hard-coding platform roles such as "user" or "administrator" into the memory
kernel would either burden local use or be too weak for a service. Treating a
free-form actor ID as proof of authority would falsely claim that authentication
already exists.

Exception-resolution operations therefore require a scoped Resolution
Authority Capability supplied by the host outside the conversation:

- `continuity_review_requester` may request an otherwise eligible technical
  retrospective review against the frozen Turn context.
- `persona_reviewer` may accept or reject a complete evidence-bound result for
  an eligible `review_required` case. It cannot edit the reply, Findings,
  evidence or Persona content while approving it.
- `relationship_reviewer` may decide whether an exceptional utterance caused a
  consequence in the one bound `Agent x User` relationship. It has no authority
  over Character Blueprint or another relationship.
- `continuity_correction_authority` may initiate the narrower Historical
  Continuity Correction path for a demonstrable evaluator, aggregation-policy
  or authority-decision defect.

An Agent, generative model, evaluator and arbitrary chat message cannot issue,
delegate or satisfy any of these capabilities. Statements such as "I allow you
to change" remain conversation evidence, not authorization. The host may expose
an authenticated front-end or CLI action, but approval is a control-plane
operation rather than role-play content.

One person may hold several capabilities in a trusted personal installation.
The imported-character owner or local data owner can therefore review their own
Persona Instance and relationship without a multi-person ceremony. A formal
service may separate these capabilities, require stronger review for historical
correction, or prohibit combinations according to its security policy without
changing the kernel's domain operations.

The current kernel validates that the declared capability type and scope match
the requested operation and persists a bounded actor claim, decision time and
capability type. This is audit semantics, not authentication. A bare actor ID,
capability string or relationship ID does not prove identity, tenant membership
or permission. Until the host implements authentication and authorization, a
resolution Interface is suitable only behind a trusted local or application
boundary.

## Consequences

The domain model supports both self-hosted use and future least-privilege
services. Authorization policy can become stricter without changing the meaning
of historical decisions. Documentation and APIs must not advertise actor claims
as a complete security boundary; formal product exposure remains dependent on
the separate authentication, tenant-isolation and authorization roadmap.
