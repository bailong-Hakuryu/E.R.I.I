# E.R.I.I. v0.4.0a8 — Continuity Audit and Release Closeout

> GitHub prerelease / Alpha. Suitable for local development, prototyping, and
> controlled integrations. Do not expose the reference service as a public
> production system without a trusted host-side security boundary.

`0.4.0a8` is an alpha prerelease of the E.R.I.I. character-continuity and
long-term-memory kernel. It closes the v0.4 Alpha line by making reply review,
archival evidence, exceptional-delivery quarantine, recall authority, and
portable data validation durable and inspectable.

这是 E.R.I.I. v0.4 Alpha 阶段的收口版本，重点不是增加新的关系数值或记忆类型，而是让回复连续性审查、长期记忆证据、异常交付隔离、召回权威与数据携带校验形成可持久验证的闭环。

## Highlights / 核心变化

- Every modern completed Turn owns a strict `ContinuityReviewRecord`; a
  successful review is bound to the exact final visible reply and its
  relationship-scoped evidence.
- Modern Timeline and MemoryNode artifacts require exact message-level Unicode
  spans, Source Turn revision, role, message hash, and stable evidence identity.
- Agent messages delivered through `overridden` or `shown_unreviewed` remain in
  the true transcript but cannot silently become ordinary memory, persona,
  promise, reflection, growth, or relationship authority.
- Recall is partitioned into `ordinary`, `legacy_context`, and
  `quarantined_history`. Public recall uses only Ordinary evidence; Legacy data
  remains inspectable and portable without being silently promoted.
- MemoryPack `0.4.0a8`, FileStorage, and SQLite Schema v9 preserve the new
  receipts, evidence closures, artifact commitments, and relationship
  processing state across round trips.
- The reference REST service now fails closed without an API key unless the
  host explicitly enables loopback-only unauthenticated development mode. It
  also enforces request and import-size boundaries and returns sanitized errors.

## Character behavior / 角色行为原则

Continuity judgment is affectively neutral. Kindness is not automatically
correct, and refusal, anger, distance, conflict, or hurt are not automatically
OOC. A sharp choice that follows the character's identity and causal history
may be valid; the consequences of that choice must still remain in memory and
relationship history.

连续性判断不把“温柔”等同于正确，也不把拒绝、生气、疏远或伤害等同于 OOC。只要选择确实来自角色自身的人格与经历因果，它可以是正常选择；但造成的关系后果必须被角色记住、面对，并允许后续真实经历决定是否修复。

## Compatibility / 兼容性

- Python 3.9 through 3.12 are supported. This is the final release that promises
  Python 3.9 support; `0.4.0b1` raises the minimum to Python 3.11.
- SQLite databases are migrated in place to Schema v9. Back up important data
  before opening it with a8 for the first time.
- Data from `0.4.0a7` and earlier remains readable. Missing modern evidence is
  represented explicitly as Legacy rather than reconstructed from current
  models or guessed from content.
- New archival submissions require extractor schema `"2"`. Frozen schema
  `"1"` commit work can finish under its original Legacy identity; unfinished
  extraction work must be resubmitted explicitly with a new idempotency key.

## Security and product limits / 安全与产品边界

- The reference API key is one project-owner credential, not per-user
  authorization or tenant isolation.
- MemoryPack is self-consistency checked but is not signed or encrypted. It
  does not authenticate an untrusted third-party pack that has been rewritten
  as a whole.
- TLS termination, rate limiting, quotas, abuse detection, encryption, and
  complete multi-tenant authorization remain host or later-version work.
- JSON and SQLite data are plaintext by default. The explicit unauthenticated
  loopback development mode must never be placed behind a reverse proxy.
- Prompt-injection filtering and PII masking are defense-in-depth helpers, not
  authentication, authorization, or a complete data-loss-prevention boundary.
- E.R.I.I. remains an alpha library maintained without a commercial SLA. Do not
  expose the reference service directly to the public internet without an
  additional trusted security boundary.

## Installation / 安装

Install the exact immutable source tag:

```bash
git clone --branch v0.4.0a8 --depth 1 https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
python -m pip install .
```

The GitHub prerelease also contains the wheel, source distribution, and
`SHA256SUMS.txt` generated from the same release workflow run.

## Verification / 验证

Before tagging, the release candidate passed 426 tests and 319 subtests, Ruff,
Python 3.9 compilation/import checks, wheel and sdist builds, package-content
inspection, and isolated wheel installation. The tag workflow repeats the
supported-version checks, builds the artifacts once, verifies both wheel and
sdist installation, and publishes the same files with SHA-256 checksums.

See [CHANGELOG.md](https://github.com/bailong-Hakuryu/E.R.I.I/blob/v0.4.0a8/CHANGELOG.md),
[README.md](https://github.com/bailong-Hakuryu/E.R.I.I/blob/v0.4.0a8/README.md),
and the [a8 implementation contract](https://github.com/bailong-Hakuryu/E.R.I.I/blob/v0.4.0a8/docs/a8-implementation-contract.md)
for the complete public behavior and migration details.
