"""AgentNetwork：name → AgentCard + base URL（SmartVoyage 同名概念）。

支持静态种子注册和动态 Registry 发现两种模式。
"""

from __future__ import annotations

import httpx
import structlog

from platform_contracts.agent_card import AgentCard

logger = structlog.get_logger(__name__)


class AgentNetwork:
    def __init__(self, name: str = "platform") -> None:
        self.name = name
        self._cards: dict[str, AgentCard] = {}
        self._url_overrides: dict[str, str] = {}

    def add(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def add_url(self, service_name: str, base_url: str) -> None:
        self._url_overrides[service_name] = base_url.rstrip("/")

    def get_card(self, name: str) -> AgentCard:
        if name not in self._cards:
            raise KeyError(f"service not in network: {name}")
        return self._cards[name]

    def list_enabled(self) -> list[AgentCard]:
        return [c for c in self._cards.values() if c.enabled]

    def list_all(self) -> list[AgentCard]:
        return list(self._cards.values())

    def base_url(self, name: str) -> str:
        if name in self._url_overrides:
            return self._url_overrides[name].rstrip("/")
        card = self.get_card(name)
        url = card.url.rstrip("/")
        suffix = "/a2a/v1"
        if url.endswith(suffix):
            return url[: -len(suffix)]
        return url

    async def discover_from_registry(
        self,
        registry_url: str,
        *,
        timeout: float = 5.0,
        merge: bool = True,
    ) -> list[AgentCard]:
        """从 Capability Registry 动态发现 Agent，合并到当前网络。

        Args:
            registry_url: Registry 的 base URL
            merge: True = 合并到现有 _cards（动态覆盖静态），
                   False = 只返回不合并
        Returns:
            发现的 AgentCard 列表
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{registry_url.rstrip('/')}/registry/agents")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("network.discover_failed", registry=registry_url, error=str(exc))
            return []

        discovered: list[AgentCard] = []
        for item in data if isinstance(data, list) else data.get("agents", data):
            if isinstance(item, dict):
                try:
                    card = AgentCard(**item)
                    discovered.append(card)
                except Exception as exc:
                    logger.warning("network.parse_card_failed", name=item.get("name"), error=str(exc))

        if merge:
            for card in discovered:
                self._cards[card.name] = card

        return discovered

    @classmethod
    def from_cards(
        cls,
        cards: list[AgentCard],
        *,
        name: str = "platform",
        url_overrides: dict[str, str] | None = None,
    ) -> AgentNetwork:
        net = cls(name=name)
        for card in cards:
            net.add(card)
        if url_overrides:
            for svc, url in url_overrides.items():
                net.add_url(svc, url)
        return net
