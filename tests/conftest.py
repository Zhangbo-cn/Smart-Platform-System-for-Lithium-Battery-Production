"""为 tests/ 下的测试配置 Python 路径。"""
import sys
from pathlib import Path

# 添加各包的 src 路径
_HERE = Path(__file__).resolve().parent
_PACKAGES = _HERE.parent / "packages"

_PATHS = [
    _PACKAGES / "eval-core" / "src",
    _PACKAGES / "platform-contracts" / "src",
    _PACKAGES / "harness-core" / "src",
]

for p in _PATHS:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
