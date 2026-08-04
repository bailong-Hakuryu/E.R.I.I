# Getting Started: Prove Relationship Continuity

This is the shortest path from a source checkout to E.R.I.I.'s core promise:
one character can remember an experience with **User A** after a process
restart, while **User B** neither recalls that experience nor inherits the same
intimacy.

For the complete English manual, see [the User Guide](USAGE.md). 中文完整手册见
[中文使用手册](USAGE_zh-CN.md).

## What this demo proves

The Golden Continuity Demo uses an original synthetic character, deterministic
host-side extractors, real SQLite storage, structured recall, and a real
MemoryPack export/import round trip. It does not need a model API key or
network access.

It verifies four observable claims:

1. **Restart persistence:** User A's accepted first-snow event remains
   available after the first Engine is closed and a new Engine opens the
   database.
2. **Relationship isolation:** both relationships approve a Manifest compiled
   from the same synthetic Blueprint. User A selects one synthetic canonical
   relationship graph; User B remains fresh and receives none of that private
   Persona projection, first-snow event, memory, state change, or state reason.
3. **Provenance:** recalled long-term artifacts resolve to the completed Source
   Turn and archival identity that produced them.
4. **Portability:** User A's relationship is exported as `user-a.erii`,
   atomically imported into a fresh SQLite database, reopened, and checked for
   the same relationship state, Persona, event, memory, Turn, and compact
   archival commitments. User B is absent from the imported store.

The live source store retains the full archival receipt, so its recalled
artifacts are `source_linked`. MemoryPack intentionally carries a content-free
Archival Tombstone with exact artifact fingerprints instead of exporting the
full operational receipt. After import, those same memories keep ordinary
generation authority and their exact Source Turn, but Recall reports
`partial_source` rather than overstating the retained evidence. The Demo checks
this downgrade explicitly.

The demo does **not** simulate v0.5 Relationship Consequence, Narrative Tension,
repair, refusal of repair, or Character Deliberation.

## Install from source

E.R.I.I. `0.x` identifiers are source milestones. Formal package distribution
is planned for `1.0`; until then, pin a reviewed full commit SHA for
reproducible use.

```bash
git clone https://github.com/bailong-Hakuryu/E.R.I.I.git
cd E.R.I.I
python -m venv .venv
```

Linux or macOS:

```bash
source .venv/bin/activate
python -m pip install .
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

The current source requires Python 3.11 or newer within the repository's
documented support range. The base install is enough for this demo.

## Run one command

Run the demo with a path that does not already exist:

```bash
erii demo --output-dir ./erii-demo
```

Expected result:

```text
E.R.I.I. Golden Continuity Demo
[PASS] restart persistence
[PASS] relationship isolation
[PASS] provenance
[PASS] portable round trip
Artifacts: <absolute path>/erii-demo
```

The command refuses to reuse an existing output directory. Choose another path
for a new run; E.R.I.I. will not delete or overwrite the old artifacts.

## Inspect the proof

The output directory contains:

| Artifact | What it shows |
| --- | --- |
| `erii-demo.sqlite3` | The persistent SQLite store reopened by the second Engine |
| `user-a-imported.sqlite3` | A fresh SQLite store produced only through the exported MemoryPack and then reopened |
| `user-a-recall.md` | The rendered Agent-private recall for User A |
| `user-a.erii` | User A's portable MemoryPack |
| `demo-report.json` | Recorded IDs, exact B baseline evidence, Persona-scope evidence, provenance references, and all four checks |

The report intentionally identifies `agent-lumi`, `user-a`, and `user-b`.
User A and User B share the same character identity but have different
relationship and persona identities. Both approve relationship-bound Manifests
from the same Blueprint text, but only User A selects the synthetic canonical
“silver paper-star” graph. User B's planned Persona context excludes that graph
and its source phrase. Only User A's Pack is exported and imported into the
fresh target.

`demo-report.json` is an inspectable Demo artifact, not a frozen application
API. Use its `schema_version` when experimenting, but do not treat the Demo
report shape as a long-term host contract.

MemoryPack is an open data-portability format, not an encrypted or authenticated
container. The demo data is synthetic; before using real conversations, read
[Security Policy](../SECURITY.md) and protect storage and exports in the host.

## Integrate a real host next

The demo proves the kernel, but it is not a chat product. E.R.I.I. does not
generate the visible reply, choose a model, run a hidden worker, or provide
product authentication.

Continue with [Host Integration](host-integration.md) for the canonical
Turn-to-recall flow, then use [API Stability](api-stability.md) to decide which
surface belongs in a first integration.
