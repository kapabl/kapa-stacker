"""LLM-powered text generator — uses ollama for PR titles, summaries, commit messages."""

from __future__ import annotations

from src.domain.port.text_generator import TextGenerator
from src.domain.port.llm_service import LLMService
from src.infrastructure.llm.rule_based_generator import RuleBasedGenerator

TITLE_SYSTEM = (
    "You generate concise, descriptive PR titles from code diffs. "
    "One line only. No quotes. No prefix like 'PR:'. "
    "Use imperative mood: 'Add', 'Fix', 'Refactor', 'Update'."
)

SUMMARY_SYSTEM = (
    "You generate PR descriptions from code diffs. "
    "Write 2-3 bullet points starting with '-'. "
    "Explain what changed and why it matters. Be specific."
)

COMMIT_SYSTEM = (
    "You generate git commit messages from code diffs. "
    "First line: imperative mood, under 72 chars. "
    "Optional blank line + body for context. No quotes."
)


class LlmTextGenerator(TextGenerator):
    """Uses a local LLM for human-quality text. Falls back to rules."""

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service
        self._fallback = RuleBasedGenerator()

    def generate_title(
        self, diff_text: str, file_paths: list[str], symbols: list[str],
    ) -> str:
        if not self._llm.available:
            return self._fallback.generate_title(diff_text, file_paths, symbols)

        prompt = _build_title_prompt(diff_text, file_paths, symbols)
        response = self._llm.query(prompt, system=TITLE_SYSTEM, max_tokens=100)
        title = response.text.strip().strip('"\'')

        if not title or response.error:
            return self._fallback.generate_title(diff_text, file_paths, symbols)
        return title

    def generate_summary(
        self, diff_text: str, file_paths: list[str], depends_on: list[int],
    ) -> str:
        if not self._llm.available:
            return self._fallback.generate_summary(diff_text, file_paths, depends_on)

        prompt = _build_summary_prompt(diff_text, file_paths, depends_on)
        response = self._llm.query(prompt, system=SUMMARY_SYSTEM, max_tokens=300)

        if not response.text.strip() or response.error:
            return self._fallback.generate_summary(diff_text, file_paths, depends_on)
        return response.text.strip()

    def generate_commit_message(self, diff_text: str, title: str) -> str:
        if not self._llm.available:
            return self._fallback.generate_commit_message(diff_text, title)

        prompt = _build_commit_prompt(diff_text, title)
        response = self._llm.query(prompt, system=COMMIT_SYSTEM, max_tokens=200)

        if not response.text.strip() or response.error:
            return self._fallback.generate_commit_message(diff_text, title)
        return response.text.strip()


def _build_title_prompt(
    diff_text: str, file_paths: list[str], symbols: list[str],
) -> str:
    files_section = "\n".join(file_paths[:10])
    symbols_section = ", ".join(symbols[:5]) if symbols else "(none detected)"
    diff_preview = diff_text[:500] if diff_text else "(no diff)"

    return (
        f"Files:\n{files_section}\n\n"
        f"Key symbols: {symbols_section}\n\n"
        f"Diff:\n{diff_preview}\n\n"
        f"Write a one-line PR title:"
    )


def _build_summary_prompt(
    diff_text: str, file_paths: list[str], depends_on: list[int],
) -> str:
    files_section = "\n".join(file_paths[:10])
    deps_section = ", ".join(f"PR #{dep}" for dep in depends_on) if depends_on else "none"
    diff_preview = diff_text[:1000] if diff_text else "(no diff)"

    return (
        f"Files:\n{files_section}\n\n"
        f"Dependencies: {deps_section}\n\n"
        f"Diff:\n{diff_preview}\n\n"
        f"Write 2-3 bullet points for the PR description:"
    )


def _build_commit_prompt(diff_text: str, title: str) -> str:
    diff_preview = diff_text[:500] if diff_text else "(no diff)"

    return (
        f"PR title: {title}\n\n"
        f"Diff:\n{diff_preview}\n\n"
        f"Write a git commit message:"
    )
