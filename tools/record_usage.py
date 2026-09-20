"""fetch_usage.js の返り値（JSON）を標準入力で受け取り、data/usage_snapshots.jsonl に追記する。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "data" / "usage_snapshots.jsonl"

d = json.loads(sys.stdin.read())
if isinstance(d, str):  # javascript_tool の出力が文字列として二重に包まれている場合
    d = json.loads(d)

# 使用率が入っていない返り値（organizations は通ったが usage が 403 など）は追記しない。
# 追記してしまうと、以後 tools/budget.py と tools/status.py がその行で落ち続ける
for k in ("five_hour", "seven_day"):
    if not isinstance(d.get(k), dict) or "utilization" not in d[k]:
        sys.exit(f"{k} が入っていないため記録しない（ログインが切れている可能性）: {d}")

d = {"captured_at": datetime.now(timezone.utc).isoformat(),
     "source": "claude.ai usage API (browser pane)", **d}
LOG.parent.mkdir(exist_ok=True)
with LOG.open("a", encoding="utf-8") as f:
    f.write(json.dumps(d, ensure_ascii=False) + "\n")
print("recorded", d["five_hour"]["utilization"], d["seven_day"]["utilization"])
