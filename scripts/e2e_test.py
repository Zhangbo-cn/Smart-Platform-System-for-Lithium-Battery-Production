"""端到端链路测试：启动全服务 → 发 close_loop 请求 → 验证 Reporter 产出。"""
from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)
MCP_DIR = ROOT / "services" / "mcp"

SERVICES = {
    # MCP servers (own transport, not uvicorn)
    "mcp_mes":    ("python", "-m", "mes_server.mes_server"),
    "mcp_scada":  ("python", "-m", "scada_server.scada_server"),
    "mcp_erp":    ("python", "-m", "erp_server.erp_server"),
    "mcp_lims":   ("python", "-m", "lims_server.lims_server"),
    "mcp_qms":    ("python", "-m", "qms_server.qms_server"),
    "mcp_knowledge": ("python", "-m", "knowledge_server.app"),
    "mcp_eam":    ("python", "-m", "eam_server.eam_server"),
    "mcp_wms":    ("python", "-m", "wms_server.wms_server"),
    "mcp_plc":    ("python", "-m", "plc_server.plc_server"),
}

WORKERS = {
    "trace":     (8002, ROOT / "services/a2a_server/trace_worker",        "app:app"),
    "rca":       (8003, ROOT / "services/a2a_server/rca-agent",           "api.main:app"),
    "reporter":  (8004, ROOT / "services/a2a_server/report-agent",    "app:app"),
    "orchestrator": (8020, ROOT / "services/orchestrator",                "app:app"),
    "planner":   (8011, ROOT / "services/planner-agent",                        "app:app"),
    "gateway":   (8010, ROOT / "services/client-gateway",                 "app:app"),
}

procs: list[subprocess.Popen] = []


def start_mcp() -> None:
    print("=== Starting 9 MCP servers ===")
    for name, cmd in SERVICES.items():
        log = open(LOGS_DIR / f"mcp_{name}.log", "w")
        p = subprocess.Popen(
            list(cmd), cwd=str(MCP_DIR), stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        procs.append(p)
        print(f"  {name} pid={p.pid}")
    time.sleep(3)


def start_worker(name: str, port: int, cwd: Path, app: str) -> None:
    log = open(LOGS_DIR / f"svc_{name}.log", "w")
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(cwd), "PATH": sys.prefix + "/bin;" + (sys.prefix + "/Scripts;")}
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--port", str(port), "--log-level", "warning"],
        cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, env={**__import__("os").environ, **env},
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    procs.append(p)
    print(f"  {name}:{port} pid={p.pid}")


async def wait_health(url: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{url}/health", timeout=3.0)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return False


async def test_pipeline():
    print("\n=== Starting worker/agent services ===")
    for name, (port, cwd, app) in WORKERS.items():
        start_worker(name, port, cwd, app)

    print("\n=== Waiting for services ===")
    checks = {
        "planner":       "http://127.0.0.1:8011",
        "reporter":      "http://127.0.0.1:8004",
        "rca":           "http://127.0.0.1:8003",
        "orchestrator":  "http://127.0.0.1:8020",
        "gateway":       "http://127.0.0.1:8010",
    }
    results = await asyncio.gather(*[wait_health(url) for url in checks.values()])
    for name, ok in zip(checks, results):
        print(f"  {name}: {'✅' if ok else '❌'}")

    healthy = all(results)
    if not healthy:
        print("\nSome services failed to start, aborting test")
        return

    print("\n=== Sending close_loop request ===")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(
                "http://127.0.0.1:8010/v1/assistant/tasks",
                json={
                    "message": "批次B20260630001容量异常，需要8D闭环",
                    "playbook": "close_loop",
                    "batch_id": "B20260630001",
                    "factory_id": "FD-01",
                    "defect_type": "capacity_low",
                    "confirm_rca": True,
                    "hitl_approved": True,
                    "skip_planner": True,
                    "skip_triage": True,
                },
            )
            print(f"  HTTP {r.status_code}")
            data = r.json()
            print(f"  session_id={data.get('session_id')}")
            print(f"  status={data.get('status')}")
            print(f"  sse_url={data.get('sse_url')}")
            print(f"  planned_playbook={data.get('planned_playbook')}")

            # Poll for result
            session_id = data["session_id"]
            for i in range(20):
                await asyncio.sleep(2)
                ctx_r = await client.get(
                    f"http://127.0.0.1:8010/v1/assistant/tasks/{session_id}/stream",
                    timeout=10.0,
                )
                poll_r = await client.get(
                    f"http://127.0.0.1:8020/a2a/v1/context/{session_id}",
                    timeout=10.0,
                )
                if poll_r.status_code == 200:
                    ctx = poll_r.json()
                    status = ctx.get("task_status", "")
                    step = ctx.get("current_step", "")
                    print(f"  [{i}] status={status} step={step}")
                    if status in ("done", "completed"):
                        report = ctx.get("report_8d", {})
                        rca_info = ctx.get("rca", {})
                        print(f"\n  ✅ Pipeline completed!")
                        print(f"  RCA root_cause: {rca_info.get('root_cause', 'N/A')}")
                        print(f"  RCA confidence: {rca_info.get('confidence', 'N/A')}")
                        print(f"  Report capa_id: {report.get('capa_id', 'N/A')}")
                        print(f"  Report preview: {(report.get('report_md') or '')[:200]}...")
                        break
                else:
                    print(f"  [{i}] context {poll_r.status_code}")
            else:
                print("  ⚠️ Timeout waiting for completion")
        except Exception as exc:
            print(f"  ❌ Error: {exc}")


def cleanup():
    print("\n=== Cleanup ===")
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(1)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    print("  All processes stopped")


async def main():
    start_mcp()
    try:
        await test_pipeline()
    finally:
        cleanup()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
