"""MCP mes_server 测试路径配置。"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICE = _HERE.parent
_PARENT = _SERVICE.parent

if str(_SERVICE) not in sys.path:
    sys.path.append( str(_SERVICE))
# Some MCP servers may need the parent mcp/ dir
if str(_PARENT) not in sys.path:
    sys.path.append( str(_PARENT))
