"""knowledge_server 测试路径配置。
注意：knowledge_server 包和模块同名，需要精确导入。
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SERVICE = _HERE.parent  # services/mcp/knowledge_server/

# Add parent dir so 'knowledge_server' package is findable
if str(_SERVICE.parent) not in sys.path:
    sys.path.insert(0, str(_SERVICE.parent))
# Add the package dir itself
if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))
