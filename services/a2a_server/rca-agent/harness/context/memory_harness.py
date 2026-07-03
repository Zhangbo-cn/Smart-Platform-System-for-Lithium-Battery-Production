from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from config import get_settings
from harness.memory.in_memory import InMemoryLTM, InMemorySTM, InMemoryWorking
from harness.memory.long_term import LongTermMemory
from harness.memory.short_term import ShortTermMemory
from harness.memory.working import WorkingMemory

logger = structlog.get_logger(__name__)


class MemoryHarness:
    """
    Context engineering orchestrator: short-term (Redis) + working (PG) + long-term (Milvus/Neo4j).

    - Short-term: per-session dialogue turns + last state snapshot (TTL 30min)
    - Working: cross-session user prefs, open issues, session summaries (30d)
    - Long-term: confirmed case vectors + knowledge graph (degrades when backends absent)
    """

    def __init__(
        self,
        stm: Any,
        working: WorkingMemory | InMemoryWorking | None,
        ltm: LongTermMemory | InMemoryLTM,
    ) -> None:
        self.stm = stm
        self.working = working
        self.ltm = ltm

    @classmethod
    async def create(cls, agent_name: str = "default") -> MemoryHarness:
        settings = get_settings()
        stm: Any
        working: WorkingMemory | InMemoryWorking
        ltm: LongTermMemory | InMemoryLTM

        # STM: Redis → InMemory fallback
        try:
            candidate = ShortTermMemory(agent_name=agent_name)
            await candidate.redis.ping()
            stm = candidate
            logger.info("memory.stm", backend="redis", agent=agent_name)
        except Exception as exc:
            stm = InMemorySTM(agent_name=agent_name)
            logger.warning("memory.stm_fallback", backend="in_memory", agent=agent_name, error=str(exc))

        # Working: PostgreSQL → InMemory fallback
        try:
            working = WorkingMemory(dsn=settings.postgres_dsn)
            await working.init()
            logger.info("memory.working", backend="postgres")
        except Exception as exc:
            working = InMemoryWorking()
            logger.warning("memory.working_fallback", backend="in_memory", error=str(exc))

        # LTM: Milvus+Neo4j → InMemory fallback
        try:
            ltm = LongTermMemory()
            if ltm.milvus or ltm.neo4j:
                logger.info("memory.ltm", milvus=bool(ltm.milvus), neo4j=bool(ltm.neo4j))
            else:
                raise ValueError("no LTM backends configured")
        except Exception as exc:
            ltm = InMemoryLTM()
            logger.warning("memory.ltm_fallback", backend="in_memory", error=str(exc))

        return cls(stm=stm, working=working, ltm=ltm)

    async def build_planner_context(
        self,
        session_id: str,
        user_id: str,
        query: str,
        defect_type: str | None = None,
    ) -> str:
        sections: list[str] = []

        turns = await self.stm.get_turns(session_id, last_n=6)
        if turns:
            lines = [f"- {t['role']}: {t['content'][:200]}" for t in turns]
            sections.append("【近期对话（短期记忆）】\n" + "\n".join(lines))

        last_state = await self.stm.get(session_id, slot="last_state")
        if last_state and last_state.get("root_cause"):
            sections.append(
                "【上轮分析摘要（短期记忆）】\n"
                f"根因: {last_state.get('root_cause')} | 置信度: {last_state.get('confidence', 0):.2f}"
            )

        if self.working:
            prefs = await self.working.get_preferences(user_id)
            if prefs:
                sections.append("【用户偏好（中期记忆）】\n" + json.dumps(prefs, ensure_ascii=False))

            issues = await self.working.list_open_issues(user_id)
            if issues:
                titles = [f"- {i['title']}" for i in issues[:5]]
                sections.append("【未结案质量问题（中期记忆）】\n" + "\n".join(titles))

            summaries = await self._recent_summaries(user_id, limit=3)
            if summaries:
                sections.append("【历史会话摘要（中期记忆）】\n" + "\n".join(summaries))

        similar = await self.ltm.search_similar_cases(query, defect_type=defect_type, top_k=3)
        if similar:
            lines = [
                f"- {c.get('root_cause', c.get('description', ''))}"
                for c in similar
            ]
            sections.append("【相似历史案例（长期记忆）】\n" + "\n".join(lines))

        return "\n\n".join(sections)

    async def _recent_summaries(self, user_id: str, limit: int = 3) -> list[str]:
        if not self.working:
            return []
        # InMemoryWorking path
        if isinstance(self.working, InMemoryWorking):
            return await self.working.recent_summaries(user_id, limit)
        # PostgreSQL path
        from sqlalchemy import select
        from harness.memory.working import SessionSummary
        async with self.working.Session() as s:
            stmt = (
                select(SessionSummary)
                .where(SessionSummary.user_id == user_id)
                .order_by(SessionSummary.created_at.desc())
                .limit(limit)
            )
            res = await s.execute(stmt)
            return [row.summary for row in res.scalars()]

    async def persist_analysis(
        self,
        session_id: str,
        user_id: str,
        query: str,
        state: dict[str, Any],
    ) -> None:
        root = state.get("root_cause", "")
        confidence = float(state.get("confidence", 0.0))
        hitl = state.get("hitl_response") or {}

        await self.stm.append_turn(session_id, "user", query)
        if root:
            await self.stm.append_turn(session_id, "assistant", root)
        await self.stm.set(
            session_id,
            {
                "root_cause": root,
                "confidence": confidence,
                "defect_type": state.get("defect_type"),
                "trace_id": state.get("trace_id"),
            },
            slot="last_state",
        )

        if self.working and state.get("status") == "done":
            summary = (
                f"缺陷: {state.get('defect_type', '未知')} | "
                f"根因: {root or '未确认'} | 置信度: {confidence:.2f}"
            )
            try:
                await self.working.save_session_summary(session_id, user_id, summary)
            except Exception:
                pass  # memory is best-effort

            if confidence < 0.7 and not hitl.get("approved"):
                await self.working.add_open_issue(
                    issue_id=uuid.uuid4().hex,
                    user_id=user_id,
                    title=f"待复核: {query[:80]}",
                    payload={"session_id": session_id, "partial": state.get("partial_result")},
                )

        confirmed = hitl.get("approved") or confidence >= 0.85
        if confirmed and root:
            await self.ltm.add_case(
                case_id=state.get("trace_id") or uuid.uuid4().hex,
                description=query,
                defect_type=state.get("defect_type", ""),
                root_cause=root,
                evidence=state.get("evidence", []),
                confirmed_by=user_id,
                score=5 if hitl.get("approved") else 4,
            )
