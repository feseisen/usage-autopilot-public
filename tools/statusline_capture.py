"""ステータスラインに渡される JSON から、公式の使用率（5時間枠・7日枠）を記録する。

Claude Code は Pro/Max 契約のとき、ステータスラインのスクリプトへ
rate_limits.five_hour / seven_day の used_percentage と resets_at を渡す。
これを data/rate_limits.jsonl に追記し、画面には短い要約だけを出す。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "data" / "rate_limits.jsonl"


def main():
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return
    rl = d.get("rate_limits") or {}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "session_id": d.get("session_id"),
            "rate_limits": rl,
        }, ensure_ascii=False) + "\n")
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rl.get(key)
        if w and w.get("used_percentage") is not None:
            reset = datetime.fromtimestamp(w["resets_at"]).strftime("%m/%d %H:%M")
            parts.append(f"{label} {w['used_percentage']:.0f}% (reset {reset})")
    sys.stdout.reconfigure(encoding="utf-8")
    print(" | ".join(parts) if parts else "usage: n/a")


if __name__ == "__main__":
    main()
