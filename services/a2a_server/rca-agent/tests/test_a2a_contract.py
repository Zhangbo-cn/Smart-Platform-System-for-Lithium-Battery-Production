"""A2A 契约：RcaInvokeRequest / AnalysisResponse 字段对齐。"""

from __future__ import annotations

from api.handlers import prior_evidence_items, prior_tool_records
from api.schemas import AnalysisRequest, AnalysisResponse
from platform_contracts.agent_handoffs import RcaInvokeRequest


def test_analysis_request_extends_rca_invoke():
    req = AnalysisRequest(
        session_id="sess-001",
        user_query="分析批次析锂异常",
        batch_id="B202406001",
        defect_type="lithium_plating",
        prior_evidence=[{"description": "涂布厚度偏低", "source_tool": "mes.get_process_params"}],
        prior_tool_calls=[{"tool": "mes.get_process_params", "args": {"batch_id": "B202406001"}}],
    )
    assert isinstance(req, RcaInvokeRequest)
    assert req.batch_id == "B202406001"
    assert len(req.prior_evidence) == 1


def test_prior_evidence_normalization():
    items = prior_evidence_items(
        [{"summary": "SCADA 温度偏高", "source": "scada.query_equipment_timeseries"}]
    )
    assert items[0]["description"] == "SCADA 温度偏高"
    assert items[0]["source_tool"] == "scada.query_equipment_timeseries"


def test_prior_tool_records_normalization():
    records = prior_tool_records([{"tool": "mes.query_batch_trace", "tool_args": {"batch_id": "B1"}}])
    assert records[0]["tool"] == "mes.query_batch_trace"
    assert records[0]["args"]["batch_id"] == "B1"
    assert records[0]["_from_prior"] is True


def test_analysis_response_rca_artifacts_shape():
    resp = AnalysisResponse(
        trace_id="trc_1",
        thread_id="sess-1",
        status="done",
        root_cause="涂布 → N/P 比偏低",
        recommendations=["立即：复核涂布参数"],
        confidence=0.82,
        report_md="摘要",
        requires_hitl=False,
        evidence=[{"description": "ev1", "source_tool": "mes", "data_ref": "", "confidence": 0.9}],
        rca_artifacts={
            "summary": "摘要",
            "root_cause": "涂布 → N/P 比偏低",
            "recommendations": ["立即：复核涂布参数"],
            "confidence": 0.82,
            "evidence_count": 1,
        },
    )
    assert resp.rca_artifacts["root_cause"] == resp.root_cause
