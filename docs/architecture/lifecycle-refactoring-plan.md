# Data Lifecycle Refactoring Roadmap

**Status:** Planning Phase  
**Target:** Split `erii/data_lifecycle.py` (180KB, 4000+ lines) into maintainable modules  
**Priority:** P1 (High - but not urgent)  
**Risk:** Medium (requires careful testing)

---

## Current State

### File Structure
```
erii/
└── data_lifecycle.py (180KB, 4000+ lines) ⚠️ TOO LARGE
    ├── DataLifecycleCoordinator (main class)
    ├── Format inspection logic
    ├── Migration strategies
    ├── Backup/restore logic
    └── Erasure logic
```

### Problems
1. **Monolithic** - One file handles all lifecycle operations
2. **Hard to navigate** - 4000 lines is too much for one file
3. **Mixed concerns** - Inspection, migration, backup, erasure all in one place
4. **Testing complexity** - Hard to isolate specific functionality

---

## Proposed Target Structure

```
erii/lifecycle/
├── __init__.py                    # Public API
├── _coordinator.py                # DataLifecycleCoordinator
├── _inspection.py                 # Format detection & validation
├── _migration.py                  # Upgrade strategies
├── _backup_restore.py             # Backup and restore
├── _erasure.py                    # Data deletion
├── _sqlite_migrations.py          # SQLite-specific migrations
├── _file_storage_migrations.py   # FileStorage migrations
└── _memory_pack_migrations.py    # MemoryPack migrations
```

---

## Module Breakdown

### Module 1: Coordinator (_coordinator.py)
**Responsibility:** Main orchestration and planning

**Key Classes:**
- `DataLifecycleCoordinator`
- `LifecycleTarget`
- `LifecycleAssessment`
- `LifecyclePlan`
- `LifecycleReport`

**Key Methods:**
- `inspect()` - Inspect a target
- `plan()` - Create a plan
- `execute()` - Execute a plan

**Lines:** ~500

---

### Module 2: Inspection (_inspection.py)
**Responsibility:** Detect format versions and validate integrity

**Key Classes:**
- `FormatInspector`
- `SQLiteInspector`
- `FileStorageInspector`
- `MemoryPackInspector`

**Key Functions:**
- Detect version from files
- Validate schema
- Check data integrity
- Assess migration needs

**Lines:** ~800

---

### Module 3: Migration (_migration.py)
**Responsibility:** Coordinate upgrades across formats

**Key Classes:**
- `MigrationStrategy`
- `MigrationExecutor`

**Key Functions:**
- Select appropriate strategy
- Execute migrations
- Verify migration success
- Rollback on failure

**Lines:** ~600

---

### Module 4: Backup & Restore (_backup_restore.py)
**Responsibility:** Create and restore backups

**Key Classes:**
- `BackupCreator`
- `BackupRestorer`

**Key Functions:**
- Create backup archives
- Verify backup integrity
- Restore from backup
- Handle partial failures

**Lines:** ~700

---

### Module 5: Erasure (_erasure.py)
**Responsibility:** Safe data deletion

**Key Classes:**
- `ErasureCoordinator`
- `ErasureVerifier`

**Key Functions:**
- Plan erasure operations
- Execute deletions
- Verify complete removal
- Handle erasure conflicts

**Lines:** ~600

---

### Module 6-8: Format-Specific Migrations
**Responsibility:** Handle format-specific upgrade logic

**Files:**
- `_sqlite_migrations.py` (~400 lines)
- `_file_storage_migrations.py` (~300 lines)
- `_memory_pack_migrations.py` (~400 lines)

**Content:**
- Schema upgrade SQL
- Data transformation logic
- Version-specific handlers

---

## Refactoring Strategy

### Phase 1: Extract Inspection Logic (Low Risk)
**Target:** `_inspection.py`  
**Reason:** Mostly read-only, low coupling  
**Effort:** 1-2 days

### Phase 2: Extract Migration Logic (Medium Risk)
**Target:** `_migration.py` + format-specific files  
**Reason:** Complex but well-tested  
**Effort:** 3-4 days

### Phase 3: Extract Backup/Restore (Medium Risk)
**Target:** `_backup_restore.py`  
**Reason:** Self-contained functionality  
**Effort:** 2-3 days

### Phase 4: Extract Erasure (Low Risk)
**Target:** `_erasure.py`  
**Reason:** Separate code path  
**Effort:** 1-2 days

### Phase 5: Refactor Coordinator (High Risk)
**Target:** `_coordinator.py`  
**Reason:** Orchestrates everything  
**Effort:** 2-3 days

---

## Backward Compatibility

Old code **must continue to work**:

```python
# Old (must keep working)
from erii import DataLifecycleCoordinator

# New (optional)
from erii.lifecycle import (
    DataLifecycleCoordinator,
    FormatInspector,
    MigrationStrategy,
)
```

**Implementation:**
```python
# erii/data_lifecycle.py (becomes a thin wrapper)
from erii.lifecycle._coordinator import DataLifecycleCoordinator
from erii.lifecycle._inspection import FormatInspector
# ... etc

__all__ = ["DataLifecycleCoordinator", ...]
```

---

## Testing Strategy

### Test Coverage
- ✅ `tests/test_data_lifecycle.py`
- ✅ `tests/test_lifecycle_*.py` (many files)
- ✅ Integration tests with real databases

### Testing Approach
1. Run full lifecycle tests after each module extraction
2. Verify all migration paths still work
3. Test backup/restore with real data
4. Verify erasure completeness

---

## Estimated Effort

| Phase | Effort | Risk |
|-------|--------|------|
| Extract Inspection | 1-2 days | Low |
| Extract Migration | 3-4 days | Medium |
| Extract Backup/Restore | 2-3 days | Medium |
| Extract Erasure | 1-2 days | Low |
| Refactor Coordinator | 2-3 days | High |
| Testing & Fixes | 2-3 days | - |
| Documentation | 1 day | Low |
| **Total** | **2-3 weeks** | **Medium** |

---

## Comparison with engine.py

| Aspect | data_lifecycle.py | engine.py |
|--------|-------------------|-----------|
| **Size** | 180KB, 4000 lines | 232KB, 5000 lines |
| **Complexity** | Medium | High |
| **Public API** | ~10 methods | ~80 methods |
| **Coupling** | Low | High |
| **Test Coverage** | Excellent | Excellent |
| **Refactoring Risk** | Medium | High |

**Recommendation:** Refactor `data_lifecycle.py` **before** `engine.py` due to lower risk.

---

## Success Criteria

1. ✅ All lifecycle tests pass
2. ✅ All migration paths work
3. ✅ Backup/restore verified
4. ✅ Each module < 1000 lines
5. ✅ No breaking changes

---

## Next Steps

**For v0.5.0a2:**
- ✅ Document the plan (this file)
- ❌ Do NOT start refactoring yet

**For v0.5.1 or v0.6.0:**
- Start with inspection module (lowest risk)
- One module per week
- Continuous testing

---

*Document created: 2026-08-08*  
*Status: Planning - Not yet started*
