from __future__ import annotations

import pytest

from harness_core.permission.checker import PermissionChecker, PermissionDenied


def test_role_required_blocks_unauthorized():
    checker = PermissionChecker()
    with pytest.raises(PermissionDenied):
        checker.check_tool(
            tool_name="erp.query_recipe",
            user_role="operator",
            sensitive=True,
            required_roles={"quality_manager"},
        )


def test_quality_manager_passes():
    checker = PermissionChecker()
    checker.check_tool(
        tool_name="erp.query_recipe",
        user_role="quality_manager",
        sensitive=True,
        required_roles={"quality_manager"},
    )


def test_agent_write_allowlist():
    checker = PermissionChecker()
    checker.check_agent_write("report-8d-agent", "qms.create_8d_draft")
    with pytest.raises(PermissionDenied):
        checker.check_agent_write("planner", "qms.create_8d_draft")
    with pytest.raises(PermissionDenied):
        checker.check_agent_write("report-8d-agent", "mes.query_batch_trace")


def test_data_scope_factory_director():
    checker = PermissionChecker()
    checker.check_data_scope("factory_director", "P1", "L1", "coating")
    with pytest.raises(PermissionDenied):
        checker.check_data_scope("operator", "P1", "L9", "coating")
