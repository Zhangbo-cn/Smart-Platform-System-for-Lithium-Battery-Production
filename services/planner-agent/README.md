# Planner（Agent）

平台级任务规划：**LLM + Tool Use 循环** → `playbook` + 参数，交 Orchestrator 执行。

**禁止** delegate 业务服务（trace/rca/8d）；仅可调用进程内规划工具：

| Tool | 作用 |
|------|------|
| `list_playbooks` | 查已实现剧本 |
| `get_capability_card` | 读 AgentCard（enabled/capabilities） |
| `submit_plan` | 提交 `PlanResult` |

`PLANNER_MODE=rule` 时走规则引擎；`react` 且未配置 LLM 时自动回退规则。

```powershell
pip install -e ../../packages/platform-contracts
pip install -r requirements.txt
copy .env.example .env   # 填入 LLM_API_KEY
uvicorn app:app --port 8011
```

```powershell
curl -X POST http://127.0.0.1:8011/v1/plan -H "Content-Type: application/json" -d "{\"message\":\"帮我查一下批次B202406001的流转\"}"
```
