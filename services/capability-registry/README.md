# Capability Registry（`registry` 服务）

A2A 能力服务登记与健康探活：`GET /a2a/v1/agents`、`GET /a2a/v1/agents/{name}/card`。种子数据来自 `platform-contracts`。**无 LLM**，不是 Agent。

```powershell
pip install -e ../../packages/platform-contracts
uvicorn app:app --host 127.0.0.1 --port 8021
```
