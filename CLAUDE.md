# Coding Standards

## Architecture: DDD with Layers

- **Domain layer** — Core business logic, pure Python, no external dependencies.
  Models, value objects, domain services, repository interfaces.
- **Application layer** — Use cases and orchestration. Coordinates domain objects
  and infrastructure. Thin: delegates, never implements business rules.
- **Infrastructure layer** — All external I/O: git, LLM backends, file system,
  parsers (tree-sitter, ast-grep, ctags, scc, lizard), subprocess calls.
  Implements repository interfaces defined in domain.
- **Presentation layer** — CLI arg parsing, terminal output, reporters
  (text, JSON, DOT, Mermaid). No business logic.

## Single Responsibility Principle

- One responsibility per file.
- One responsibility per class.
- One responsibility per method/function.
- If you can't name it clearly in 3 words, it's doing too much. Split it.

## Function Rules

- **Max 3 parameters.** Use dataclasses or config objects when more are needed.
- **Max 30 lines** per function. Sweet spot is **10-15 lines**.
- Small, composable, easy to test.
- Prefer pure functions (input → output, no side effects) in domain layer.

## Naming

- Files: `snake_case.py`, one class/concept per file.
- Classes: `PascalCase`.
- Functions/methods: `snake_case`, verb-first (`build_graph`, `parse_imports`).
- Constants: `UPPER_SNAKE_CASE`.
- Private: prefix with `_` only when truly internal.

## Project Structure

```
src/
  domain/            # Core models and business logic
  application/       # Use cases, orchestration
  infrastructure/    # Git, LLM, parsers, external tools
  presentation/      # CLI, reporters, formatters
tests/
  domain/            # Mirrors src/domain/
  application/       # Mirrors src/application/
  infrastructure/    # Mirrors src/infrastructure/
  presentation/      # Mirrors src/presentation/
```

- Tests mirror source structure exactly.
- Test file matches source: `src/domain/pr_group.py` -> `tests/domain/test_pr_group.py`.

## Testing

- Every public function/class gets a test.
- Tests are small and focused: one assertion per test when practical.
- Use descriptive test names: `test_docs_exempt_from_line_limit`.
- No test should depend on another test.
- Mock infrastructure (git, LLM, filesystem) in domain/application tests.

## Dependencies

- Domain layer imports **nothing** from application, infrastructure, or presentation.
- Application layer imports from domain only.
- Infrastructure layer imports from domain (to implement interfaces).
- Presentation layer imports from application and domain.
- Never import upward through layers.

## General

- No dead code. Delete it, don't comment it out.
- No speculative abstractions. Build what's needed now.
- Prefer composition over inheritance.
- Type hints on all public interfaces.
- Docstrings on public classes and non-obvious functions. Skip for self-evident code.
