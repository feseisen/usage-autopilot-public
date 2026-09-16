"""7日枠のリセットまでの時間を「まとめて動かす時間（run）」と「休む時間（rest）」に分ける。

考え方（docs/design.md 4章）:
- 7日枠を使い切ればよいので、すべての5時間枠を使う必要はない。動くときは5時間枠をほぼ上限まで
  続けて使い、休むときは何も頼まない。続けて動かせばキャッシュが切れず、会話が大きくても軽い
- どこで動くかは、本人が自分で使わない時間から選ぶ。本人の申告（data/user_rhythm.json）と、
  会話記録から学んだ本人の使い方（data/usage_history.jsonl の本人の使用）を合わせて判定する
- 選び方は仮置き。記録が溜まるごとに判定を詰める

選び方:
1. 自律実行に使える7日枠の残り（100 − 今の使用率 − 7日枠の本人用バッファ）を5時間枠%に換算する
2. 7日枠のリセットまでを1時間ごとに見て、各時刻から始まる5時間のまとまりに「本人が使いそうな量」を付ける
   - 申告の空き時間は 0
   - それ以外は、同じ種類の日（平日／土日）の同じ時刻に本人が使った量の平均。記録が少ないうちは
     10%/時の控えめな値を3日分混ぜる（たまたま使わなかった数日で「空いている」と判定しないため）
3. 本人が使いそうな量が少ないまとまりから、重ならないように選ぶ。選んだまとまりの容量
   （100 − 本人が使いそうな量）の合計が、1 の残りに届いたら止める
4. 今がどれかのまとまりの中なら run、そうでなければ rest

使い方:
    python tools/schedule.py          # 予定を表示し、data/schedule.json に保存
    python tools/schedule.py --json
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RHYTHM = ROOT / "data" / "user_rhythm.json"
HISTORY = ROOT / "data" / "usage_history.jsonl"
SNAPSHOTS = ROOT / "data" / "usage_snapshots.jsonl"
OUT = ROOT / "data" / "schedule.json"
JST = timezone(timedelta(hours=9))

BLOCK_H = 5
UNKNOWN_USER_PCT_PER_H = 10.0   # 記録も申告もない時間に、本人が使いそうだとみなす量（5時間枠%/時）
PRIOR_DAYS = 3                  # 学んだ平均に、上の控えめな値を「3日分観測した」とみなして混ぜる（記録が少ないうちに0と判定しないため）
WEEKLY_BUFFER = 20              # tools/budget.py と同じ仮置き
WEEKLY_TAPER_H = 48


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def is_declared_free(t_local, rhythm):
    minutes = t_local.hour * 60 + t_local.minute
    for f in rhythm.get("free", []):
        if t_local.weekday() in f["weekdays"] and _hm(f["from"]) <= minutes < _hm(f["to"]):
            return True
    return False


def declared_free_between(start, end):
    """start から end まで（10分刻みで見て）ずっと申告の空き時間か。"""
    rhythm = json.loads(RHYTHM.read_text(encoding="utf-8")) if RHYTHM.exists() else {"free": []}
    t = start
    while t < end:
        if not is_declared_free(t.astimezone(JST), rhythm):
            return False
        t += timedelta(minutes=10)
    return True


def learned_user_pct_per_hour():
    """(平日か, 時) → 本人がその1時間に使った5時間枠%の平均。会話記録から換算した値。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import calibration
    calib = calibration.load()
    if not HISTORY.exists():
        return {}
    per_day = defaultdict(float)  # (日付, 平日か, 時) → %
    days = defaultdict(set)       # 平日か → 観測した日付
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        h = datetime.fromisoformat(r["hour"]).astimezone(JST)
        weekday = h.weekday() < 5
        days[weekday].add(h.date())
        if r["origin"] == "user":
            per_day[(h.date(), weekday, h.hour)] += calibration.estimate_pct(r, r["family"], calib)
    out = {}
    for weekday in (True, False):
        n = len(days[weekday])
        if not n:
            continue
        for hour in range(24):
            total = sum(v for (d, wd, hh), v in per_day.items() if wd == weekday and hh == hour)
            out[(weekday, hour)] = (total + UNKNOWN_USER_PCT_PER_H * PRIOR_DAYS) / (n + PRIOR_DAYS)
    return out


