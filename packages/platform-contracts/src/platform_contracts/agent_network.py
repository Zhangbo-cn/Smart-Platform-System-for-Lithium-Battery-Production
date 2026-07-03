"""AgentNetwork：name → AgentCard + base URL（SmartVoyage 同名概念）。"""

from __future__ import annotations

from platform_contracts.agent_card import AgentCard


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

    def base_url(self, name: str) -> str:
        if name in self._url_overrides:
            return self._url_overrides[name].rstrip("/")
        card = self.get_card(name)
        url = card.url.rstrip("/")
        suffix = "/a2a/v1"
        if url.endswith(suffix):
            return url[: -len(suffix)]
        return url

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
