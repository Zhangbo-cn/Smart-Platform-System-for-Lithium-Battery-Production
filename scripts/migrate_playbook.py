#!/usr/bin/env python3
"""PlaybookEngine → DAGEngine 迁移工具。

把旧的 steps: 列表格式自动转为 nodes: DAG 格式。

用法:
  python migrate_playbook.py config/playbooks.yaml          # 预览转换结果
  python migrate_playbook.py config/playbooks.yaml --write   # 写入文件备份原文件
"""

from __future__ import annotations

import copy
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def migrate_playbook_yaml(source: str | Path) -> dict[str, Any]:
    """将旧格式 playbooks YAML 转换为新 DAG 格式。

    旧格式:
      playbooks:
        investigate:
          steps:
            - step: triage
              agent: triage-agent
              condition: "not skip_triage and not defect_type"

    新格式:
      playbooks:
        investigate:
          nodes:
            triage:
              agent: triage-agent
              condition: "not skip_triage and not defect_type"
              parallel: true
    """
    path = Path(source)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    playbooks_data = raw.get("playbooks", {})
    changed = False

    for pb_name, pb_def in playbooks_data.items():
        # 已经是 nodes 格式 → 跳过
        if "nodes" in pb_def:
            continue

        # 是旧格式 steps → 转换
        steps = pb_def.pop("steps", [])
        if not steps:
            continue

        nodes: dict[str, Any] = {}
        prev_step: str | None = None

        for i, step in enumerate(steps):
            # 确定节点 ID: 用 step 名，或自动生成 step_0, step_1...
            nid = step.get("step", f"step_{i}")

            # 基础字段
            node: dict[str, Any] = {
                "agent": step.get("agent", ""),
            }

            # type
            step_type = step.get("type")
            if step_type and step_type != "agent_call":
                node["type"] = step_type

            # condition
            cond = step.get("condition")
            if cond:
                node["condition"] = cond

            # parallel: 旧格式没有 parallel，全部串行
            # 但如果多个步骤没有明显依赖，可以标记为并行
            # 这里保守处理：全部设为串行

            # required → max_retry + fallback
            required = step.get("required", False)
            if required:
                node["max_retry"] = 3
            else:
                node["max_retry"] = 2

            # fallback
            fallback = step.get("fallback")
            if fallback:
                node["fallback"] = fallback
            elif required:
                # required=True 但没有 fallback → 加一个默认 fallback
                node["fallback"] = {"on_failure": True}

            # hitl_check
            hitl = step.get("hitl_check")
            if hitl:
                node["hitl_check"] = hitl

            # context_write
            ctx_write = step.get("context_write")
            if ctx_write:
                node["context_write"] = ctx_write

            # message (for input_required type)
            msg = step.get("message")
            if msg:
                node["message"] = msg

            # depends_on: 旧格式的顺序依赖
            if prev_step is not None:
                node["depends_on"] = [prev_step]

            nodes[nid] = node
            prev_step = nid

        pb_def["nodes"] = nodes
        changed = True

    if changed:
        raw["_migrated_at"] = datetime.now().isoformat()
        raw["_migration_note"] = "从 steps 格式自动转换为 nodes DAG 格式"

    return raw


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    source = Path(args[0])
    if not source.exists():
        print(f"[ERR] 文件不存在: {source}")
        sys.exit(1)

    do_write = "--write" in args

    # 预览
    result = migrate_playbook_yaml(source)
    playbooks = result.get("playbooks", {})

    migrated = 0
    already_dag = 0
    for name, pb in playbooks.items():
        if "nodes" in pb and "_migrated_at" not in result:
            already_dag += 1
        elif "nodes" in pb:
            migrated += 1

    if migrated == 0 and already_dag > 0:
        print(f"[OK] 全部 {already_dag} 个 playbook 已经是 DAG 格式，无需迁移")
        return

    print(f"[INFO] 预览: {migrated} 个 playbook 需要迁移")

    if do_write:
        backup = source.with_suffix(f".yaml.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(source, backup)
        source.write_text(yaml.dump(result, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
        print(f"[OK] 已写入 {source}")
        print(f"[BAK] 备份: {backup}")
    else:
        print("\n--- 转换预览 ---")
        print(yaml.dump(result, allow_unicode=True, default_flow_style=False, sort_keys=False)[:2000])
        print("\n[HINT] 加 --write 参数写入文件")


if __name__ == "__main__":
    main()
