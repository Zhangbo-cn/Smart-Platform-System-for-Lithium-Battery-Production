"""Agent × MCP × Tool 绑定：Registry / bootstrap 的权威清单。

命名：`{server}.{tool_name}`，与姊妹仓 `agent/tools/bootstrap.py` 及 `mcp_servers/` 对齐。
"""

from __future__ import annotations

from typing import Literal

ToolStatus = Literal["implemented", "planned", "in_process"]

# ----- MCP Server 全量 Tool 目录 -----

MCP_SERVER_TOOLS: dict[str, dict[str, dict[str, object]]] = {
    "mes": {
        "query_batch_trace": {
            "description": "批次/电芯工站追溯",
            "status": "implemented",
            "sensitive": False,
        },
        "query_defect_cells": {
            "description": "时间窗内缺陷电芯列表",
            "status": "implemented",
            "sensitive": False,
        },
        "get_process_params": {
            "description": "批次工序参数",
            "status": "implemented",
            "sensitive": False,
        },
        "get_shift_summary": {
            "description": "开班巡线摘要",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "scada": {
        "query_equipment_timeseries": {
            "description": "设备传感器时序",
            "status": "implemented",
            "sensitive": False,
        },
        "detect_anomaly_window": {
            "description": "设备异常时间窗检测",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "erp": {
        "query_material_batch": {
            "description": "原材料批次与供应商",
            "status": "implemented",
            "sensitive": False,
        },
        "query_recipe": {
            "description": "BOM/配方（敏感）",
            "status": "implemented",
            "sensitive": True,
            "required_roles": ["quality_manager", "factory_director", "group_it"],
        },
    },
    "lims": {
        "query_cell_test": {
            "description": "单电芯电测结果",
            "status": "implemented",
            "sensitive": False,
        },
        "batch_test_summary": {
            "description": "批次电测统计",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "knowledge": {
        "search_fmea": {
            "description": "FMEA 因果树检索（Neo4j 图路径 + 关键词）",
            "status": "implemented",
            "sensitive": False,
            "note": "基于 Neo4j 因果路径匹配",
        },
        "search_sop": {
            "description": "SOP/作业指导书",
            "status": "implemented",
            "sensitive": False,
            "note": "当前为关键词匹配，P2 升级语义搜索",
        },
        "search_golden_case": {
            "description": "历史 Golden Case 检索（关键词匹配，兼容旧版）",
            "status": "implemented",
            "sensitive": False,
            "note": "推荐使用 hybrid_search_golden_case",
        },
        "hybrid_search_golden_case": {
            "description": "向量(Milvus) + 图(Neo4j) 混合检索历史 Golden Case，按 RRF 融合排序",
            "status": "implemented",
            "sensitive": False,
            "note": "RCA Agent Reflector 节点优先使用此 tool",
        },
    },
    "qms": {
        "create_8d_draft": {
            "description": "创建 8D/CAPA 草稿",
            "status": "implemented",
            "sensitive": False,
        },
        "update_capa_status": {
            "description": "更新 CAPA 状态",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "wms": {
        "get_inventory": {
            "description": "库存查询",
            "status": "implemented",
            "sensitive": False,
        },
        "trace_material_location": {
            "description": "物料库位追溯",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "eam": {
        "get_maintenance_log": {
            "description": "维保记录",
            "status": "implemented",
            "sensitive": False,
        },
        "get_work_orders": {
            "description": "工单列表",
            "status": "implemented",
            "sensitive": False,
        },
    },
    "plc": {
        "emergency_stop": {
            "description": "紧急停线",
            "status": "implemented",
            "sensitive": True,
            "exclusive_agent": "safety-agent",
        },
        "write_setpoint": {
            "description": "写 PLC 设定值",
            "status": "implemented",
            "sensitive": True,
            "exclusive_agent": "safety-agent",
        },
    },
}


def _t(server: str, tool: str) -> str:
    return f"{server}.{tool}"


# ----- 各 Agent bootstrap 允许的 Tool 子集（AgentCard.allowed_tools） -----

AGENT_ALLOWED_TOOLS: dict[str, list[str]] = {
    "quality-rca-agent": [
        _t("mes", "query_batch_trace"),
        _t("mes", "query_defect_cells"),
        _t("mes", "get_process_params"),
        _t("scada", "query_equipment_timeseries"),
        _t("scada", "detect_anomaly_window"),
        _t("erp", "query_material_batch"),
        _t("erp", "query_recipe"),
        _t("lims", "query_cell_test"),
        _t("lims", "batch_test_summary"),
        _t("knowledge", "search_fmea"),
        _t("knowledge", "search_golden_case"),
        _t("knowledge", "hybrid_search_golden_case"),
    ],
    "trace-agent": [
        _t("mes", "query_batch_trace"),
        _t("mes", "get_process_params"),
        _t("scada", "query_equipment_timeseries"),
        _t("erp", "query_material_batch"),
        _t("lims", "batch_test_summary"),
    ],
    "trace-worker": [
        _t("mes", "query_batch_trace"),
        _t("mes", "get_process_params"),
        _t("scada", "query_equipment_timeseries"),
        _t("erp", "query_material_batch"),
        _t("lims", "batch_test_summary"),
    ],
    "triage-agent": [
        _t("mes", "query_defect_cells"),
        _t("mes", "get_process_params"),
    ],
    "report-8d-agent": [
        _t("qms", "create_8d_draft"),
        _t("qms", "update_capa_status"),
        _t("knowledge", "search_sop"),
        _t("knowledge", "search_golden_case"),
    ],
    "report-reporter-agent": [
        _t("qms", "create_8d_draft"),
        _t("qms", "update_capa_status"),
        _t("knowledge", "search_sop"),
        _t("knowledge", "search_golden_case"),
    ],
    "report-8d-worker": [
        _t("qms", "create_8d_draft"),
        _t("qms", "update_capa_status"),
        _t("knowledge", "search_sop"),
        _t("knowledge", "search_golden_case"),
    ],
    "quality-prediction-agent": [
        _t("mes", "query_defect_cells"),
        _t("scada", "detect_anomaly_window"),
        _t("lims", "batch_test_summary"),
    ],
    "patrol-agent": [
        _t("mes", "get_shift_summary"),
        _t("mes", "query_defect_cells"),
        _t("scada", "detect_anomaly_window"),
    ],
    "process-optimization-agent": [
        _t("mes", "get_process_params"),
        _t("scada", "query_equipment_timeseries"),
        _t("knowledge", "search_sop"),
    ],
    "equipment-health-agent": [
        _t("scada", "query_equipment_timeseries"),
        _t("scada", "detect_anomaly_window"),
        _t("eam", "get_maintenance_log"),
        _t("eam", "get_work_orders"),
    ],
    "wms-supply-agent": [
        _t("wms", "get_inventory"),
        _t("wms", "trace_material_location"),
        _t("erp", "query_material_batch"),
    ],
    "safety-agent": [
        _t("plc", "emergency_stop"),
        _t("plc", "write_setpoint"),
        _t("mes", "get_process_params"),
        _t("qms", "update_capa_status"),
    ],
}


def allowed_tools_for(agent_name: str) -> list[str]:
    return list(AGENT_ALLOWED_TOOLS.get(agent_name, []))


def tool_policies_for(
    agent_name: str,
) -> tuple[frozenset[str], dict[str, set[str]]]:
    """从矩阵推导 sensitive_tools 与 role_restricted（bootstrap 单一事实源）。"""
    sensitive: set[str] = set()
    role_restricted: dict[str, set[str]] = {}
    for full in AGENT_ALLOWED_TOOLS.get(agent_name, []):
        server, tool = full.split(".", 1)
        meta = MCP_SERVER_TOOLS.get(server, {}).get(tool, {})
        if meta.get("sensitive"):
            sensitive.add(full)
        roles = meta.get("required_roles")
        if roles:
            role_restricted[full] = set(roles)  # type: ignore[arg-type]
    return frozenset(sensitive), role_restricted


def servers_for_agent(agent_name: str) -> list[str]:
    """从 allowed_tools 反推涉及的 MCP Server（与 AgentCard.mcp_servers 应对齐）。"""
    servers: list[str] = []
    for full in AGENT_ALLOWED_TOOLS.get(agent_name, []):
        server = full.split(".", 1)[0]
        if server not in servers:
            servers.append(server)
    return servers
