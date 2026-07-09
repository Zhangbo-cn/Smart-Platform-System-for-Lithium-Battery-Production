"""Agent 模板验证测试。"""

from __future__ import annotations

from app_template import AgentConfig, _build_agent_card


class TestAgentConfig:
    def test_default_config(self):
        cfg = AgentConfig()
        assert cfg.name == "example-agent"
        assert cfg.port == 8200

    def test_custom_config(self):
        cfg = AgentConfig(
            name="my-agent",
            description="自定义 Agent",
            port=8300,
            capabilities=["quality_rca"],
        )
        assert cfg.name == "my-agent"
        assert cfg.port == 8300


class TestBuildAgentCard:
    def test_card_from_config(self):
        cfg = AgentConfig(
            name="test-agent",
            description="测试",
            capabilities=["test_cap"],
            mcp_servers={"mes": "http://localhost:8101/sse"},
            agent_url="http://localhost:8888",
        )
        card = _build_agent_card(cfg)
        assert card.name == "test-agent"
        assert card.url == "http://localhost:8888/a2a/v1"
        assert "test_cap" in card.capabilities
        assert "mes" in card.mcp_servers

    def test_card_skills_match_capabilities(self):
        cfg = AgentConfig(name="a", description="b", capabilities=["cap1", "cap2"])
        card = _build_agent_card(cfg)
        assert len(card.skills) == 2
        assert card.skills[0].id == "cap1"

    def test_card_defaults(self):
        cfg = AgentConfig(name="minimal", description="x")
        card = _build_agent_card(cfg)
        assert card.enabled is True
        assert card.version == "1.0.0"
