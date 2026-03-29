"""Rule-based text generator — fallback when LLM unavailable."""

from __future__ import annotations

from pathlib import Path

from src.domain.port.text_generator import TextGenerator
from src.domain.entity.changed_file import ChangedFile
from src.domain.service.pr_namer import generate_title


class RuleBasedGenerator(TextGenerator):
    """Generates titles and summaries using templates. No LLM needed."""

    def generate_title(
        self, diff_text: str, file_paths: list[str], symbols: list[str],
    ) -> str:
        # Build minimal ChangedFile objects for pr_namer compatibility
        files = [
            ChangedFile(path=path, added=0, removed=0, status="M", diff_text=diff_text)
            for path in file_paths
        ]
        return generate_title(files)

    def generate_summary(
        self, diff_text: str, file_paths: list[str], depends_on: list[int],
    ) -> str:
        file_count = len(file_paths)
        modules = _unique_modules(file_paths)
        dep_text = f" Depends on PR(s) {', '.join(f'#{dep}' for dep in depends_on)}." if depends_on else ""

        return (
            f"- Changes {file_count} file(s) in {', '.join(modules)}\n"
            f"- {_line_summary(diff_text)}\n"
            f"- {_file_type_summary(file_paths)}"
            f"{dep_text}"
        )

    def generate_commit_message(self, diff_text: str, title: str) -> str:
        return title


def _unique_modules(file_paths: list[str]) -> list[str]:
    modules: list[str] = []
    for path in file_paths:
        parts = Path(path).parts
        module = parts[0] if len(parts) > 1 else Path(path).stem
        if module not in modules:
            modules.append(module)
    return modules[:3]


def _line_summary(diff_text: str) -> str:
    added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"+{added}/-{removed} lines"


def _file_type_summary(file_paths: list[str]) -> str:
    extensions = {Path(path).suffix for path in file_paths if Path(path).suffix}
    if not extensions:
        return "Mixed file types"
    return f"Languages: {', '.join(sorted(extensions))}"
