"""兼容 shim：旧 RCA Agent 引用 harness_core.a2a，实际在 platform_contracts.a2a。"""

from platform_contracts.a2a import *  # noqa: F401, F403
