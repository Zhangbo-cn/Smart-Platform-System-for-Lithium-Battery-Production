"""Agent 模板测试路径配置。"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICE = _HERE.parent

if str(_SERVICE) not in sys.path:
    sys.path.append( str(_SERVICE))
