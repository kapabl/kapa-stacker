# kapa-stacker — Session Summary

> Generated from the initial build session. Use this to onboard into the codebase.

## What it does

CLI tool that splits a feature branch into stacked PRs. Analyzes code dependencies across 15+ languages, groups files into ~3-file/~200-line PRs, generates git commands to create the branches, and uses a local LLM (ollama) for smarter grouping.

## Architecture (DDD + Layers)

```
src/
  domain/
    entity/        ChangedFile, ProposedPR, ExecutionPlan
    value_object/  ImportRef, SymbolDef, FileComplexity, RiskScore,
                   MergeStrategy, StepStatus, ExtractionRule, TestPair
    aggregate/     StackedPRSet (root)
    factory/       PRSetFactory, PlanFactory
    repository/    PlanRepository (interface)
    service/       DependencyResolver, TestPairFinder, MergeOrderResolver,
                   FileMatcher, PromptParser, PRNamer
    policy/        RiskPolicy, MergeStrategyPolicy
    port/          GitReader, ImportParser, SymbolExtractor,
                   ComplexityAnalyzer, LLMService, CommandRunner
    event.py       DependencyCycleDetected, DependencyPulledIn, StepFailed

  application/     AnalyzeBranchUseCase, ExtractFilesUseCase,
                   GeneratePlanUseCase, ExecutePlanUseCase

  infrastructure/
    git/           GitClient, ShellCommandRunner
    parsers/       LanguageDetector, PythonAstParser, RegexParsers (15 langs),
                   ImportDispatcher, MultiLangParser
    complexity/    LizardAnalyzer, SccAnalyzer, LizardSccAnalyzer
    llm/           OllamaLLMService, NullLLMService, backends, setup_ollama
    persistence/   JsonPlanStore
    indexer/       CtagsIndexer, ImportCache, CochangeCache,
                   ComplexityCache, IndexAll

  presentation/
    cli.py         Entry point (argparse only, no business logic)
    reporters/     TextReporter, JsonReporter, DotReporter,
                   PlanReporter, ExtractionReporter
```

## Key Decisions

- **AI on by default.** `--no-ai` to disable. Falls back silently if ollama isn't running.
- **PRNamer** generates titles from actual code changes (class/function defs in the diff), not from file names.
- **Test pairing** is a hard constraint — `test_foo.py` always stays with `foo.py` in the same PR.
- **Domain has zero external imports.** Pure Python, testable without mocks.
- **Old flat files deleted.** No `stacked_pr_analyzer.py`, `lang_parsers.py` etc. at root.
- **Entry point:** `kapa-stacker` command (via `pyproject.toml`) or `python kapa-stacker.py`.
- **Caches** go to `.stacker-cache/` (gitignored).

## CLI Commands

```bash
kapa-stacker                          # analyze current branch vs main
kapa-stacker --base develop           # diff against a different base
kapa-stacker --setup                  # install ALL deps (ollama, ctags, scc, ast-grep, lizard)
kapa-stacker --index                  # pre-compute caches (ctags, imports, co-change, complexity)
kapa-stacker --generate-plan          # create .stacked-pr-plan.json with git commands
kapa-stacker --check-plan             # show plan progress
kapa-stacker --run-plan               # execute plan interactively
kapa-stacker --run-plan --dry-run     # preview without executing
kapa-stacker --run-plan --step 5      # execute single step
kapa-stacker --extract "auth changes" # pull subset into a PR branch
kapa-stacker --json                   # JSON output
kapa-stacker --visualize              # DOT graph
kapa-stacker --shell-script           # bash script to stdout
kapa-stacker --print-commands         # copy-pasteable git commands
kapa-stacker --no-ai                  # disable LLM
kapa-stacker --ai-check               # check LLM backend status
```

## Supported Languages

Python, C, C++, Java, Kotlin, Go, Rust, JavaScript, TypeScript,
Gradle (Groovy + KTS), CMake, Buck2, BXL, Starlark/Bazel, Groovy.

Import parsing uses a layered strategy: tree-sitter -> ast-grep -> Python ast -> regex.

## Tests

```bash
python -m unittest discover -s tests/domain -v          # 33 domain tests (pure, 0.003s)
python -m unittest discover -s tests/infrastructure -v   # 66 infrastructure tests
python -m unittest discover -s tests -v                  # 99 total
```

## Known Gaps / Next Steps

1. **`lang_parsers.py` legacy file** — still at `src/infrastructure/parsers/lang_parsers.py` alongside the new split files. Can be deleted once the new import dispatcher is fully wired and the old monolith has no remaining consumers.
2. **Application + presentation tests** — not yet written. Only domain and infrastructure tests exist (99 total).
3. **Indexer caches not consumed yet** — the `--index` command builds caches to `.stacker-cache/`, but the `AnalyzeBranchUseCase` still does live parsing. Need to wire the use case to check caches first.
4. **`GeneratePlanUseCase` vs `PlanFactory`** — the application use case duplicates some logic from the domain factory. Should consolidate: use case calls factory, factory owns the creation logic.
5. **Aggregate root not fully wired** — `StackedPRSet` exists but the analysis pipeline still returns raw lists of `ProposedPR` instead of the aggregate. The use case should return a `StackedPRSet`.
6. **No integration tests** — the infrastructure tests test individual parsers but not the full pipeline (git diff -> parse -> group -> plan).

## Coding Standards (from CLAUDE.md)

- Max 3 parameters per function. Use dataclasses for more.
- Max 30 lines per function. Sweet spot is 10-15.
- One responsibility per file, class, method.
- Domain imports nothing from application, infrastructure, or presentation.
- Tests mirror source structure exactly.
- No dead code, no speculative abstractions.
