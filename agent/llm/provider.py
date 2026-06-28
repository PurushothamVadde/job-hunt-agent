"""LLMProvider Protocol — depend on this, not on a concrete client."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Swap implementations for testing or to change providers."""

    async def complete_json(
        self, system: str, user: str, *, temperature: float = 0.1
    ) -> dict[str, Any]: ...

    async def complete_text(
        self, system: str, user: str, *, temperature: float = 0.3
    ) -> str: ...
