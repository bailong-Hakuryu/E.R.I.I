# Engine Refactoring Roadmap

**Status:** Planning Phase  
**Target:** Split `erii/engine.py` (232KB, 5000+ lines, 80+ methods) into maintainable modules  
**Priority:** P1 (High - but not urgent)  
**Risk:** Medium (requires careful testing)

---

## Current State

### File Structure
```
erii/
├── engine.py (232KB, 5000+ lines) ⚠️ TOO LARGE
│   └── ERIIEngine class (80+ methods)
└── data_lifecycle.py (180KB, 4000+ lines) ⚠️ TOO LARGE
```

### Problems
1. **Hard to understand** - New contributors face a 5000-line file
2. **Merge conflicts** - High risk when multiple people edit
3. **Slow to navigate** - IDEs struggle with large files
4. **Difficult to test** - Hard to isolate specific functionality

---

## Proposed Target Structure

### Phase 1: Split ERIIEngine into Logical Modules

```
erii/engine/
├── __init__.py              # Public API (re-exports everything)
├── _core.py                 # Main ERIIEngine coordinator
├── _recall.py               # Memory recall functionality
├── _turn_lifecycle.py       # Turn management
├── _archival.py             # Archival coordination  
├── _relationship.py         # Relationship adjudication
├── _persona.py              # Persona compilation & growth
├── _temporal.py             # Promise/loop temporal logic
├── _narrative.py            # Narrative tension & consequences
├── _monologue.py            # Inner monologue & diary
└── _import_export.py        # MemoryPack operations
```

### Backward Compatibility Strategy

The old import path **must continue to work**:

```python
# Old (must keep working)
from erii import ERIIEngine

# New (optional, for advanced users)
from erii.engine import RecallEngine, TurnEngine
```

**Implementation:**
```python
# erii/engine.py (becomes a thin wrapper)
from erii.engine._core import ERIIEngine

__all__ = ["ERIIEngine"]
```

---

## Method Grouping

### Module 1: Recall (_recall.py)
**Methods:**
- `recall()` - Basic recall
- `recall_structured()` - Structured recall
- `render_recall()` - Render recall results
- `set_core_memory()` - Set core memory
- `get_core_memory()` - Get core memory

**Lines:** ~300  
**Dependencies:** Storage, Retriever, BudgetManager

---

### Module 2: Turn Lifecycle (_turn_lifecycle.py)
**Methods:**
- `begin_turn()` - Start a turn
- `get_turn()` - Get turn by ID
- `list_turns()` - List turns
- `record_reply_attempt_failure()` - Record failure
- `list_reply_attempts()` - List attempts
- `complete_turn()` - Complete turn
- `abandon_turn()` - Abandon turn
- `record_turn()` - Record complete turn
- `archive_turn()` - Archive turn

**Lines:** ~500  
**Dependencies:** Storage, TurnLedger, Archiver

---

### Module 3: Archival (_archival.py)
**Methods:**
- `get_archival_receipt()` - Get receipt
- `list_archival_receipts()` - List receipts
- `compact_archival_receipts()` - Compact receipts

**Lines:** ~200  
**Dependencies:** Storage, ArchivalCoordinator

---

### Module 4: Relationship (_relationship.py)
**Methods:**
- `initialize_relationship()` - Initialize relationship
- `get_relationship_snapshot()` - Get snapshot
- `list_relationship_events()` - List events
- `adjudicate_relationship_candidates()` - Adjudicate (deprecated)
- `adjudicate_turn_candidates()` - Adjudicate turn
- `list_relationship_adjudications()` - List adjudications
- `process_relationship_turn()` - Process turn
- `get_relationship_processing_run()` - Get processing run
- `list_relationship_processing_runs()` - List runs
- `get_relationship_processing_receipt()` - Get receipt
- `list_relationship_processing_receipts()` - List receipts

**Lines:** ~800  
**Dependencies:** Storage, RelationshipAdjudicator

---

### Module 5: Persona (_persona.py)
**Methods:**
- `get_persona_manifest()` - Get manifest
- `list_persona_manifests()` - List manifests
- `compile_persona_blueprint()` - Compile blueprint
- `propose_persona_growth()` - Propose growth
- `decide_persona_growth_proposal()` - Decide proposal
- `list_persona_growth_proposals()` - List proposals

**Lines:** ~600  
**Dependencies:** Storage, PersonaCompiler

---

### Module 6: Temporal (_temporal.py)
**Methods:**
- `record_promise()` - Record promise
- `confirm_promise_condition()` - Confirm condition
- `resolve_promise()` - Resolve promise
- `record_open_loop()` - Record open loop
- `resolve_open_loop()` - Resolve open loop

**Lines:** ~400  
**Dependencies:** Storage, TemporalEngine

---

### Module 7: Narrative (_narrative.py)
**Methods:**
- `record_relationship_consequence()` - Record consequence
- `append_relationship_consequence()` - Append consequence
- `list_relationship_consequences()` - List consequences
- `record_narrative_tension_link()` - Record link
- `append_narrative_tension_link()` - Append link
- `list_narrative_tension_links()` - List links
- `list_narrative_tensions()` - List tensions

**Lines:** ~400  
**Dependencies:** Storage

---

### Module 8: Monologue (_monologue.py)
**Methods:**
- `remember_thought()` - Remember thought
- `get_inner_monologue()` - Get monologue
- `get_diary_timeline()` - Get diary
- `resolve_thought()` - Resolve thought
- `_compact_monologue_if_needed()` - Compact (internal)

