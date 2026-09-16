"""公式の使用率から、この5時間枠で自律実行に使ってよい量を計算する。

考え方は docs/design.md 4章。要点:
- 本人用のバッファは、リセットが近づくほど小さくする（直前に残しても使われないため）
- 動くのは tools/schedule.py が選んだ「まとめて動かす時間」だけ。その間は7日枠の残りの範囲で5時間枠を上限近くまで使い、
  それ以外は休む（すべての5時間枠を薄く使うと、休みが1時間を超えるたびにキャッシュが切れて重くなるため）
- 使い残しは、残量から毎回計算し直すので自動で次に上乗せされる

入力: data/usage_snapshots.jsonl（tools/record_usage.py で追記）
出力: 人が読む要約。--json で指揮役が読む形。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "usage_snapshots.jsonl"

WEEKLY_BUFFER = 20        # 7日枠の本人用バッファ（本人の決定 2026-09-13）
WEEKLY_TAPER_H = 48       # この時間を切ったら、7日枠のバッファを 0 に向けて減らす
FIVE_HOUR_BUFFER = 20     # 5時間枠の本人用バッファ（本人の決定 2026-09-13）
FIVE_HOUR_TAPER_H = 2     # この時間を切ったら、5時間枠のバッファを 0 に向けて減らす
UPTIME_DEFAULT = 1.0      # アプリが開いている時間の割合。観測が足りないうちは控えめ側（残りの枠を多く数える）
RATIO_DEFAULT = 9.0       # 7日枠1%あたりの5時間枠%。記録から出せないときに使う
STALE_MIN = 30            # これより古い使用率では判断しない
CALIBRATION = ROOT / "data" / "calibration.json"  # tools/calibration.py が r と稼働率を書く

# 一度限りの例外: 仕組みを始めた 2026-09-13 は、それまで枠を使っていなかったため、
# 7日枠のリセット（同日 19:00）まで5時間枠の余力を残さない（本人の判断）。この時刻を過ぎたら効かない
NO_FIVE_HOUR_BUFFER_UNTIL = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)


def load():
    return [json.loads(x) for x in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if x.strip()]


def _calibration():
    try:
        return json.loads(CALIBRATION.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def estimate_ratio(snaps):
    """7日枠1%あたりの5時間枠%。tools/calibration.py が記録から出した値を優先する。

    それが無ければ、同じ5時間枠の中の区間だけで比を出す（7日枠が3%以上動いていれば）。
    """
    r = _calibration().get("ratio") or {}
    if r.get("value"):
        return r["value"], r.get("weekly_delta", 0)
    num = den = 0
    for a, b in zip(snaps, snaps[1:]):
        ra, rb = (datetime.fromisoformat(x["five_hour"]["resets_at"]) for x in (a, b))
        if abs((ra - rb).total_seconds()) > 120:  # リセット時刻は取得ごとに1秒ほど揺れる
            continue
        d5 = b["five_hour"]["utilization"] - a["five_hour"]["utilization"]
        d7 = b["seven_day"]["utilization"] - a["seven_day"]["utilization"]
        if d7 > 0 and d5 > 0:
            num += d5
            den += d7
    if den >= 3:
        return num / den, den
    return RATIO_DEFAULT, 0


def estimate_uptime():
    """アプリが開いている時間の割合。観測が足りないうちは既定値（控えめ側）。"""
    u = _calibration().get("uptime") or {}
    if u.get("value") and u.get("observed_hours", 0) >= u.get("min_hours_to_use", float("inf")):
        return max(0.1, min(1.0, u["value"])), u["observed_hours"]
    return UPTIME_DEFAULT, u.get("observed_hours", 0)


def taper(buffer, hours_left, taper_h):
    return buffer * min(1.0, max(0.0, hours_left) / taper_h)


sys.path.insert(0, str(Path(__file__).resolve().parent))


def compute(snaps, now=None):
    now = now or datetime.now(timezone.utc)
    s = snaps[-1]
    used5, used7 = s["five_hour"]["utilization"], s["seven_day"]["utilization"]
    reset5 = datetime.fromisoformat(s["five_hour"]["resets_at"])
    reset7 = datetime.fromisoformat(s["seven_day"]["resets_at"])
    h5 = max(0.0, (reset5 - now).total_seconds() / 3600)
    h7 = max(0.0, (reset7 - now).total_seconds() / 3600)
    age_min = (now - datetime.fromisoformat(s["captured_at"])).total_seconds() / 60

    ratio, ratio_basis = estimate_ratio(snaps)
    uptime, uptime_hours = estimate_uptime()
    b7 = taper(WEEKLY_BUFFER, h7, WEEKLY_TAPER_H)
    windows_left = max(1.0, h7 / 5 * uptime)
    weekly_room = max(0.0, 100 - used7 - b7)

    # まとめて動かす時間か、休む時間か（tools/schedule.py）。動く時間は、7日枠の残りの範囲で
    # 5時間枠を上限近くまで使う
    from schedule import plan as block_plan, declared_free_between
    sched = block_plan(now)
    block = sched["current_block"]
    # 今からこの5時間枠のリセットまで、ずっと本人が寝ている申告の時間なら、本人用バッファは要らない
    if now < NO_FIVE_HOUR_BUFFER_UNTIL or declared_free_between(now, reset5):
        b5 = 0.0
    else:
        b5 = taper(FIVE_HOUR_BUFFER, h5, FIVE_HOUR_TAPER_H)
    ceiling5 = min(100 - b5, used5 + weekly_room * ratio)  # 7日枠の残りを超えない
    remaining5 = max(0.0, ceiling5 - used5)

    hard_line = 100 - b5 / 2   # バッファの半分まで食い込んだら、区切りを待たずに止める
    if age_min > STALE_MIN:
        state, reason = "stale", f"使用率の記録が {age_min:.0f} 分前。取り直す"
    elif not block:
        nxt = sched["next_block"]
        state = "rest"
        reason = (f"休む時間。次に動くのは {nxt['start'][5:16].replace('T', ' ')} から" if nxt
                  else "休む時間。7日枠のリセットまで動く予定はない")
        remaining5 = 0.0
    elif used5 >= hard_line:
        state, reason = "hard", f"5時間枠 {used5}% が本人用バッファの半分（{hard_line:.0f}%）を超えた。区切りを待たずに止める"
    elif used5 >= 100 - b5:
        state, reason = "wrap", f"5時間枠 {used5}% が本人用バッファ（{b5:.0f}%）に入った。作業中のセッションに区切りをつけて止まってもらう"
    elif remaining5 <= 0:
        state, reason = "wait", f"この枠の予算を使い切った（上限 {ceiling5:.0f}%）。新しい作業は始めない"
    else:
        state, reason = "go", f"この枠はあと {remaining5:.0f}% 使える（上限 {ceiling5:.0f}%）"

    return {
        "state": state,
        "reason": reason,
        "five_hour": {"used": used5, "buffer": round(b5, 1), "ceiling": round(ceiling5, 1), "hard_line": round(hard_line, 1),
                      "remaining": round(remaining5, 1), "hours_to_reset": round(h5, 2),
                      "resets_local": reset5.astimezone().strftime("%m/%d %H:%M")},
        "seven_day": {"used": used7, "buffer": round(b7, 1), "room": round(weekly_room, 1),
                      "hours_to_reset": round(h7, 2), "windows_left": round(windows_left, 2),
                      "resets_local": reset7.astimezone().strftime("%m/%d %H:%M")},
        "ratio": round(ratio, 2), "ratio_basis_weekly_pct": den_label(ratio_basis),
        "uptime": round(uptime, 3), "uptime_observed_hours": uptime_hours,
        "snapshot_age_min": round(age_min, 1),
        "schedule": {"mode": sched["mode"], "current_block": block, "next_block": sched["next_block"]},
    }


def den_label(den):
    return den if den else "既定値"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = compute(load())
    if args.json:
        json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
        return
    sys.stdout.reconfigure(encoding="utf-8")
    f5, f7 = out["five_hour"], out["seven_day"]
    label = {"go": "進めてよい", "rest": "休む時間", "wait": "新しい作業は始めない", "wrap": "区切りをつけて止まってもらう", "hard": "区切りを待たずに止める", "stale": "使用率を取り直す"}
    print(f"判定: {label[out['state']]} — {out['reason']}")
    print(f"5時間枠: {f5['used']}% / 上限 {f5['ceiling']:.0f}%（バッファ {f5['buffer']:.0f}%、{f5['resets_local']} リセット、あと {f5['hours_to_reset']:.1f} 時間）")
    print(f"7日枠  : {f7['used']}% / バッファ {f7['buffer']:.0f}%（{f7['resets_local']} リセット、あと {f7['hours_to_reset']:.1f} 時間、5時間枠は残り約 {f7['windows_left']:.1f} 回）")
    print(f"換算   : 7日枠1% ≈ 5時間枠 {out['ratio']:.1f}%（{"実測: 7日枠 " + str(out["ratio_basis_weekly_pct"]) + "% 分の記録から" if out["ratio_basis_weekly_pct"] != "既定値" else "既定値"}）")
    print(f"稼働率 : {out['uptime']}（観測 {out['uptime_observed_hours']} 時間。足りないうちは 1.0）")


if __name__ == "__main__":
    main()
