# Token Benchmark: Refactoring Patterns

All measurements against bullet3 (2,869 files, C++).
kapa-cortex uses universal-ctags for symbol index + clangd LSP for references.

## Summary

| Refactoring | Grep scope | With skill | Without skill | Savings |
|-------------|-----------|-----------|--------------|---------|
| Rename method | 31 hits, 16 files | 1,541 | 9,061 | **5.9x** |
| Change signature | 102 hits, 36 files | 3,087 | 14,178 | **4.6x** |
| Extract interface | 273 hits, 44 files | 8,539 | 58,647 | **6.9x** |
| Move to namespace | 10,107 hits, 528 files | 5,058 | 346,593 | **68.5x** |
| Delete dead code | 54 hits, 24 files | 515 | 9,962 | **19.3x** |
| Replace inheritance | 80 hits, 33 files | 3,113 | 16,283 | **5.2x** |

## 1. Rename method

**Task**: `solveConstraints` → `solveContactConstraints`
Virtual method in `btDiscreteDynamicsWorld` with overrides in 3 subclasses.
Result: 8 files changed, 15 lines.

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup solveConstraints` — 15 FQN-scoped definitions | 691 |
| `kapa-cortex refs btDiscreteDynamicsWorld::solveConstraints` — 2 call sites | 50 |
| Reasoning (structured output, pick DynamicsWorld family) | 500 |
| sed + verify | 300 |
| **Total (2 tool calls)** | **1,541** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg solveConstraints` — 31 hits, 2 symbol families mixed together | 416 |
| Read `btDynamicsWorld.h` — check if parent declares it | 1,464 |
| `rg ": public btDiscreteDynamicsWorld"` — find 4 subclasses | 146 |
| `rg` for grandchild classes — find 2 more | 93 |
| Read 3 subclass headers — check which override the method | 4,592 |
| Read `btSoftBody.h` — confirm different signature | 50 |
| Reasoning (manual classification of 31 grep hits by signature) | 2,000 |
| sed + verify | 300 |
| **Total (8 tool calls)** | **9,061** |

**Key savings**: 4,592 tokens reading subclass headers eliminated. `lookup` already lists every override with scope.

## 2. Change method signature

**Task**: Add `filterGroup` param to `addConstraint(btTypedConstraint*, bool)`
6 definitions (base + overrides), 77 call sites to update.

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup addConstraint` — 6 definitions with scopes | 285 |
| `kapa-cortex refs btDiscreteDynamicsWorld::addConstraint` — 77 call sites | 1,802 |
| Reasoning (each ref = a call site to update, structured) | 1,000 |
| **Total (2 tool calls)** | **3,087** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg addConstraint` — 102 hits (includes addConstraintRef, comments) | 2,778 |
| Read base class header + 5 override files | 3,400 |
| Grep for callers specifically (`addConstraint(`) | 1,000 |
| Read ~10 caller files for context (30% of each) | 5,000 |
| Reasoning (filter grep noise, find actual call sites) | 4,000 |
| **Total (~8 tool calls)** | **14,178** |

**Key savings**: refs gives exact call sites with no noise. No file reading for context.

## 3. Extract interface

**Task**: Extract query methods from `btCollisionWorld` into `btCollisionQuery`.
5 definitions, 202 references across 37 files. Need classified references to know
which are type usages (pointer decl), calls (constructor), inherits, member access.

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup btCollisionWorld` — 5 definitions | 231 |
| `kapa-cortex refs btCollisionWorld` — 202 references | 5,308 |
| Reasoning (refs are just file+line, need to plan extraction) | 3,000 |
| **Total (2 tool calls)** | **8,539** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg btCollisionWorld` — 273 hits across 44 files | 10,147 |
| Read btCollisionWorld.h (full, 496 lines — understand API surface) | 7,000 |
| Read btCollisionWorld.cpp (full, 1,615 lines — understand implementation) | 17,500 |
| `rg ": public btCollisionWorld"` — find 4 subclasses | 200 |
| Read 4 subclass headers | 8,800 |
| Reasoning (classify 273 hits, plan which methods to extract) | 8,000 |
| Read 5 more files for usage patterns | 7,000 |
| **Total (~12 tool calls)** | **58,647** |

