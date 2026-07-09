from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from config import get_settings

Base = declarative_base()


class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id = Column(String(64), primary_key=True)
    preferences = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class OpenIssue(Base):
    __tablename__ = "open_issues"
    issue_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class SessionSummary(Base):
    __tablename__ = "session_summaries"
    session_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RoundRecord(Base):
    __tablename__ = "round_records"
    id = Column(String(64), primary_key=True)  # f"{session_id}:{round_id}"
    session_id = Column(String(64), nullable=False, index=True)
    round_id = Column(Integer, nullable=False)
    user_id = Column(String(64), nullable=False)
    trace_id = Column(String(64), nullable=True)
    input_data = Column(JSON, nullable=False, default=dict)
    tool_calls = Column(JSON, nullable=False, default=list)
    key_findings = Column(JSON, nullable=False, default=list)
    output_data = Column(JSON, nullable=False, default=dict)
    token_usage = Column(JSON, nullable=False, default=dict)
    plan_summary = Column(String(1000), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkingMemory:
    """Cross-session memory: user prefs, open issues, session summaries, round records."""

    def __init__(self, dsn: str | None = None, retention_days: int = 30) -> None:
        dsn = dsn or get_settings().postgres_dsn
        self.engine = create_async_engine(dsn, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.retention = timedelta(days=retention_days)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ── 轮次记录 ──────────────────────────────────────
    async def save_round(self, session_id: str, round_id: int, user_id: str, *,
                         trace_id: str | None = None,
                         input_data: dict | None = None,
                         plan_summary: str | None = None,
                         tool_calls: list | None = None,
                         key_findings: list | None = None,
                         output_data: dict | None = None,
                         token_usage: dict | None = None) -> None:
        async with self.Session() as s, s.begin():
            s.add(RoundRecord(
                id=f"{session_id}:{round_id}",
                session_id=session_id, round_id=round_id,
                user_id=user_id, trace_id=trace_id,
                input_data=input_data or {},
                plan_summary=plan_summary,
                tool_calls=tool_calls or [],
                key_findings=key_findings or [],
                output_data=output_data or {},
                token_usage=token_usage or {},
            ))

    async def get_rounds(self, session_id: str, limit: int = 10) -> list[dict]:
        async with self.Session() as s:
            stmt = (select(RoundRecord).where(RoundRecord.session_id == session_id)
                    .order_by(RoundRecord.round_id.desc()).limit(limit))
            res = await s.execute(stmt)
            return [
                {
                    "round_id": r.round_id,
                    "input_data": r.input_data,
                    "plan_summary": r.plan_summary,
                    "tool_calls": r.tool_calls,
                    "key_findings": r.key_findings,
                    "output_data": r.output_data,
                    "token_usage": r.token_usage,
                }
                for r in res.scalars()
            ]

    async def get_round_count(self, session_id: str) -> int:
        from sqlalchemy import func
        async with self.Session() as s:
            stmt = select(func.count()).select_from(RoundRecord).where(
                RoundRecord.session_id == session_id)
            res = await s.execute(stmt)
            return res.scalar() or 0

    # ── 用户偏好 ──────────────────────────────────────
    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        async with self.Session() as s:
            row = await s.get(UserPreference, user_id)
            return row.preferences if row else {}

    async def upsert_preferences(self, user_id: str, prefs: dict) -> None:
        async with self.Session() as s, s.begin():
            row = await s.get(UserPreference, user_id)
            if row:
                row.preferences = {**row.preferences, **prefs}
                row.updated_at = datetime.utcnow()
            else:
                s.add(UserPreference(user_id=user_id, preferences=prefs))

    # ── 待办事项 ──────────────────────────────────────
    async def add_open_issue(self, issue_id: str, user_id: str, title: str, payload: dict) -> None:
        async with self.Session() as s, s.begin():
            s.add(
                OpenIssue(
                    issue_id=issue_id,
                    user_id=user_id,
                    title=title,
                    payload=payload,
                    expires_at=datetime.utcnow() + self.retention,
                )
            )

    async def list_open_issues(self, user_id: str) -> list[dict]:
        async with self.Session() as s:
            stmt = select(OpenIssue).where(
                OpenIssue.user_id == user_id,
                OpenIssue.expires_at > datetime.utcnow(),
            )
            res = await s.execute(stmt)
            return [
                {"issue_id": r.issue_id, "title": r.title, "payload": r.payload}
                for r in res.scalars()
            ]

    # ── 会话摘要 ──────────────────────────────────────
    async def save_session_summary(self, session_id: str, user_id: str, summary: str) -> None:
        async with self.Session() as s, s.begin():
            s.add(SessionSummary(session_id=session_id, user_id=user_id, summary=summary))
