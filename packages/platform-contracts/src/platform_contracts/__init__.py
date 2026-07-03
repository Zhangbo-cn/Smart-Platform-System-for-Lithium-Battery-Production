"""跨服务契约：AgentCard、PlatformContext、TaskState。"""

from platform_contracts.a2a import A2AClient, A2AError, mount_a2a
from platform_contracts.a2a_server import AsyncA2AServer
from platform_contracts.agent_network import AgentNetwork
from platform_contracts.agent_card import AgentCard, AgentSkill
from platform_contracts.agent_handoffs import (
    RcaArtifactDraft,
    RcaHitlResolveRequest,
    RcaInvokeRequest,
    Report8dRequest,
    Report8dResponse,
    TraceRequest,
    TraceResponse,
    TriageRequest,
    TriageResponse,
)
from platform_contracts.agent_registry_seed import (
    ALL_BUSINESS_AGENT_CARDS,
    ALL_REGISTERED_AGENT_CARDS,
    CAPABILITY_REGISTRY_CARD,
    CLIENT_AGENT_CARD,
    CLIENT_GATEWAY_CARD,
    ORCHESTRATOR_CARD,
    PLANNER_CARD,
    RCA_AGENT_CARD,
    RCA_INTERFACE_AGENTS,
    REGISTRY_AGENT_CARD,
    REPORT_8D_AGENT_CARD,
    REPORT_REPORTER_AGENT_CARD,
    REPORT_8D_WORKER_CARD,
    ROUTER_AGENT_CARD,
    TRACE_AGENT_CARD,
    TRACE_WORKER_CARD,
    TRIAGE_AGENT_CARD,
)
from platform_contracts.plan_result import PlanParams, PlanResult, PlannerRequest
from platform_contracts.mcp_tool_matrix import (
    AGENT_ALLOWED_TOOLS,
    MCP_SERVER_TOOLS,
    allowed_tools_for,
    tool_policies_for,
)
from platform_contracts.platform_context import PlatformContext
from platform_contracts.task_events import (
    TERMINAL_TASK_EVENTS,
    AgentHealthRecord,
    AgentHealthStatus,
    TaskEvent,
    TaskEventType,
)
from platform_contracts.task_state import TaskState

__all__ = [
    "A2AClient",
    "A2AError",
    "mount_a2a",
    "AsyncA2AServer",
    "AgentNetwork",
    "AgentCard",
    "AgentSkill",
    "PlatformContext",
    "TaskState",
    "TaskEvent",
    "TaskEventType",
    "TERMINAL_TASK_EVENTS",
    "AgentHealthRecord",
    "AgentHealthStatus",
    "TraceRequest",
    "TraceResponse",
    "TriageRequest",
    "TriageResponse",
    "Report8dRequest",
    "Report8dResponse",
    "RcaArtifactDraft",
    "RcaInvokeRequest",
    "RcaHitlResolveRequest",
    "RCA_AGENT_CARD",
    "TRACE_AGENT_CARD",
    "TRIAGE_AGENT_CARD",
    "REPORT_8D_AGENT_CARD",
    "RCA_INTERFACE_AGENTS",
    "ALL_BUSINESS_AGENT_CARDS",
    "ALL_REGISTERED_AGENT_CARDS",
    "PlannerRequest",
    "PlanResult",
    "PlanParams",
    "CLIENT_GATEWAY_CARD",
    "ORCHESTRATOR_CARD",
    "CAPABILITY_REGISTRY_CARD",
    "PLANNER_CARD",
    "TRACE_WORKER_CARD",
    "REPORT_REPORTER_AGENT_CARD",
    "REPORT_8D_WORKER_CARD",
    "CLIENT_AGENT_CARD",
    "ROUTER_AGENT_CARD",
    "REGISTRY_AGENT_CARD",
    "MCP_SERVER_TOOLS",
    "AGENT_ALLOWED_TOOLS",
    "allowed_tools_for",
    "tool_policies_for",
]
