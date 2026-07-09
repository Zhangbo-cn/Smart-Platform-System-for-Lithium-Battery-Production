"""全链路测试：启动全部服务 → RCA → close_loop → Reporter"""
import asyncio, subprocess, sys, time, os, httpx
from pathlib import Path

ROOT = Path(__file__).parent.parent
MCP_DIR = ROOT / "services" / "mcp"
PROCS = []

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def start_mcp():
    log("Starting 9 MCP servers...")
    for mod in ["mes_server.mes_server","scada_server.scada_server",
                "erp_server.erp_server","lims_server.lims_server","qms_server.qms_server",
                "knowledge_server.app","eam_server.eam_server","wms_server.wms_server",
                "plc_server.plc_server"]:
        p = subprocess.Popen([sys.executable,"-m",mod], cwd=str(MCP_DIR),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PROCS.append(p)
    time.sleep(4)

def start_svc(port, cwd_rel, app_mod, extra_env=None):
    cwd = str(ROOT / cwd_rel)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    p = subprocess.Popen([sys.executable,"-m","uvicorn",app_mod,"--port",str(port),"--log-level","error"],
                         cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    PROCS.append(p)

async def wait_health(ports, timeout=90, label=""):
    async with httpx.AsyncClient() as c:
        for port in ports:
            for _ in range(timeout):
                try:
                    r = await c.get(f"http://127.0.0.1:{port}/health", timeout=3)
                    if r.status_code == 200:
                        log(f"  {label}:{port} ✅")
                        break
                except: pass
                await asyncio.sleep(1)
            else:
                log(f"  {label}:{port} ❌")
                return False
    return True

async def run_playbook(playbook, extra_params, wait_max=420):
    body = {
        "message": "批次B20260630001容量衰减，需要根因分析和8D闭环",
        "playbook": playbook,
        "batch_id": "B20260630001",
        "factory_id": "FD-01",
        "defect_type": "容量衰减",
        "skip_planner": True,
        "skip_triage": True,
        "confirm_rca": True,
        **extra_params,
    }
    async with httpx.AsyncClient(timeout=wait_max+60) as c:
        r = await c.post("http://127.0.0.1:8010/v1/assistant/tasks", json=body)
        if r.status_code != 202:
            log(f"  POST failed: {r.status_code} {r.text[:200]}")
            return None
        sid = r.json()["session_id"]
        log(f"  Session: {sid} (playbook={playbook}, timeout={wait_max}s)")

        last_step = ""
        for i in range(wait_max // 2):
            await asyncio.sleep(2)
            try:
                cr = await c.get(f"http://127.0.0.1:8020/a2a/v1/context/{sid}", timeout=5)
                if cr.status_code != 200:
                    if i < 5: log(f"  [{i*2}s] context {cr.status_code}")
                    continue
                ctx = cr.json()
                st = ctx.get("task_status") or "running"
                step = ctx.get("current_step") or ""
                rca = ctx.get("rca") or {}
                rep = ctx.get("report_8d") or {}

                # Log on state change or every 30s
                if st != "running" or step != last_step or i % 15 == 0:
                    log(f"  [{(i*2)}s] {st} | {step} | "
                        f"rc={rca.get('root_cause','')[:60]} | conf={rca.get('confidence')} | "
                        f"capa={rep.get('capa_id','')}")
                    last_step = step

                if st == "done":
                    log(f"  ✅ Playbook completed!")
                    return ctx
                if st == "failed":
                    log(f"  ❌ Playbook failed")
                    return ctx
                if st in ("hitl","input_required"):
                    log(f"  ⏸️  HITL required (conf={rca.get('confidence')})")
                    return ctx
            except Exception as e:
                if i < 5: log(f"  [{i*2}s] poll err: {e}")
        log(f"  ⚠️ Timeout after {wait_max}s (last: {last_step})")
        return None

def cleanup():
    for p in PROCS:
        try: p.terminate()
        except: pass
    time.sleep(1)
    for p in PROCS:
        try: p.kill()
        except: pass

async def main():
    start_mcp()
    start_svc(8002, "services/a2a_server/trace_worker", "app:app")
    start_svc(8003, "services/a2a_server/rca-agent", "api.main:app")
    start_svc(8004, "services/a2a_server/report-agent", "app:app")

    log("Waiting for deps...")
    if not await wait_health([8002,8003,8004], label="dep"):
        cleanup(); return

    # Orchestrator with extended timeout for RCA LangGraph
    start_svc(8020, "services/orchestrator", "app:app",
              extra_env={"HTTP_TIMEOUT": "600", "HTTP_RETRIES": "0"})
    start_svc(8011, "services/planner-agent", "app:app")
    start_svc(8010, "services/client-gateway", "app:app")

    log("Waiting for control plane...")
    if not await wait_health([8011,8020,8010], timeout=90, label="cp"):
        cleanup(); return

    log("All 11 services healthy ✅")

    # RCA with 7 min timeout
    log("\n=== RCA (7 min timeout) ===")
    rca_ctx = await run_playbook("rca", {}, wait_max=420)

    if rca_ctx:
        rca = rca_ctx.get("rca") or {}
        rc = rca.get("root_cause","")
        log(f"RCA result: root_cause={rc[:100]}, confidence={rca.get('confidence')}")

    # close_loop with 5 min timeout
    log("\n=== close_loop (5 min timeout) ===")
    clo_ctx = await run_playbook("close_loop", {"hitl_approved": True}, wait_max=300)

    if clo_ctx and clo_ctx.get("task_status") == "done":
        rep = clo_ctx.get("report_8d") or {}
        log(f"\n{'='*60}")
        log(f"✅✅✅ FULL PIPELINE COMPLETE!")
        log(f"{'='*60}")
        log(f"  capa_id:      {rep.get('capa_id')}")
        log(f"  qms_status:   {rep.get('qms_status')}")
        log(f"  gen_mode:     {rep.get('generation_mode')}")
        md = rep.get("report_md","")
        if md:
            log(f"\n--- 8D Report ---")
            for line in md.split("\n")[:20]: log(f"  {line}")
    else:
        status = "timeout"
        if clo_ctx: status = clo_ctx.get("task_status","?")
        log(f"  close_loop result: {status}")

    cleanup()
    log("Done")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
