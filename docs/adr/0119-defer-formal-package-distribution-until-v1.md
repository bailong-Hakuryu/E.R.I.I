---
status: accepted
---

# Defer formal package distribution until v1

E.R.I.I. uses `0.x` version identifiers to describe source-development
milestones and compatibility checkpoints. They do not require a matching Git
tag, GitHub Release, uploaded wheel/sdist, package-registry publication, or
release-asset readback before development can continue.

The repository may still build wheel and sdist artifacts locally, install them
in clean environments, and verify package metadata in CI. Those checks protect
installability and future release readiness; they do not create a distribution
commitment. A user who needs to reproduce a `0.x` source state must pin a
reviewed full commit SHA.

Formal package distribution is deferred until `1.0`. That milestone will define
the supported package name and metadata, immutable tag, GitHub Release,
wheel/sdist publication, integrity or signing policy, registry destination,
clean-install verification, asset readback, compatibility statement, and
support boundary as one deliberate release process.

## Consequences

`v0.4.0a8` remains an accurate historical release, but later `0.x` source
milestones are not required to repeat that release pattern. Roadmaps and support
documents must not treat publishing b1, rc, or another prerelease package as a
stage gate. Changelogs and source-version metadata may continue to identify
implemented milestones, while avoiding claims that an undistributed milestone
is an installed stable release.
