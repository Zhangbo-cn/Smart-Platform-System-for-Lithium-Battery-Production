from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.agents import ExecutorAgent, PlannerAgent, ReflectorAgent, ReporterAgent
from agent.state import QualityAnalysisState
from agent.tools.registry import ToolRegistry
from harness_core.context.compressor import ContextCompressor


def _route_after_reflector(state: QualityAnalysisState) -> str:
    if state.get("need_more_data"):
        return "executor"
    if state.get("requires_hitl"):
        return "hitl"
    return "reporter"


async def _hitl_node(state: QualityAnalysisState) -> dict:
    """
    Pause for human review. LangGraph persists checkpoint before interrupt();
    resume via Command(resume=feedback) with the same thread_id.
    """
    payload = {
        "trace_id": state.get("trace_id"),
        "confidence": state.get("confidence", 0.0),
        "partial_result": state.get("partial_result"),
        "evidence": state.get("evidence", []),
        "root_cause": state.get("root_cause", ""),
        "defect_type": state.get("defect_type"),
    }
    feedback = interrupt(payload)
    if not isinstance(feedback, dict):
        feedback = {"approved": bool(feedback), "feedback": str(feedback)}

    updates: dict = {
        "hitl_response": feedback,
        "requires_hitl": False,
        "status": "reporting",
    }
    if feedback.get("root_cause"):
        updates["root_cause"] = feedback["root_cause"]
    elif feedback.get("approved") and state.get("partial_result", {}).get("suspected"):
        suspected = state["partial_result"]["suspected"]
        updates["root_cause"] = f"人工确认疑似根因：{suspected[0]}"
    if feedback.get("recommendations"):
        updates["recommendations"] = feedback["recommendations"]
    return updates


def build_quality_analysis_graph(
    registry: ToolRegistry,
    compressor: ContextCompressor | None = None,
    checkpointer=None,
):
    planner = PlannerAgent()
    executor = ExecutorAgent(registry=registry, compressor=compressor or ContextCompressor())
    reflector = ReflectorAgent()
    reporter = ReporterAgent()

    graph = StateGraph(QualityAnalysisState)
    graph.add_node("planner", planner.run)
    graph.add_node("executor", executor.run)
    graph.add_node("reflector", reflector.run)
    graph.add_node("hitl", _hitl_node)
    graph.add_node("reporter", reporter.run)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "reflector")
    graph.add_conditional_edges(
        "reflector",
        _route_after_reflector,
        {"executor": "executor", "hitl": "hitl", "reporter": "reporter"},
    )
    graph.add_edge("hitl", "reporter")
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
