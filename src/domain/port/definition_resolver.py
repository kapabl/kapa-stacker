"""Port: resolve a symbol reference to its definition location."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class DefinitionLocation:
    """Where a symbol is defined."""

    file_path: str
    line: int = 0
    column: int = 0


class DefinitionResolver(ABC):
    """Contract for resolving symbol references to definition locations."""

    @abstractmethod
    def resolve(
        self, file_path: str, symbol_name: str, line: int = 0,
    ) -> DefinitionLocation | None: ...

    @abstractmethod
    def find_references(
        self, file_path: str, symbol_name: str, line: int = 0,
    ) -> list[DefinitionLocation]: ...
