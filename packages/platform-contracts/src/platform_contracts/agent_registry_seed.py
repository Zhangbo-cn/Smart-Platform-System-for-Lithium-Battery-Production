"""全平台 AgentCard 种子（A2A 服务 ID；见 docs/TERMINOLOGY.md）。"""

from platform_contracts.agent_card import AgentCard, AgentSkill, RCA_AGENT_CARD
from platform_contracts.mcp_tool_matrix import allowed_tools_for

# 控制面（均非 Agent）
CLIENT_GATEWAY_CARD = AgentCard(
    name="client-gateway",
    description="Client Gateway：用户门户，转发 Orchestrator（无 LLM）",
    url="http://localhost:8010/a2a/v1",
    capabilities=["user_gateway", "task_status_push"],
    skills=[
        AgentSkill(
            id="submit_task",
            name="提交分析任务",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "playbook": {"type": "string"},
                    "batch_id": {"type": "string"},
                },
                "required": ["message"],
            },
        )
    ],
    enabled=True,
)
CLIENT_AGENT_CARD = CLIENT_GATEWAY_CARD  # 兼容旧名

ORCHESTRATOR_CARD = AgentCard(
    name="playbook-orchestrator",
    description="Playbook Orchestrator：固定剧本委派 A2A 服务（无 LLM）",
    url="http://localhost:8020/a2a/v1",
    capabilities=["routing", "playbook_orchestration", "platform_context"],
    skills=[
        AgentSkill(
            id="dispatch",
            name="执行 Playbook",
            input_schema={
                "type": "object",
                "properties": {
                    "playbook": {"type": "string"},
                    "message": {"type": "string"},
                    "batch_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "async_mode": {"type": "boolean"},
                },
                "required": ["playbook"],
            },
        )
    ],
    enabled=True,
)
ROUTER_AGENT_CARD = ORCHESTRATOR_CARD  # 兼容旧名

CAPABILITY_REGISTRY_CARD = AgentCard(
    name="capability-registry",
    description="Capability Registry：A2A 服务登记与健康探活",
    url="http://localhost:8021/a2a/v1",
    capabilities=["agent_registry", "health_probe"],
    skills=[
        AgentSkill(
            id="list_agents",
            name="列出能力服务",
            input_schema={"type": "object", "properties": {"action": {"const": "list"}}},
        ),
        AgentSkill(
            id="get_card",
            name="获取 AgentCard",
            input_schema={
                "type": "object",
                "properties": {"action": {"const": "get_card"}, "name": {"type": "string"}},
                "required": ["action", "name"],
            },
        ),
    ],
    enabled=True,
)
REGISTRY_AGENT_CARD = CAPABILITY_REGISTRY_CARD  # 兼容旧名

PLANNER_CARD = AgentCard(
    name="planner",
    description="Planner Agent：NL → playbook + 参数（ReAct + Tool Use）",
    url="http://localhost:8011/a2a/v1",
    capabilities=["task_planning", "playbook_selection"],
    skills=[
        AgentSkill(
            id="plan",
            name="规划 Playbook",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "playbook": {"type": "string"},
                    "batch_id": {"type": "string"},
                },
                "required": ["message"],
            },
        )
    ],
    enabled=True,
)

# L0 Reporter Agent（Deep Agents；保留 report-8d-worker 服务 ID 别名）
REPORT_REPORTER_AGENT_CARD = AgentCard(
    name="report-reporter-agent",
    description="Reporter Agent：Deep Agents 动态 8D 生成 + QMS 写回",
    url="http://localhost:8004/a2a/v1",
    capabilities=["report_8d", "capa_draft"],
    mcp_servers=["qms", "knowledge"],
    allowed_tools=allowed_tools_for("report-reporter-agent"),
    enabled=True,
    skills=[
        AgentSkill(
            id="report_8d",
            name="8D 定稿报告",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "hitl_approved": {"type": "boolean"},
                },
                "required": ["session_id", "root_cause", "hitl_approved"],
            },
        )
    ],
)
REPORT_8D_WORKER_CARD = AgentCard(
    name="report-8d-worker",
    description="Reporter Agent 兼容别名（同 report-reporter-agent）",
    url="http://localhost:8004/a2a/v1",
    capabilities=["report_8d"],
    mcp_servers=["qms", "knowledge"],
    allowed_tools=allowed_tools_for("report-8d-worker"),
    enabled=True,
)
REPORT_8D_AGENT_CARD = REPORT_REPORTER_AGENT_CARD  # 兼容旧名

