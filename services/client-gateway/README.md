# Client Gateway（`client-gateway`）

用户门户：**无 LLM**。可选经 Planner 规划后转发 Orchestrator。

```env
ORCHESTRATOR_URL=http://127.0.0.1:8020
PLANNER_URL=http://127.0.0.1:8011
RCA_AGENT_URL=http://127.0.0.1:8003
AUTO_PLAN=true
```

```powershell
cd services\client-gateway
pip install -r requirements.txt
pip install -e ..\..\packages\platform-contracts
uvicorn app:app --port 8010
```
