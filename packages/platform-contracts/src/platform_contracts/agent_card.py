from pydantic import BaseModel, Field

from platform_contracts.mcp_tool_matrix import allowed_tools_for


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str | None = None
    input_schema: dict = Field(default_factory=dict)


class AgentCard(BaseModel):
    """对齐 docs/A2A_PROTOCOL.md §2、docs/AGENT_CATALOG.md"""

    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: list[str]
    skills: list[AgentSkill] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="bootstrap 子集；须为 mcp_servers 下 Tool 的显式白名单，见 mcp_tool_matrix.py",
    )
    enabled: bool = True
    hitl_required_below: float | None = None
    factory_ids: list[str] | None = None


RCA_AGENT_CARD = AgentCard(
    name="quality-rca-agent",
    description="锂电质量根因分析：MCP跨域取证+FMEA规则+HITL",
    url="http://localhost:8003/a2a/v1",
    capabilities=["root_cause_analysis", "evidence_chain", "report_8d_draft"],
    skills=[
        AgentSkill(
            id="analyze_quality",
            name="质量异常根因分析",
            input_schema={
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string"},
                    "defect_type": {"type": "string"},
                    "user_query": {"type": "string"},
                },
                "required": ["batch_id"],
            },
        )
    ],
    mcp_servers=["mes", "scada", "erp", "lims", "knowledge"],
    allowed_tools=allowed_tools_for("quality-rca-agent"),
    hitl_required_below=0.7,
)
