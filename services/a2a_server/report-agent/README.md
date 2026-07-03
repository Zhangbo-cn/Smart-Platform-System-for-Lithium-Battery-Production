# Reporter Agent（Deep Agents）

8D 定稿服务：`report-reporter-agent`（A2A）· 端口 8004

## 模式

| `REPORTER_MODE` | 行为 |
|-----------------|------|
| `deep_agent` | 官方 `deepagents` + 子 Agent + VFS |
| `template` | 模板 + QMS MCP（无 LLM） |

LLM 未配置时自动 fallback `template`。

## 子 Agent（进程内）

- `d4_root_cause_writer`：根因只读
- `d5_capa_planner`：CAPA + SOP/Golden 检索
- `evidence_appendix`：D6 证据

## 启动

```bash
cd services/a2a_server/report-agent
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --port 8004
```
