"""Provider manager registry."""

from __future__ import annotations

import logging
from typing import Dict, Optional, Type

from agentfactor.clients.tmux import TmuxClient
from agentfactor.models.enums import TerminalStatus
from agentfactor.providers.base import BaseProvider, ProviderInitializationError
from agentfactor.providers.claude_code import ClaudeCodeProvider
from agentfactor.providers.codex import CodexProvider
from agentfactor.providers.deepseek_provider import DeepSeekProvider
from agentfactor.providers.q_cli import QCLIProvider

LOG = logging.getLogger(__name__)


class UnknownProviderError(RuntimeError):
    """Raised when a provider key is not registered."""


class ProviderManager:
    """Factory and cache for provider instances keyed by terminal ID."""

    _registry: Dict[str, Type[BaseProvider]] = {
        "q_cli": QCLIProvider,
        "claude_code": ClaudeCodeProvider,
        "codex": CodexProvider,
        "deepseek": DeepSeekProvider,
        "deepcode": DeepSeekProvider,
    }

    def __init__(self, tmux: Optional[TmuxClient] = None) -> None:
        self.tmux = tmux or TmuxClient()
        self._providers: Dict[str, BaseProvider] = {}

    def create_provider(
        self,
        provider_key: str,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str],
    ) -> BaseProvider:
        if provider_key not in self._registry:
            raise UnknownProviderError(f"Provider '{provider_key}' is not registered.")

        provider_cls = self._registry[provider_key]
        provider = provider_cls(
            terminal_id=terminal_id,
            session_name=session_name,
            window_name=window_name,
            agent_profile=agent_profile,
            tmux=self.tmux,
        )
        try:
            provider.initialize()
        except ProviderInitializationError:
            raise
        self._providers[terminal_id] = provider
        return provider

    def get_provider(self, terminal_id: str) -> BaseProvider:
        if terminal_id not in self._providers:
            raise UnknownProviderError(f"Provider for terminal '{terminal_id}' is not loaded.")
        return self._providers[terminal_id]

    def attach_provider(
        self,
        provider_key: str,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str],
    ) -> BaseProvider:
        """Attach to an existing tmux window without re-initializing the provider process."""
        existing = self._providers.get(terminal_id)
        if existing:
            return existing

        if provider_key not in self._registry:
            raise UnknownProviderError(f"Provider '{provider_key}' is not registered.")

        provider_cls = self._registry[provider_key]
        provider = provider_cls(
            terminal_id=terminal_id,
            session_name=session_name,
            window_name=window_name,
            agent_profile=agent_profile,
            tmux=self.tmux,
        )
        self._providers[terminal_id] = provider
        return provider

    def cleanup_provider(self, terminal_id: str) -> None:
        provider = self._providers.pop(terminal_id, None)
        if provider:
            try:
                provider.cleanup()
            except Exception:  # pragma: no cover - defensive guard
                LOG.warning("Provider cleanup failed for %s", terminal_id, exc_info=True)

    def status(self, terminal_id: str) -> TerminalStatus:
        provider = self.get_provider(terminal_id)
        return provider.get_status()

    def iter_providers(self):
        return self._providers.items()
