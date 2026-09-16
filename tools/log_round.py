"""指揮役が実際に出した指示を、理由と一緒に data/rounds.jsonl に記録する。

allocate.py の参考案と違う判断をしたときも、ここに書く記録が正になる。

入力（標準入力の JSON。1件でも配列でもよい）:
{"action": "plan|work|split|wrap|stop|skip", "folder": "...", "session_id": "...",
 "next_step": "任意", "estimate_pct": 任意, "corrected_estimate_pct": 任意（過去のずれで補正した値）,
 "reason": "この判断をした理由（日本語で一文）", "delivery": "delivered|queued|error など任意"}
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from budget import compute, load  # noqa: E402

ROUNDS = Path(__file__).resolve().parent.parent / "data" / "rounds.jsonl"

d = json.loads(sys.stdin.read())
items = d if isinstance(d, list) else [d]
b = compute(load())
now = datetime.now(timezone.utc).isoformat()
with ROUNDS.open("a", encoding="utf-8") as f:
    for it in items:
        rec = {"at": now, "window": b["five_hour"]["resets_local"],
               "five_hour_used": b["five_hour"]["used"], "seven_day_used": b["seven_day"]["used"], **it}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
sys.stdout.reconfigure(encoding="utf-8")
print(f"{len(items)} 件を記録")