# L1 Worker
TRACE_WORKER_CARD = AgentCard(
    name="trace-worker",
    description="Trace Worker：批次追溯，输出 prior_evidence 供 RCA",
    url="http://localhost:8002/a2a/v1",
    capabilities=["batch_trace", "process_params"],
    mcp_servers=["mes", "scada", "erp", "lims"],
    allowed_tools=allowed_tools_for("trace-worker"),
    enabled=True,
    skills=[
        AgentSkill(
            id="trace_batch",
            name="批次追溯",
            input_schema={
                "type": "object",
                "properties": {"batch_id": {"type": "string"}},
                "required": ["batch_id"],
            },
        )
    ],
)
TRACE_AGENT_CARD = TRACE_WORKER_CARD  # 兼容旧名

TRIAGE_AGENT_CARD = AgentCard(
    name="triage-agent",
    description="异常分诊：NL → defect_type / severity / 多意图拆解（LLM + Rule 双模）",
    url="http://localhost:8001/a2a/v1",
    capabilities=["defect_classification", "severity", "intent_recognition"],
    mcp_servers=["mes"],
    allowed_tools=allowed_tools_for("triage-agent"),
    enabled=True,
    skills=[
        AgentSkill(
            id="triage",
            name="异常分诊与意图识别",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "query": {"type": "string"},
                    "batch_id": {"type": "string"},
                },
                "required": ["session_id", "query"],
            },
        )
    ],
)

QUALITY_PRED_AGENT_CARD = AgentCard(
    name="quality-prediction-agent",
    description="SPC 预警；coating_incident 入口",
    url="http://localhost:8201/a2a/v1",
    capabilities=["spc_alarm", "defect_trend"],
    mcp_servers=["mes", "scada", "lims"],
    allowed_tools=allowed_tools_for("quality-prediction-agent"),
    enabled=True,
)

PATROL_AGENT_CARD = AgentCard(
    name="patrol-agent",
    description="开班巡线摘要",
    url="http://localhost:8005/a2a/v1",
    capabilities=["patrol_summary"],
    mcp_servers=["mes", "scada"],
    allowed_tools=allowed_tools_for("patrol-agent"),
    enabled=True,
)

PROCESS_AGENT_CARD = AgentCard(
    name="process-optimization-agent",
    description="工艺参数建议",
    url="http://localhost:8202/a2a/v1",
    capabilities=["process_optimization", "parameter_recommendation"],
    mcp_servers=["mes", "scada", "knowledge"],
    allowed_tools=allowed_tools_for("process-optimization-agent"),
    enabled=True,
)

EQUIPMENT_AGENT_CARD = AgentCard(
    name="equipment-health-agent",
    description="设备预测性维护",
    url="http://localhost:8203/a2a/v1",
    capabilities=["predictive_maintenance", "equipment_telemetry"],
    mcp_servers=["scada", "eam"],
    allowed_tools=allowed_tools_for("equipment-health-agent"),
    enabled=True,
)

WMS_AGENT_CARD = AgentCard(
    name="wms-supply-agent",
    description="仓储与物料追溯",
    url="http://localhost:8204/a2a/v1",
    capabilities=["inventory_query", "material_trace"],
    mcp_servers=["wms", "erp"],
    allowed_tools=allowed_tools_for("wms-supply-agent"),
    enabled=True,
)

SAFETY_AGENT_CARD = AgentCard(
    name="safety-agent",
    description="停线改参门闩；独占 plc MCP",
    url="http://localhost:8099/a2a/v1",
    capabilities=["emergency_stop", "parameter_write_approval"],
    mcp_servers=["plc", "mes", "qms"],
    allowed_tools=allowed_tools_for("safety-agent"),
    enabled=True,
)

RCA_INTERFACE_AGENTS: list[AgentCard] = [
    TRACE_WORKER_CARD,
    RCA_AGENT_CARD,
    REPORT_REPORTER_AGENT_CARD,
]

ALL_BUSINESS_AGENT_CARDS: list[AgentCard] = [
    PATROL_AGENT_CARD,
    TRIAGE_AGENT_CARD,
    TRACE_WORKER_CARD,
    QUALITY_PRED_AGENT_CARD,
    RCA_AGENT_CARD,
    REPORT_REPORTER_AGENT_CARD,
    REPORT_8D_WORKER_CARD,
    PROCESS_AGENT_CARD,
    EQUIPMENT_AGENT_CARD,
    WMS_AGENT_CARD,
    SAFETY_AGENT_CARD,
]

ALL_REGISTERED_AGENT_CARDS: list[AgentCard] = [
    CAPABILITY_REGISTRY_CARD,
    CLIENT_GATEWAY_CARD,
    PLANNER_CARD,
    ORCHESTRATOR_CARD,
    *ALL_BUSINESS_AGENT_CARDS,
]
