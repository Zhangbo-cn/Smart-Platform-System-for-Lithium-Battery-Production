# Trace Worker（`trace-worker`）

批次追溯 Worker：MES MCP → `prior_evidence`。无 LLM。

```powershell
pip install -e ../../../packages/platform-contracts
pip install -e ../../../packages/harness-core
pip install -r requirements.txt
uvicorn app:app --port 8002
```
