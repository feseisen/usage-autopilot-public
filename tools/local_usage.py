"""Claude Code のセッション記録から、直近5時間・7日のトークン使用量を集計する。

公式の使用率（%）ではない。このPCの Claude Code 分だけを数えた実測値。
claude.ai のチャットや Cowork、他のPCでの利用は含まれない。

使い方:
    python tools/local_usage.py            # 表示
    python tools/local_usage.py --json     # 機械可読
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"

# 料金の相対比（入力=1）。モデル間の差は含めず、同じモデル内の重み付けだけに使う。
WEIGHT = {"input": 1.0, "cache_write": 2.0, "cache_read": 0.1, "output": 5.0}


def iter_requests():
    seen = set()
    for path in PROJECTS.rglob("*.jsonl"):
        project = path.relative_to(PROJECTS).parts[0]
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                if not usage or d.get("type") != "assistant":
                    continue
                # 1回のAPI応答が content block ごとに複数行へ分かれるため requestId で重複を除く
                key = d.get("requestId") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                yield {
                    "ts": datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")),
                    "project": project,
                    "model": msg.get("model", "?"),
                    "sidechain": bool(d.get("isSidechain")),
                    "input": usage.get("input_tokens", 0),
                    "cache_write": usage.get("cache_creation_input_tokens", 0),
                    "cache_read": usage.get("cache_read_input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                }


def summarize(reqs, since):
    total = defaultdict(float)
    by_model = defaultdict(lambda: defaultdict(float))
    by_project = defaultdict(float)
    for r in reqs:
        if r["ts"] < since:
            continue
        w = sum(r[k] * WEIGHT[k] for k in WEIGHT)
        total["requests"] += 1
        total["weighted"] += w
        for k in WEIGHT:
            total[k] += r[k]
            by_model[r["model"]][k] += r[k]
        by_model[r["model"]]["weighted"] += w
        by_project[r["project"]] += w
    return {
        "total": dict(total),
        "by_model": {m: dict(v) for m, v in by_model.items()},
        "by_project": dict(sorted(by_project.items(), key=lambda x: -x[1])),
    }


def hourly(reqs, since):
    buckets = defaultdict(float)
    for r in reqs:
        if r["ts"] < since:
            continue
        h = r["ts"].astimezone().replace(minute=0, second=0, microsecond=0)
        buckets[h.isoformat()] += sum(r[k] * WEIGHT[k] for k in WEIGHT)
    return dict(sorted(buckets.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    reqs = list(iter_requests())
    result = {
        "generated_at": now.astimezone().isoformat(),
        "scope": "このPCの Claude Code のみ",
        "last_5h": summarize(reqs, now - timedelta(hours=5)),
        "last_7d": summarize(reqs, now - timedelta(days=7)),
        "hourly_7d": hourly(reqs, now - timedelta(days=7)),
    }
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        return

    def fmt(n):
        return f"{n/1e6:.2f}M" if n >= 1e6 else f"{n/1e3:.0f}k"

    for label, key in (("直近5時間", "last_5h"), ("直近7日", "last_7d")):
        t = result[key]["total"]
        print(f"■ {label}: {int(t.get('requests', 0))}回 / 重み付き {fmt(t.get('weighted', 0))}")
        print(f"   入力 {fmt(t.get('input', 0))} / キャッシュ書込 {fmt(t.get('cache_write', 0))}"
              f" / キャッシュ読込 {fmt(t.get('cache_read', 0))} / 出力 {fmt(t.get('output', 0))}")
        for p, w in list(result[key]["by_project"].items())[:6]:
            print(f"   - {p}: {fmt(w)}")
    print("\n■ 日別（重み付き）")
    daily = defaultdict(float)
    for h, w in result["hourly_7d"].items():
        daily[h[:10]] += w
    for d, w in daily.items():
        print(f"   {d}: {fmt(w)}")


if __name__ == "__main__":
    main()
