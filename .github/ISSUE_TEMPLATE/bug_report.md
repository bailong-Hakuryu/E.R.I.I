---
name: Bug report
about: Report a reproducible E.R.I.I. defect
title: "[Bug] "
labels: bug
assignees: ""
---

Thank you for helping improve E.R.I.I. Please reduce the report to a
synthetic reproduction. Do not attach real chats, private persona data, a
production database, API keys, access tokens, or other secrets.

## Environment

- E.R.I.I. full 40-character commit SHA:
- Python version:
- Operating system:
- Storage backend:
- Installation command:

## Relationship scope

- Synthetic `agent_id`:
- Synthetic `user_id`:
- Lifecycle step (`record_turn`, `archive_turn`,
  `process_relationship_turn`, `recall_structured`, or `export_memory`):

## Reproduction

Provide the smallest runnable example or test using invented identities and
content:

```python
# Minimal synthetic reproduction
```

## Expected behavior

What observable result should occur?

## Actual behavior

What occurred instead? Include the complete traceback after removing local
paths, credentials, private persona material, and user content.

## Additional checks

- [ ] The issue reproduces from a fresh database.
- [ ] The issue reproduces at the full commit SHA above.
- [ ] The example demonstrates whether another agent-user relationship is
      affected.
- [ ] I replaced real chats and copyrighted/private persona source material
      with synthetic data.
- [ ] I did not include a production database or an API key.