**Lines:** ~300  
**Dependencies:** Storage

---

### Module 9: Import/Export (_import_export.py)
**Methods:**
- `export_memory_pack()` - Export pack
- `import_memory_pack()` - Import pack
- `stage_memory_pack()` - Stage pack
- `commit_staged_memory_pack()` - Commit pack

**Lines:** ~500  
**Dependencies:** Storage, ConsolidationEngine

---

### Module 10: Core (_core.py)
**Content:**
- `ERIIEngine.__init__()` - Initialization
- `ERIIEngine.close()` - Cleanup
- `ERIIEngine.remember()` - Legacy method (deprecated)
- Internal helpers
- Composition of all other modules

**Lines:** ~500  
**Dependencies:** All modules above

---

## Refactoring Strategy

### Step 1: Create Module Structure (1 day)
```bash
# Create files
touch erii/engine/__init__.py
touch erii/engine/_core.py
touch erii/engine/_recall.py
# ... etc
```

### Step 2: Move Methods One Module at a Time (1-2 weeks)
**For each module:**
1. Create new file with methods
2. Import in `_core.py`
3. Delegate from ERIIEngine
4. Run full test suite
5. Fix any breakages
6. Commit

**Example for _recall.py:**
```python
# erii/engine/_recall.py
class RecallMixin:
    def recall(self, agent_id, user_id, query, top_k=5):
        # Move implementation here
        ...

# erii/engine/_core.py
from erii.engine._recall import RecallMixin

class ERIIEngine(RecallMixin, TurnMixin, ...):
    def __init__(self, ...):
        # Initialization
        ...
```

### Step 3: Update Imports (1 day)
```python
# erii/engine/__init__.py
from erii.engine._core import ERIIEngine

# Optionally expose sub-engines
from erii.engine._recall import RecallMixin
from erii.engine._turn_lifecycle import TurnMixin

__all__ = [
    "ERIIEngine",
    # Optional: "RecallMixin", "TurnMixin", ...
]
```

### Step 4: Update Documentation (1 day)
- Update README with new structure
- Add architecture diagrams
- Document sub-modules

---

## Testing Strategy

### Critical: Zero Breakage
**Every commit must:**
1. ✅ Pass all 646 existing tests
2. ✅ Maintain public API compatibility
3. ✅ Not change any behavior

### Test Commands
```bash
# Full test suite
python -m pytest tests/ -v

# Specific module tests
python -m pytest tests/test_engine.py -v
python -m pytest tests/test_recall*.py -v
python -m pytest tests/test_turn*.py -v
```

---

## Risk Mitigation

### High-Risk Areas
1. **`__init__()` method** - Complex initialization
2. **State sharing** - Modules share storage, adapters
3. **Internal methods** - Private methods used across modules
4. **Circular dependencies** - Modules may reference each other

### Mitigation Strategies
1. **Use Mixins** - Each module is a mixin class
2. **Shared state via composition** - Pass dependencies explicitly
3. **Gradual migration** - One module per PR
4. **Extensive testing** - Run tests after each module

---

## Alternative: Facade Pattern (Lower Risk)

If mixin approach is too complex, use **Facade Pattern**:

```python
# erii/engine/_recall.py
class RecallEngine:
    def __init__(self, storage, retriever, budget_manager):
        self.storage = storage
        self.retriever = retriever
        self.budget_manager = budget_manager
    
    def recall(self, agent_id, user_id, query, top_k=5):
        # Implementation
        ...

# erii/engine/_core.py
class ERIIEngine:
    def __init__(self, ...):
        # Initialize sub-engines
        self._recall_engine = RecallEngine(
            self.storage, self.retriever, self.budget_manager
        )
    
    def recall(self, agent_id, user_id, query, top_k=5):
        # Delegate to sub-engine
        return self._recall_engine.recall(agent_id, user_id, query, top_k)
```

**Pros:**
- Clear separation
- Easier to test sub-engines independently
- Less risk of circular dependencies

**Cons:**
- More boilerplate (delegation methods)
- Slightly more method calls

---

## Estimated Effort

| Phase | Effort | Risk |
|-------|--------|------|
| Planning & Setup | 4 hours | Low |
| Module 1-3 (Recall, Turn, Archival) | 2 days | Medium |
| Module 4-6 (Relationship, Persona, Temporal) | 3 days | Medium |
| Module 7-9 (Narrative, Monologue, Import) | 2 days | Low |
| Module 10 (Core integration) | 1 day | High |
| Testing & Bug Fixes | 2 days | - |
| Documentation | 1 day | Low |
| **Total** | **2-3 weeks** | **Medium** |

---

## Recommendation

**For v0.5.0a2:**
- ✅ Document the refactoring plan (this file)
- ❌ Do NOT start the refactoring yet
- 📊 Collect user feedback first

**For v0.5.1 or v0.6.0:**
- Start refactoring after real-world usage validates current design
- Begin with lowest-risk modules (Recall, Archival)
- Use feature flags if needed

---

## Success Criteria

The refactoring is successful if:
1. ✅ All 646 tests still pass
2. ✅ No breaking changes to public API
3. ✅ `from erii import ERIIEngine` still works
4. ✅ Each module < 1000 lines
5. ✅ New contributors find code easier to navigate
6. ✅ No performance regression

---

## Notes

- This is a **maintainability improvement**, not a bug fix
- Current code **works correctly** - no urgency
- Risk of introducing bugs if rushed
- Better to wait for quiet period in development cycle

---

*Document created: 2026-08-08*  
*Status: Planning - Not yet started*