def plan(now=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import calibration
    now = now or datetime.now(timezone.utc)
    rhythm = json.loads(RHYTHM.read_text(encoding="utf-8")) if RHYTHM.exists() else {"free": []}
    snaps = [json.loads(x) for x in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    s = snaps[-1]
    used7 = s["seven_day"]["utilization"]
    reset7 = datetime.fromisoformat(s["seven_day"]["resets_at"])
    h7 = max(0.0, (reset7 - now).total_seconds() / 3600)
    b7 = WEEKLY_BUFFER * min(1.0, h7 / WEEKLY_TAPER_H)
    ratio = (calibration.load().get("ratio") or {}).get("value") or 9.0
    budget5 = max(0.0, 100 - used7 - b7) * ratio  # 自律実行に使える量（5時間枠%）

    learned = learned_user_pct_per_hour()

    def user_pct(t):
        loc = t.astimezone(JST)
        if is_declared_free(loc, rhythm):
            return 0.0
        return learned.get((loc.weekday() < 5, loc.hour), UNKNOWN_USER_PCT_PER_H)

    # 候補は、少し前に始まったまとまりも含める（毎回組み直しても、動いている最中のまとまりが途中で外れないように）。
    # 容量は今から先の時間の分だけ数える
    start0 = now.astimezone(JST).replace(minute=0, second=0, microsecond=0) - timedelta(hours=BLOCK_H - 1)
    hours = []
    t = start0
    while t < reset7:
        hours.append(t)
        t += timedelta(hours=1)
    candidates = []
    for i, st in enumerate(hours):
        span = hours[i:i + BLOCK_H]
        if len(span) < BLOCK_H:
            continue  # リセット直前の短いまとまりは、見かけの使用量が少なく出るので候補にしない
        expected = sum(user_pct(x) for x in span)
        free_share = sum(1 for x in span if is_declared_free(x.astimezone(JST), rhythm)) / len(span)
        end = st + timedelta(hours=len(span))
        if end <= now:
            continue
        future_h = sum(1 for x in span if x + timedelta(hours=1) > now)
        candidates.append({"start": st, "end": end, "user_pct": expected,
                           "capacity": max(0.0, min(100.0, 100 - expected) * future_h / BLOCK_H),
                           "free_share": free_share})
    # 本人が使いそうな量が少ない順。同じなら早い方（残りを後で組み直せるように）
    candidates.sort(key=lambda c: (c["user_pct"], c["start"]))
    chosen, total = [], 0.0
    for c in candidates:
        if total >= budget5:
            break
        if c["capacity"] <= 0:
            continue
        if any(c["start"] < o["end"] and o["start"] < c["end"] for o in chosen):
            continue
        chosen.append(c)
        total += c["capacity"]
    chosen.sort(key=lambda c: c["start"])

    current = next((c for c in chosen if c["start"] <= now < c["end"]), None)
    upcoming = next((c for c in chosen if c["start"] > now), None)
    fmt = lambda c: {"start": c["start"].astimezone(JST).isoformat(timespec="minutes"),  # noqa: E731
                     "end": c["end"].astimezone(JST).isoformat(timespec="minutes"),
                     "user_pct": round(c["user_pct"], 1), "capacity": round(c["capacity"], 1),
                     "declared_free_share": round(c["free_share"], 2)}
    result = {
        "generated_at": now.astimezone(JST).isoformat(timespec="minutes"),
        "mode": "run" if current else "rest",
        "current_block": fmt(current) if current else None,
        "next_block": fmt(upcoming) if upcoming else None,
        "weekly_budget_five_hour_pct": round(budget5, 1),
        "planned_capacity_pct": round(total, 1),
        "blocks": [fmt(c) for c in chosen],
        "learned_hours": len(learned),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = plan()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        json.dump(r, sys.stdout, ensure_ascii=False, indent=2)
        return
    print(f"今: {'動く時間' if r['mode'] == 'run' else '休む時間'}"
          f"（自律実行に使える7日枠の残り: 5時間枠 {r['weekly_budget_five_hour_pct']}% 分、予定の容量 {r['planned_capacity_pct']}%）")
    for b in r["blocks"]:
        mark = "◀ 今" if r["current_block"] and b["start"] == r["current_block"]["start"] else ""
        print(f"  {b['start'][5:16]}〜{b['end'][11:16]}  本人が使いそうな量 {b['user_pct']}%"
              f"（申告の空き {b['declared_free_share']*100:.0f}%） 容量 {b['capacity']}% {mark}")


if __name__ == "__main__":
    main()
