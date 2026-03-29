"""Run lizard for function-level cyclomatic complexity."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed

import lizard

from src.domain.value_object.file_complexity import FileComplexity, FunctionComplexity

_MAX_WORKERS = os.cpu_count() or 4


def analyze_lizard(file_paths: list[str]) -> dict[str, FileComplexity]:
    """Run lizard on files for per-function complexity metrics."""
    existing = [path for path in file_paths if os.path.exists(path)]
    if len(existing) < 50:
        return _analyze_sequential(existing)
    return _analyze_parallel(existing)


def _analyze_sequential(file_paths: list[str]) -> dict[str, FileComplexity]:
    metrics: dict[str, FileComplexity] = {}
    for path in file_paths:
        result = _analyze_single(path)
        if result:
            metrics[path] = result
    return metrics


def _analyze_parallel(file_paths: list[str]) -> dict[str, FileComplexity]:
    metrics: dict[str, FileComplexity] = {}
    with ProcessPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_analyze_single, path): path for path in file_paths}
        for future in as_completed(futures):
            path = futures[future]
            try:
                result = future.result()
                if result:
                    metrics[path] = result
            except Exception:
                pass
    return metrics


def _analyze_single(path: str) -> FileComplexity | None:
    try:
        analysis = lizard.analyze_file(path)  # type: ignore[attr-defined]
    except Exception:
        return None

    functions = _extract_functions(analysis)
    total_cyclomatic = sum(func.cyclomatic for func in functions)
    avg_cyclomatic = total_cyclomatic / len(functions) if functions else 0
    max_cyclomatic = max((func.cyclomatic for func in functions), default=0)

    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return FileComplexity(
        language=ext, lines=analysis.nloc, code=analysis.nloc,
        comments=0, blanks=0, complexity=total_cyclomatic,
        functions=functions,
        avg_cyclomatic=round(avg_cyclomatic, 1),
        max_cyclomatic=max_cyclomatic,
    )


def _extract_functions(analysis) -> list[FunctionComplexity]:
    return [
        FunctionComplexity(
            name=func.name,
            start_line=func.start_line,
            end_line=func.end_line,
            cyclomatic=func.cyclomatic_complexity,
            token_count=func.token_count,
            parameter_count=len(func.parameters),
            length=func.nloc,
        )
        for func in analysis.function_list
    ]
