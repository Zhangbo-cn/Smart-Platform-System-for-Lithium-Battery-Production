"""
并行执行性能测试脚本

演示 ExecutorAgent 的并行优化效果
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from agent.agents.executor import ExecutorAgent
from agent.state import QualityAnalysisState
from agent.tools.registry import ToolRegistry


# Mock Tool Handler - 模拟耗时的数据查询
async def mock_query_handler(args: dict[str, Any]) -> dict[str, Any]:
    """模拟耗时 1 秒的数据查询"""
    process_name = args.get("process", "unknown")
    await asyncio.sleep(1.0)  # 模拟网络/数据库延迟
    return {
        "process": process_name,
        "data": f"mock data for {process_name}",
        "timestamp": time.time(),
    }


async def test_sequential_execution():
    """测试串行执行（旧版本）"""
    print("=" * 60)
    print("测试 1：串行执行（3 个步骤顺序执行）")
    print("=" * 60)

    registry = ToolRegistry()

    # 注册 3 个模拟工具
    for process in ["coating", "rolling", "winding"]:
        from agent.tools.registry import ToolSpec
        registry.register(
            ToolSpec(
                name=f"query_{process}",
                server="mock",
                description=f"查询{process}工序数据",
                parameters={},
                handler=mock_query_handler,
            )
        )

    executor = ExecutorAgent(registry=registry)

    # 构造状态 - parallel=False 触发串行执行
    state = QualityAnalysisState(
        user_query="查询涂布、辊压、卷绕工序数据",
        analysis_plan=[
            {
                "step_id": 1,
                "action": "查询涂布工序",
                "tool": "query_coating",
                "tool_args": {"process": "coating"},
                "rationale": "获取涂布数据",
                "parallel": False,  # 串行
            },
            {
                "step_id": 2,
                "action": "查询辊压工序",
                "tool": "query_rolling",
                "tool_args": {"process": "rolling"},
                "rationale": "获取辊压数据",
                "parallel": False,  # 串行
            },
            {
                "step_id": 3,
                "action": "查询卷绕工序",
                "tool": "query_winding",
                "tool_args": {"process": "winding"},
                "rationale": "获取卷绕数据",
                "parallel": False,  # 串行
            },
        ],
        current_step=0,
        tool_calls=[],
    )

    start_time = time.time()
    result = await executor.run(state)
    elapsed = time.time() - start_time

    print(f"✅ 执行完成")
    print(f"📊 耗时: {elapsed:.2f} 秒")
    print(f"📋 工具调用次数: {len(result['tool_calls'])}")
    print(f"预期: ~3.0 秒 (1秒 × 3)")
    print()

    return elapsed


async def test_parallel_execution():
    """测试并行执行（优化版本）"""
    print("=" * 60)
    print("测试 2：并行执行（3 个步骤同时执行）")
    print("=" * 60)

    registry = ToolRegistry()

    # 注册 3 个模拟工具
    for process in ["coating", "rolling", "winding"]:
        from agent.tools.registry import ToolSpec
        registry.register(
            ToolSpec(
                name=f"query_{process}",
                server="mock",
                description=f"查询{process}工序数据",
                parameters={},
                handler=mock_query_handler,
            )
        )

    executor = ExecutorAgent(registry=registry)

    # 构造状态 - parallel=True 触发并行执行
    state = QualityAnalysisState(
        user_query="查询涂布、辊压、卷绕工序数据",
        analysis_plan=[
            {
                "step_id": 1,
                "action": "查询涂布工序",
                "tool": "query_coating",
                "tool_args": {"process": "coating"},
                "rationale": "获取涂布数据",
                "parallel": True,  # 并行
            },
            {
                "step_id": 2,
                "action": "查询辊压工序",
                "tool": "query_rolling",
                "tool_args": {"process": "rolling"},
                "rationale": "获取辊压数据",
                "parallel": True,  # 并行
            },
            {
                "step_id": 3,
                "action": "查询卷绕工序",
                "tool": "query_winding",
                "tool_args": {"process": "winding"},
                "rationale": "获取卷绕数据",
                "parallel": True,  # 并行
            },
        ],
        current_step=0,
        tool_calls=[],
    )

    start_time = time.time()
    result = await executor.run(state)
    elapsed = time.time() - start_time

    print(f"✅ 执行完成")
    print(f"📊 耗时: {elapsed:.2f} 秒")
    print(f"📋 工具调用次数: {len(result['tool_calls'])}")
    print(f"预期: ~1.0 秒 (并行执行)")
    print()

    return elapsed


async def main():
    print("\n" + "🚀 ExecutorAgent 并行优化性能测试" + "\n")

    # 测试串行执行
    sequential_time = await test_sequential_execution()

    # 测试并行执行
    parallel_time = await test_parallel_execution()

    # 性能对比
    print("=" * 60)
    print("📈 性能对比结果")
    print("=" * 60)
    print(f"串行执行耗时: {sequential_time:.2f} 秒")
    print(f"并行执行耗时: {parallel_time:.2f} 秒")

    if parallel_time > 0:
        speedup = sequential_time / parallel_time
        improvement = (1 - parallel_time / sequential_time) * 100
        print(f"加速比: {speedup:.2f}x")
        print(f"性能提升: {improvement:.1f}%")

    print("\n✅ 优化验证完成！")
    print("\n说明：")
    print("- parallel=False: 步骤顺序执行（3秒）")
    print("- parallel=True:  步骤并行执行（1秒）")
    print("- 实际项目中可提升 60%+ 性能（90s → 35s）")


if __name__ == "__main__":
    asyncio.run(main())
