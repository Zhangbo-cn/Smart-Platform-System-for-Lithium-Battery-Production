"""进程内 Memory fallback：开发/测试环境，无需 Redis/PG/Milvus/Neo4j。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


class InMemorySTM:
    """Short-term memory fallback when Redis unavailable."""

    def __init__(self, agent_name: str = "default") -> None:
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self.agent_name = agent_name

    def _key(self, session_id: str, slot: str) -> str:
        return f"agent:stm:{self.agent_name}:{session_id}:{slot}"

    async def get(self, session_id: str, slot: str = "state") -> Any | None:
        raw = self._store.get(self._key(session_id, slot))
        return json.loads(raw) if raw else None

    async def set(self, session_id: str, value: Any, slot: str = "state") -> None:
        self._store[self._key(session_id, slot)] = json.dumps(
            value, ensure_ascii=False, default=str
        )

    async def append_turn(self, session_id: str, role: str, content: str) -> None:
        key = self._key(session_id, "turns")
        self._lists.setdefault(key, []).append(
            json.dumps({"role": role, "content": content}, ensure_ascii=False)
        )

    async def get_turns(self, session_id: str, last_n: int = 10) -> list[dict]:
        key = self._key(session_id, "turns")
        raw = self._lists.get(key, [])[-last_n:]
        return [json.loads(r) for r in raw]

    async def clear(self, session_id: str) -> None:
        prefix = f"agent:stm:{session_id}:"
        for k in list(self._store):
            if k.startswith(prefix):
                del self._store[k]
        for k in list(self._lists):
            if k.startswith(prefix):
                del self._lists[k]


class InMemoryWorking:
    """Working memory fallback: user prefs + open issues + session summaries in-process."""

    def __init__(self) -> None:
        self._prefs: dict[str, dict] = {}
        self._issues: list[dict] = []
        self._summaries: list[dict] = []

    async def init(self) -> None:
        pass  # no-op for in-memory

    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        return self._prefs.get(user_id, {})

    async def upsert_preferences(self, user_id: str, prefs: dict) -> None:
        self._prefs.setdefault(user_id, {}).update(prefs)

    async def add_open_issue(self, issue_id: str, user_id: str, title: str, payload: dict) -> None:
        self._issues.append({
            "issue_id": issue_id, "user_id": user_id,
            "title": title, "payload": payload,
            "created_at": datetime.utcnow().isoformat(),
        })

    async def list_open_issues(self, user_id: str) -> list[dict]:
        return [i for i in self._issues if i["user_id"] == user_id]

    async def save_session_summary(self, session_id: str, user_id: str, summary: str) -> None:
        self._summaries.append({
            "session_id": session_id, "user_id": user_id,
            "summary": summary, "created_at": datetime.utcnow().isoformat(),
        })

    async def recent_summaries(self, user_id: str, limit: int = 3) -> list[str]:
        user_sums = [s for s in self._summaries if s["user_id"] == user_id]
        return [s["summary"] for s in user_sums[-limit:]]


class InMemoryLTM:
    """
    Long-term memory fallback: keyword matching instead of vector search.
    For development/testing without Milvus/Neo4j.
    """

    def __init__(self) -> None:
        self._cases: list[dict] = []

    async def add_case(
        self, case_id: str, description: str, defect_type: str,
        root_cause: str, evidence: list[dict], confirmed_by: str, score: int,
    ) -> None:
        if score < 4:
            return
        self._cases.append({
            "case_id": case_id, "description": description,
            "defect_type": defect_type, "root_cause": root_cause,
            "score": score, "confirmed_by": confirmed_by,
        })

    async def search_similar_cases(
        self, query: str, defect_type: str | None = None, top_k: int = 5,
    ) -> list[dict]:
        candidates = self._cases
        if defect_type:
            candidates = [c for c in candidates if c["defect_type"] == defect_type]
        # Simple keyword overlap scoring
        query_words = set(re.findall(r"[\w一-鿿]+", query.lower()))
        scored = []
        for c in candidates:
            desc_words = set(re.findall(r"[\w一-鿿]+", c["description"].lower()))
            overlap = len(query_words & desc_words) if query_words else 0
            scored.append((overlap, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"case_id": c["case_id"], "description": c["description"],
             "root_cause": c["root_cause"], "score": c["score"]}
            for _, c in scored[:top_k] if c["defect_type"] == (defect_type or c["defect_type"])
        ] or [{"case_id": c["case_id"], "description": c["description"],
               "root_cause": c["root_cause"]}
              for _, c in scored[:top_k]]

    async def mark_negative_example(self, case_id: str, reason: str) -> None:
        for c in self._cases:
            if c["case_id"] == case_id:
                c["negative"] = True
                c["reason"] = reason
