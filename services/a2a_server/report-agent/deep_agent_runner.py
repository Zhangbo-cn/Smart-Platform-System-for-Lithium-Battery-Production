"""Reporter Deep Agents：主 Agent + 子 Agent 动态生成 8D。"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import structlog
from langchain_core.tools import StructuredTool
from platform_contracts.agent_handoffs import Report8dRequest

from report_runner import run_report_8d
from report_tools import INTERNAL_REPORT_TOOLS, bind_report_context
from settings import Report8dSettings

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """你是锂电质量平台的 Reporter Agent（8D 定稿）。

约束：
1. D4 根因和 D6 证据已在用户消息中预格式化，原样使用，禁止改写。
2. D5 纠正措施委派给子 Agent d5_capa_planner 生成（他会查 SOP / Golden Case）。
3. D2 问题描述、D3 临时措施、D7 预防措施、D8 总结由你直接生成。
4. 将全部章节组装为完整 Markdown，写入虚拟文件 /draft/8d.md。
5. 终稿后调用 MCP 工具 qms.create_8d_draft 提交 QMS。
6. 置信度 <0.75 时在 D5 首条加「低置信度：建议质量经理二次签核后再关闭 CAPA」。

注意：D4 和 D6 已就绪，不要再调任何 tool 去获取它们。直接使用消息中 [D4_PREFORMATTED] 和 [D6_PREFORMATTED] 的内容。"""


def _mcp_tools_from_registry(registry) -> list:
    """将 ToolRegistry 中已注册 MCP 工具包装为 LangChain tools。"""
    from platform_contracts.mcp_tool_matrix import allowed_tools_for

    lc_tools = []
    for full_name in allowed_tools_for("report-reporter-agent"):
        short = full_name.replace(".", "_")

        async def _handler(_tool=full_name, **args: Any):
            return await registry.invoke(
                _tool,
                args,
                user_id="report-reporter-agent",
                user_role="quality_manager",
            )

        lc_tools.append(
            StructuredTool.from_function(
                coroutine=_handler,
                name=short,
                description=f"MCP tool {full_name}",
            )
        )
    return lc_tools


def _build_subagents(mcp_tools: list | None = None) -> list[dict[str, Any]]:
    """只保留 d5 作为子 Agent——它有搜索歧义，需要 LLM 判断。
    d4 和 d6 是纯确定性操作（粘贴根因 + 编号列表），降级为普通 tool，不消耗 LLM。"""
    # 从 MCP 中提取 hybrid_search 工具（若有）
    extra_tool_names = set()
    if mcp_tools:
        for t in mcp_tools:
            name = getattr(t, "name", None) or (t.name if hasattr(t, "name") else "")
            if "hybrid_search_golden_case" in name:
                extra_tool_names.add(name)

    d5_tools = [t for t in INTERNAL_REPORT_TOOLS if t.name in ("search_sop", "search_golden_case", "get_rca_artifacts")]

    hybrid_tool_prompt = ""
    if extra_tool_names:
        d5_tools.extend(t for t in mcp_tools if getattr(t, "name", None) in extra_tool_names)
        hybrid_tool_prompt = (
            "优先使用 hybrid_search_golden_case 混合检索（向量+图）："
            "它会同时基于语义相似和 FMEA 因果路径召回历史案例，比 search_golden_case 更全面。"
        )

    return [
        {
            "name": "d5_capa_planner",
            "description": "撰写 D5 纠正措施；可参考 SOP 与历史 Golden Case",
            "system_prompt": (
                "你负责 D5 纠正措施。先调用 get_rca_artifacts 了解根因背景，"
                f"再调用 search_sop 或 search_golden_case 检索参考。{hybrid_tool_prompt}"
                "最后生成可执行的纠正措施。"
                "措施必须可执行，不得推翻 D4 根因。输出 Markdown 列表。"
            ),
            "tools": d5_tools,
        },
    ]


def _init_chat_model(settings: Report8dSettings):
    from langchain_openai import ChatOpenAI

    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("LLM not configured")
    return ChatOpenAI(
        base_url=settings.llm_base_url.rstrip("/"),
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )


def _user_message(req: Report8dRequest) -> str:
    artifacts = req.rca_artifacts
    if hasattr(artifacts, "model_dump"):
        artifacts = artifacts.model_dump()

    # 预格式化 D4（确定性操作，不需要 LLM 调 tool）
    d4_block = f"## D4 根因\n\n{req.root_cause or '（未锁定根因）'}\n"

    # 预格式化 D6（确定性操作，不需要 LLM 调 tool）
    evidence = req.evidence or []
    if evidence:
        d6_lines = ["## D6 证据摘要\n"]
        for i, ev in enumerate(evidence[:12], start=1):
            if isinstance(ev, dict):
                desc = ev.get("description") or ev.get("note") or ev.get("source_tool") or str(ev)
            else:
                desc = str(ev)
            d6_lines.append(f"{i}. {desc}")
        d6_block = "\n".join(d6_lines) + "\n"
    else:
        d6_block = "## D6 证据摘要\n\n- （无结构化证据，见 RCA 会话）\n"

    return (
        f"Session: {req.session_id}\n"
        f"Factory: {req.factory_id or 'N/A'}\n"
        f"Defect: {req.defect_type or 'unknown'}\n"
        f"Confidence: {req.confidence}\n"
        f"Recommendations: {json.dumps(req.recommendations, ensure_ascii=False)}\n"
        f"Evidence count: {len(evidence)}\n"
        f"RCA artifacts: {json.dumps(artifacts or {}, ensure_ascii=False)}\n\n"
        "--- 以下是预格式化章节，原样使用，禁止改写 ---\n\n"
        f"[D4_PREFORMATTED]\n{d4_block}\n"
        f"[D6_PREFORMATTED]\n{d6_block}\n"
        "--- 预格式化章节结束 ---\n\n"
        "任务：\n"
        "1. 将 D4_PREFORMATTED 原样复制到报告中（不调任何 tool）\n"
        "2. 委派 d5_capa_planner 子 Agent 生成 D5 纠正措施\n"
        "3. 将 D6_PREFORMATTED 原样复制到报告中（不调任何 tool）\n"
        "4. 生成 D2/D3/D7/D8 章节\n"
        "5. 组装完整 8D Markdown，写入 /draft/8d.md\n"
        "6. 调用 qms MCP 工具提交 QMS\n"
        "禁止调用 get_locked_root_cause 或 get_rca_artifacts —— D4/D6 已就绪。"
    )


async def _extract_report_md(result: dict[str, Any], req: Report8dRequest) -> str:
    """从 Deep Agent 结果或虚拟文件中提取报告正文。"""
    messages = result.get("messages") or []
    for msg in reversed(messages):
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
        if isinstance(content, str) and "# 8D" in content:
            return content
    files = result.get("files") or {}
    draft = files.get("/draft/8d.md") or files.get("draft/8d.md")
    if isinstance(draft, dict) and draft.get("content"):
        return draft["content"]
    if isinstance(draft, str):
        return draft
    return _fallback_report_md(req)


def _fallback_report_md(req: Report8dRequest) -> str:
    lines = ["# 8D 质量报告", "", f"**Session**: `{req.session_id}`", "", "## D4 根因", req.root_cause, ""]
    if req.recommendations:
        lines.append("## D5 纠正措施")
        for r in req.recommendations:
            lines.append(f"- {r}")
    return "\n".join(lines)


async def run_report_with_deep_agent(
    registry,
    req: Report8dRequest,
    settings: Report8dSettings,
) -> tuple[str, list[str], str | None, str | None, Literal["deep_agent", "template"]]:
    bind_report_context(req.model_dump(mode="json"))
    started = time.perf_counter()

    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        logger.warning("reporter.deepagents_missing", error=str(exc))
        md, recs, capa_id, qms_status = await run_report_8d(registry, req)
        return md, recs, capa_id, qms_status, "template"

    if settings.reporter_mode != "deep_agent":
        md, recs, capa_id, qms_status = await run_report_8d(registry, req)
        return md, recs, capa_id, qms_status, "template"

    try:
        model = _init_chat_model(settings)
    except ValueError:
        logger.warning("reporter.llm_not_configured")
        md, recs, capa_id, qms_status = await run_report_8d(registry, req)
        return md, recs, capa_id, qms_status, "template"

    mcp_tools = _mcp_tools_from_registry(registry)
    all_tools = INTERNAL_REPORT_TOOLS + mcp_tools
    subagents = _build_subagents(mcp_tools)

    agent = create_deep_agent(
        model=model,
        tools=all_tools,
        system_prompt=_SYSTEM_PROMPT,
        subagents=subagents,
    )

    callbacks = _langsmith_callbacks(settings)
    invoke_kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": _user_message(req)}]}
    config: dict[str, Any] = {
        "recursion_limit": 25,  # 1 子 Agent + 无循环工具，25 步安全（框架内部节点也占步数）
    }
    if callbacks:
        config["callbacks"] = callbacks

    try:
        result = await agent.ainvoke(invoke_kwargs, config=config)
    except Exception as exc:
        logger.exception("reporter.deep_agent_failed", error=str(exc))
        md, recs, capa_id, qms_status = await run_report_8d(registry, req)
        return md, recs, capa_id, qms_status, "template"

    report_md = await _extract_report_md(result, req)
    recommendations = list(req.recommendations or [])
    if req.confidence is not None and req.confidence < 0.75:
        recommendations = ["低置信度：建议质量经理二次签核后再关闭 CAPA", *recommendations]

    capa_id: str | None = None
    qms_status: str | None = None
    try:
        draft_raw = await registry.invoke(
            "qms.create_8d_draft",
            {
                "session_id": req.session_id,
                "title": f"8D-{req.session_id[-8:]}",
                "report_md": report_md,
                "root_cause": req.root_cause,
            },
            user_id="report-reporter-agent",
            user_role="quality_manager",
        )
        if isinstance(draft_raw, list) and draft_raw:
            text = draft_raw[0].get("text", "") if isinstance(draft_raw[0], dict) else str(draft_raw[0])
            try:
                parsed = json.loads(text)
                capa_id = parsed.get("capa_id") or parsed.get("id")
            except json.JSONDecodeError:
                capa_id = None
        status_raw = await registry.invoke(
            "qms.update_capa_status",
            {"session_id": req.session_id, "capa_id": capa_id, "status": "draft"},
            user_id="report-reporter-agent",
            user_role="quality_manager",
        )
        if isinstance(status_raw, list) and status_raw:
            qms_status = "draft"
    except Exception as exc:
        logger.warning("reporter.qms_write_failed", error=str(exc))

    logger.info(
        "reporter.deep_agent_done",
        duration_ms=int((time.perf_counter() - started) * 1000),
        capa_id=capa_id,
    )
    return report_md, recommendations, capa_id, qms_status, "deep_agent"


def _langsmith_callbacks(settings: Report8dSettings) -> list:
    if not settings.langsmith_api_key:
        return []
    import os

    os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
    try:
        from langchain_core.tracers import LangChainTracer

        return [LangChainTracer(project_name=settings.langsmith_project)]
    except ImportError:
        return []
