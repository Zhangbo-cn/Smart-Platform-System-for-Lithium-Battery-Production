"""全链路测试：启动全部服务 → RCA → close_loop → 验证 Reporter 产出。"""
import asyncio, subprocess, sys, time, httpx
from pathlib import Path

ROOT = Path(__file__).parent.parent
MCP_DIR = ROOT / "services" / "mcp"
PROCS = []

def start_mcp():
    for mod in ["mes_server.mes_server","scada_server.scada_server",
                "erp_server.erp_server","lims_server.lims_server","qms_server.qms_server",
                "knowledge_server.app","eam_server.eam_server","wms_server.wms_server",
                "plc_server.plc_server"]:
        p = subprocess.Popen([sys.executable,"-m",mod], cwd=str(MCP_DIR),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PROCS.append(p)
    time.sleep(4)

def start_svc(port, cwd_rel, app_mod, logfile=None):
    cwd = str(ROOT / cwd_rel)
    kw = {}
    if logfile:
        kw = {"stdout": open(logfile,"w"), "stderr": subprocess.STDOUT}
    else:
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    p = subprocess.Popen([sys.executable,"-m","uvicorn",app_mod,"--port",str(port),"--log-level","error"],
                         cwd=cwd, **kw)
    PROCS.append(p)

async def wait_health(ports, timeout=60):
    async with httpx.AsyncClient() as c:
        results = {}
        for port in ports:
            for _ in range(int(timeout)):
                try:
                    r = await c.get(f"http://127.0.0.1:{port}/health", timeout=3)
                    if r.status_code == 200:
                        results[port] = True
                        print(f"  port {port} ✅")
                        break
                except Exception: pass
                await asyncio.sleep(1)
            else:
                results[port] = False
                print(f"  port {port} ❌")
        return all(results.values())

async def rca_analysis(defect_type="容量衰减"):
    """Step 1: RCA analysis, wait up to 3 minutes for result."""
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post("http://127.0.0.1:8010/v1/assistant/tasks", json={
            "message": f"批次B20260630001{defect_type}异常根因分析",
            "playbook": "rca",
            "batch_id": "B20260630001",
            "defect_type": defect_type,
            "skip_planner": True,
        })
        if r.status_code != 202:
            print(f"  RCA request failed: {r.status_code} {r.text}")
            return None
        sid = r.json()["session_id"]
        print(f"  Session: {sid}")

        for i in range(90):
            await asyncio.sleep(2)
            try:
                cr = await c.get(f"http://127.0.0.1:8020/a2a/v1/context/{sid}", timeout=5)
                if cr.status_code != 200:
                    if i == 0: print(f"  waiting...")
                    continue
                ctx = cr.json()
                st = ctx.get("task_status","")
                step = ctx.get("current_step","")
                rca = ctx.get("rca",{}) or {}
                rc = rca.get("root_cause","")[:80]
                conf = rca.get("confidence")
                print(f"  [{i*2}s] {st} | {step} | rc={rc} | conf={conf}")
                if st in ("done","completed","hitl","input_required"):
                    return ctx
            except Exception as e:
                if i < 3: print(f"  poll err: {e}")
        return None

async def close_loop_8d():
    """Step 2: close_loop with hitl_approved=True to reach Reporter."""
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post("http://127.0.0.1:8010/v1/assistant/tasks", json={
            "message": "根因已确认，出8D报告闭环",
            "playbook": "close_loop",
            "batch_id": "B20260630001",
            "defect_type": "容量衰减",
            "hitl_approved": True,
            "skip_planner": True,
            "skip_triage": True,
        })
        if r.status_code != 202:
            print(f"  close_loop request failed: {r.status_code}")
            return None
        sid = r.json()["session_id"]
        print(f"  Session: {sid}")

        for i in range(60):
            await asyncio.sleep(2)
            try:
                cr = await c.get(f"http://127.0.0.1:8020/a2a/v1/context/{sid}", timeout=5)
                if cr.status_code != 200: continue
                ctx = cr.json()
                st = ctx.get("task_status","")
                step = ctx.get("current_step","")
                rep = ctx.get("report_8d",{}) or {}
                capa = rep.get("capa_id","")
                mode = rep.get("generation_mode","")
                print(f"  [{i*2}s] {st} | {step} | capa={capa} | mode={mode}")
                if st == "done":
                    return ctx
                if st == "failed":
                    return ctx
            except: pass
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
    print("=== 1. Starting MCP servers ===")
    start_mcp()

    print("=== 2. Starting Trace, RCA, Reporter ===")
    start_svc(8002, "services/a2a_server/trace_worker", "app:app")
    start_svc(8003, "services/a2a_server/rca-agent", "api.main:app")
    start_svc(8004, "services/a2a_server/report-agent", "app:app")

    print("=== 3. Waiting for deps ===")
    if not await wait_health([8002,8003,8004]):
        print("FATAL: deps not healthy"); return

    print("=== 4. Starting Orchestrator, Planner, Gateway ===")
    start_svc(8020, "services/orchestrator", "app:app")
    start_svc(8011, "services/planner-agent", "app:app")
    start_svc(8010, "services/client-gateway", "app:app")

    print("=== 5. Waiting for control plane ===")
    if not await wait_health([8011,8020,8010], timeout=90):
        print("FATAL: control plane not healthy"); return

    print("\n=== 6. RCA analysis ===")
    rca_ctx = await rca_analysis("容量衰减")
    if rca_ctx:
        rca = rca_ctx.get("rca",{}) or {}
        print(f"\n  RCA: root_cause={rca.get('root_cause','')[:120]}")
        print(f"  RCA: confidence={rca.get('confidence')}")
        print(f"  RCA: status={rca.get('status')}")

    print("\n=== 7. close_loop → Reporter ===")
    ctx = await close_loop_8d()
    if ctx and ctx.get("task_status") == "done":
        rep = ctx.get("report_8d",{}) or {}
        print(f"\n{'='*60}")
        print(f"✅✅✅ FULL PIPELINE COMPLETE!")
        print(f"{'='*60}")
        print(f"  capa_id:      {rep.get('capa_id')}")
        print(f"  qms_status:   {rep.get('qms_status')}")
        print(f"  gen_mode:     {rep.get('generation_mode')}")
        md = rep.get("report_md","")
        if md: print(f"  Report preview:\n{md[:400]}")
        return True
    else:
        status = "timeout"
        if ctx:
            status = ctx.get("task_status","unknown")
            err = ctx.get("report_8d",{}) or {}
            print(f"\n  Status: {status}")
            print(f"  Report: {err}")
        else:
            print(f"\n  ⚠️ close_loop timed out")
        return False

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    ok = asyncio.run(main())
    cleanup()
    sys.exit(0 if ok else 1)
