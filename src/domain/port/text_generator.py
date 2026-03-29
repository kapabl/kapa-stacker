"""Port: generate human-quality text from structured analysis data."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TextGenerator(ABC):
    """Contract for generating PR titles, summaries, and commit messages."""

    @abstractmethod
    def generate_title(
        self, diff_text: str, file_paths: list[str], symbols: list[str],
    ) -> str: ...

    @abstractmethod
    def generate_summary(
        self, diff_text: str, file_paths: list[str], depends_on: list[int],
    ) -> str: ...

    @abstractmethod
    def generate_commit_message(self, diff_text: str, title: str) -> str: ...
