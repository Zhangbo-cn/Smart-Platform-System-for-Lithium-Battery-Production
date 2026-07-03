# Playbook Orchestrator（`router` 服务）

Playbook 编排、`PlatformContext` 黑板、A2A 委派业务能力服务。**无 LLM**，不是 Agent。

```powershell
pip install -e ../../packages/platform-contracts
pip install -e ../../packages/harness-core
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8020
```

| 端点 | 说明 |
|------|------|
| `POST /a2a/v1/router/dispatch` | `playbook=investigate\|trace_only\|rca` |
| `GET /a2a/v1/context/{session_id}` | 读 Context |
| `GET /health` | 依赖探活 |
