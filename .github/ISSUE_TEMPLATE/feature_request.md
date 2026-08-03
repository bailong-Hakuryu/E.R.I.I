---
name: Feature request
about: Propose an observable capability or contract change
title: "[Feature] "
labels: enhancement
assignees: ""
---

Describe the request with a synthetic example. Do not attach real chats,
private persona data, a production database, API keys, access tokens, or
other secrets.

## Current observable behavior

What can a host application or user observe today?

## Desired observable behavior

What should become observable? Show the smallest synthetic input and expected
output.

## Proposed scope

Choose the narrowest owning layer:

- [ ] Core — durable continuity, memory, relationship, or portable-data rule
- [ ] Host Integration — orchestration through the documented lifecycle
- [ ] Adapter — provider, model, storage, or framework integration
- [ ] Labs — optional experiment without a stable compatibility promise

Explain why the request belongs in that layer:

## Contract and data review

- Data-format impact:
- Compatibility impact:
- Privacy/security impact:
- Relationship-isolation impact:
- Migration or rollback behavior:

## Alternatives

What can a host or removable module do without changing the core?

## Acceptance evidence

List deterministic tests or observable checks. Use invented identities and
synthetic content only; never include private persona material, a production
database, or an API key.