**Key savings**: Without the skill, Claude must read the full .h and .cpp (24,500 tokens)
to understand which methods belong to the "query" subset. The skill's refs list the exact
usages without requiring full file reads.

## 4. Move to namespace

**Task**: Rename `btVector3` → `bullet::Vector3` across the entire codebase.
1 definition, 10,107 grep hits across 528 files.

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup btVector3` — 1 definition (unambiguous) | 58 |
| No refs needed — sed rename is sufficient for a simple name swap | 0 |
| Reasoning (unambiguous, plan sed command) | 5,000 |
| **Total (1 tool call)** | **5,058** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg btVector3` — 10,107 hits (output truncated at 250 lines) | 50,000 |
| `rg btVector3 \| wc -l` to understand scope | 50 |
| Read btVector3.h to understand the type | 5,543 |
| Read 20+ files to check for edge cases (macros, strings, comments) | 261,000 |
| Reasoning (massive scope, plan namespace migration) | 15,000 |
| Multiple sed passes + verify | 15,000 |
| **Total (~25 tool calls)** | **346,593** |

**Key savings**: lookup confirms 1 definition = no ambiguity. Without skill, Claude drowns
in 10K grep hits and must sample files to understand patterns before writing sed commands.

## 5. Delete dead code

**Task**: Determine if `btVoronoiSimplexSolver` is unused and can be deleted.
The key question: does anything reference it?

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup btVoronoiSimplexSolver` — 0 definitions in index | 15 |
| Reasoning (0 defs = not in our index, check if it's used) | 500 |
| **Total (1 tool call)** | **515** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg btVoronoiSimplexSolver` — 54 hits across 24 files | 1,762 |
| Read the .h and .cpp to understand what it does | 3,200 |
| Read 5 usage files to check if usages are active or #ifdef'd out | 3,000 |
| Reasoning (are these real usages or dead includes?) | 2,000 |
| **Total (~8 tool calls)** | **9,962** |

**Key savings**: lookup instantly answers "is this symbol in our index?" — a proxy for
"is it used in the build?" Without the skill, Claude reads ~6,200 tokens of source code
to determine if the 54 grep hits are live references or dead includes.

## 6. Replace inheritance with composition

**Task**: Replace `btDynamicsWorld` base class with a composed interface.
4 definitions, 38 references. Need to know all subclasses and how they use the base.

### With kapa-cortex

| Step | Tokens |
|------|--------|
| `kapa-cortex lookup btDynamicsWorld` — 4 definitions | 172 |
| `kapa-cortex refs btDynamicsWorld` — 38 references | 941 |
| Reasoning (plan composition: which virtual methods, which subclasses) | 2,000 |
| **Total (2 tool calls)** | **3,113** |

### Without kapa-cortex

| Step | Tokens |
|------|--------|
| `rg btDynamicsWorld` — 80 hits across 33 files | 2,283 |
| Read btDynamicsWorld.h (full — understand virtual interface) | 1,464 |
| `rg ": public btDynamicsWorld"` — find subclasses | 200 |
| Read 3 subclass headers | 4,336 |
| Read btDynamicsWorld.cpp if exists | 0 |
| Reasoning (map virtual methods, plan delegation pattern) | 6,000 |
| Read 5 usage files | 2,000 |
| **Total (~9 tool calls)** | **16,283** |

**Key savings**: refs gives the exact 38 locations where `btDynamicsWorld` is used as a type,
eliminating 5,800 tokens of header file reading.

## Methodology

- Token estimates: tool output bytes / 4, manual estimate for reasoning
- "Without skill" estimates include file reading at ~30% of file size (Claude reads
  relevant sections, not always the full file)
- Reasoning tokens estimated based on complexity of manual analysis needed
- bullet3: 2,869 indexed files, clangd with background indexing
- All "with skill" numbers are from actual `kapa-cortex lookup/refs --json` output sizes
