"""Client Gateway 测试路径配置。"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICE = _HERE.parent

if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))
