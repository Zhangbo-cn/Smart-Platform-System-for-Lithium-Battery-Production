from __future__ import annotations

import structlog

from harness_core.permission.rbac import RBACPolicy

logger = structlog.get_logger(__name__)


class PermissionDenied(Exception):
    pass


class PermissionChecker:
    AGENT_WRITE_ALLOWLIST: dict[str, set[str]] = {
        "report-8d-agent": {"qms.create_8d_draft", "qms.update_capa_status"},
        "report-8d-worker": {"qms.create_8d_draft", "qms.update_capa_status"},
    }

    # 独占工具：只有声明的 Agent 可以调用（如 plc.emergency_stop 仅 safety-agent）
    # 启动时由 bootstrap 根据 mcp_tool_matrix 注入
    EXCLUSIVE_TOOLS: dict[str, str] = {}  # tool_full_name → exclusive_agent_name

    def __init__(self, rbac: RBACPolicy | None = None) -> None:
        self.rbac = rbac or RBACPolicy()

    def check_tool(
        self,
        tool_name: str,
        user_role: str,
        sensitive: bool,
        required_roles: set[str],
        caller_agent: str = "",
    ) -> None:
        # 独占工具检查：非声明的 Agent 禁止调用
        exclusive_owner = self.EXCLUSIVE_TOOLS.get(tool_name)
        if exclusive_owner and caller_agent != exclusive_owner:
            raise PermissionDenied(
                f"Tool '{tool_name}' is exclusive to '{exclusive_owner}', "
                f"'{caller_agent}' is not authorized"
            )
        if required_roles and user_role not in required_roles:
            logger.warning(
                "permission.denied",
                tool=tool_name,
                role=user_role,
                required=list(required_roles),
            )
            raise PermissionDenied(
                f"Role '{user_role}' is not allowed to call tool '{tool_name}'"
            )
        if sensitive:
            logger.info("permission.sensitive_tool_invoked", tool=tool_name, role=user_role)

    def check_agent_write(self, agent_name: str, target_table: str) -> None:
        allowed = self.AGENT_WRITE_ALLOWLIST.get(agent_name, set())
        if target_table not in allowed:
            raise PermissionDenied(
                f"Agent '{agent_name}' is not allowed to write to '{target_table}'"
            )

    def check_data_scope(self, user_role: str, plant: str, line: str, process: str) -> None:
        if not self.rbac.can_read(user_role, plant, line, process):
            raise PermissionDenied(
                f"Role '{user_role}' cannot access plant={plant} line={line} process={process}"
            )
