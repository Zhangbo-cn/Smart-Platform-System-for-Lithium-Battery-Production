"""一键调用质量分析：自动签发 JWT 并请求 API（普通脚本，不是 pytest）。"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.auth import issue_token

API_URL = "http://127.0.0.1:8000/v1/analysis/quality"
DEFAULT_QUERY = "批次B20260529-A1电芯C240529A001容量低仅4790mAh，请查涂布工序COAT-A2参数和化成数据"


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    token = issue_token("u1", "quality_manager", "P1")
    body = json.dumps({"query": query}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}", file=sys.stderr)
        print(exc.read().decode(), file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
