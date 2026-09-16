"""ある期間に、案件ごとにどれだけ5時間枠を使ったかを推定する。

会話記録のトークン量を、公式の使用率から当てはめた係数（tools/calibration.py）で5時間枠の%に換算する。
案件ごと・本人／自律実行ごとに出す。同じ期間の公式の増え方も並べるので、換算のずれも見える。

限界:
- claude.ai のチャットや他の端末の利用は会話記録に無いので、ここには出ない（公式の増え方との差に現れる）
- Sonnet の係数は、Sonnet が主の区間が溜まるまで Opus と同じ 1.0 として扱う

使い方:
    python tools/actuals.py --since 2026-09-13T08:00:00+09:00 [--until ...] [--json]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibration  # noqa: E402
from usage_history import load_requests  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


SNAP_TOLERANCE = timedelta(minutes=15)  # 期間の端から、これより離れた記録では比べない


def official_delta(since, until):
    """期間の始まりと終わりに一番近い使用率の記録で、5時間枠%の増え方を出す。

    記録は時刻ちょうどには取れないので、端の前後どちらでも一番近いものを使う（前だけに限ると、
    数十秒あとに取れた記録を飛ばして、ずっと前の記録と比べてしまう）。
    近い記録が無い、または5時間枠をまたぐなら None。使った記録の時刻も返す。
    """
    snaps = [json.loads(x) for x in (ROOT / "data" / "usage_snapshots.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if not snaps:
        return None, None, None
    for s in snaps:
        s["_t"] = datetime.fromisoformat(s["captured_at"])
    a = min(snaps, key=lambda s: abs(s["_t"] - since))
    b = min(snaps, key=lambda s: abs(s["_t"] - until))
    if b is a or abs(a["_t"] - since) > SNAP_TOLERANCE or abs(b["_t"] - until) > SNAP_TOLERANCE:
        return None, a["_t"], b["_t"]
    ra, rb = (datetime.fromisoformat(x["five_hour"]["resets_at"]) for x in (a, b))
    if abs((ra - rb).total_seconds()) > 120:
        return None, a["_t"], b["_t"]
    return b["five_hour"]["utilization"] - a["five_hour"]["utilization"], a["_t"], b["_t"]


def parse_time(s):
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.astimezone()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    since = parse_time(args.since).astimezone(timezone.utc)
    until = parse_time(args.until).astimezone(timezone.utc) if args.until else datetime.now(timezone.utc)

    calib = calibration.load()
    pct = defaultdict(lambda: defaultdict(float))
    for r in load_requests():
        if since <= r["ts"] < until:
            pct[r["folder"]][r["origin"]] += calibration.estimate_pct(r, r["family"], calib)
    rows = [{"folder": f, "five_hour_pct": round(sum(v.values()), 1),
             "user_pct": round(v["user"], 1), "autopilot_pct": round(v["autopilot"], 1)}
            for f, v in sorted(pct.items(), key=lambda x: -sum(x[1].values()))]
    delta, snap_a, snap_b = official_delta(since, until)
    out = {"since": since.astimezone().isoformat(), "until": until.astimezone().isoformat(),
           "official_five_hour_delta": delta,
           "official_compared": [t.astimezone().isoformat(timespec="minutes") if t else None for t in (snap_a, snap_b)], "estimated_total": round(sum(r["five_hour_pct"] for r in rows), 1),
           "calibration": calib.get("source"), "by_folder": rows}
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        return
    print(f"期間: {out['since'][:16]} 〜 {out['until'][:16]}　推定の合計 {out['estimated_total']}%"
          f"（公式の増え方: {'取得不可' if delta is None else str(delta) + '%'}"
          f"{'' if delta is None else '（' + out['official_compared'][0][11:16] + ' と ' + out['official_compared'][1][11:16] + ' の記録）'}、係数: {out['calibration']}）")
    for r in rows:
        print(f"  {r['folder']}: {r['five_hour_pct']}%（本人 {r['user_pct']}% / 自律実行 {r['autopilot_pct']}%）")


if __name__ == "__main__":
    main()
