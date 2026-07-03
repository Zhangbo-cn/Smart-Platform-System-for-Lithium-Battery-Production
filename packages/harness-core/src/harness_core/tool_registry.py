from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog

from harness_core.audit.tracer import AuditTracer
from harness_core.permission.checker import PermissionChecker

logger = structlog.get_logger(__name__)
ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass
class ToolSpec:
    name: str
    server: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    sensitive: bool = False
    required_roles: set[str] = field(default_factory=set)


class ToolRegistry:
    """MCP 工具注册表（非 Capability Registry）。"""

    def __init__(
        self,
        permission_checker: PermissionChecker | None = None,
        audit_tracer: AuditTracer | None = None,
    ) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._permission = permission_checker or PermissionChecker()
        self._audit = audit_tracer or AuditTracer()

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def list_tools(self, role: str | None = None) -> list[dict[str, Any]]:
        out = []
        for spec in self._tools.values():
            if role and spec.required_roles and role not in spec.required_roles:
                continue
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.parameters,
                }
            )
        return out

    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        user_id: str,
        user_role: str,
        caller_agent: str = "",
    ) -> Any:
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        spec = self._tools[name]
        self._permission.check_tool(
            tool_name=name,
            user_role=user_role,
            sensitive=spec.sensitive,
            required_roles=spec.required_roles,
            caller_agent=caller_agent,
        )
        with self._audit.span(
            f"tool.{name}",
            attributes={"user_id": user_id, "role": user_role, "args": args},
        ):
            return await spec.handler(args)
