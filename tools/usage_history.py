"""使用量の履歴を、本人の使用と自律実行の使用に分けて保存し続ける。

本人の使用を予測する土台（docs/design.md 4章）。Claude Code は古い会話記録を消すことがあるので、
会話記録から1時間ごと・フォルダごと・由来ごと・モデルごとに集計したトークン量（種類別の生の数）を
data/usage_history.jsonl に残す。生の数で残すので、換算の係数（tools/calibration.py）が変わっても計算し直せる。
一度記録した時間帯の値は、会話記録が消えても減らさない（集計し直したときは多い方を残す）。

■ 本人の使用か、自律実行の使用か
会話記録の中で、各ターンを始めたメッセージで見分ける。ツールの結果や、スキルなどシステムが差し込んだ
メッセージはターンの始まりとみなさない。
- 自律実行: 他のセッションからの指示、定期タスク、指揮役の繰り返し実行（orchestrator.md を指定した /loop の
            セッションはそのセッション全体）、システムからの通知で動いたターン
- 本人: それ以外（本人がチャットに打ち込んだ発言）

■ 5時間枠ごとの内訳と検証（data/usage_windows.json）
- 自律実行の使用 = 会話記録から換算した自律実行の量（公式の使用率を上限とする）
- 本人の使用 = 公式の使用率 − 自律実行の使用（会話記録に無い claude.ai のチャットなども本人側に入る）
- 仮置きの余力 = その枠の開始時点で tools/budget.py の規則が本人用に残した量
- 予測なら残した量 = その枠より前の、同じ種類の日（平日／土日）の同じ時間帯に本人が使った量の上位25%
  （会話記録から換算した量。claude.ai のチャットは含まない）

使い方:
    python tools/usage_history.py          # 係数・履歴・枠ごとの内訳を更新して要約を表示
    python tools/usage_history.py --json   # 枠ごとの内訳を JSON で表示
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibration  # noqa: E402
from budget import FIVE_HOUR_BUFFER, NO_FIVE_HOUR_BUFFER_UNTIL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "usage_history.jsonl"
WINDOWS = ROOT / "data" / "usage_windows.json"
SNAPSHOTS = ROOT / "data" / "usage_snapshots.jsonl"
PROJECTS_LOG = Path.home() / ".claude" / "projects"
PROJECTS_ROOT = Path("C:/Users/<ユーザー名>/Claude")
PARENT_PREFIX = "C--Users-<ユーザー名>-Claude"
JST = timezone(timedelta(hours=9))
TOKENS = ("input", "cache_read", "cache_write", "output")

AUTOPILOT_MARKERS = ("Another Claude session sent a message", "<cross-session-message",
                     "<scheduled-task", "<task-notification", "[SYSTEM NOTIFICATION")


def _log_name(folder):
    """Claude Code が会話記録のフォルダ名にするときの変換（英数字以外は - になる）。"""
    return "".join(ch if ch.isalnum() else "-" for ch in folder)


def folder_of(log_dir_name):
    """会話記録のフォルダ名から、実際の案件フォルダ名に戻す（youtube-dashboard → youtube_dashboard）。"""
    if log_dir_name == PARENT_PREFIX:
        return "(親フォルダ)"
    name = log_dir_name[len(PARENT_PREFIX) + 1:]
    for d in PROJECTS_ROOT.iterdir():
        if d.is_dir() and _log_name(d.name) == name:
            return d.name
    return name


def prompt_text(d):
    """ターンを始めうるユーザー側のメッセージなら、その本文を返す。ツールの結果だけなら None。"""
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        texts = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
        return texts[0] if texts else None
    return None


def origin_of(text, is_meta):
    """ターンの始まりなら 'autopilot' / 'user' を返す。始まりとみなさないメッセージは None。"""
    head = text.lstrip()[:600]
    if any(m in head for m in AUTOPILOT_MARKERS):
        return "autopilot"
    if is_meta:
        return None
    if "orchestrator.md" in head:
        return "autopilot"
    return "user"


def load_requests():
    """会話記録から、API の応答1件ごとに時刻・フォルダ・由来・モデル・トークン量を返す。"""
    out, seen = [], set()
    for path in PROJECTS_LOG.glob(f"{PARENT_PREFIX}*/**/*.jsonl"):
        folder = folder_of(path.relative_to(PROJECTS_LOG).parts[0])
        records = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        whole_autopilot = any(
            r.get("type") == "user" and "orchestrator.md" in (prompt_text(r) or "") and "/loop" in (prompt_text(r) or "")
            for r in records)
        origin = "autopilot" if whole_autopilot else "user"
        for d in records:
            if d.get("type") == "user":
                if not whole_autopilot:
                    text = prompt_text(d)
                    if text is not None:
                        origin = origin_of(text, bool(d.get("isMeta"))) or origin
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            fam = calibration.family(msg.get("model"))
            if d.get("type") != "assistant" or not u or not fam:
                continue
            key = d.get("requestId") or d.get("uuid")
            if key in seen:
                continue
            seen.add(key)
            out.append({"ts": datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")),
                        "folder": folder, "origin": origin, "family": fam,
                        "input": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
                        "cache_write": u.get("cache_creation_input_tokens", 0), "output": u.get("output_tokens", 0)})
    return out


def hourly(requests):
    agg = defaultdict(lambda: {**dict.fromkeys(TOKENS, 0), "requests": 0})
    for r in requests:
        hour = r["ts"].astimezone(JST).replace(minute=0, second=0, microsecond=0).isoformat()
        a = agg[(hour, r["folder"], r["origin"], r["family"])]
        for k in TOKENS:
            a[k] += r[k]
        a["requests"] += 1
    return agg


def update_history(fresh):
    """既存の履歴と合わせる。同じ時間帯は多い方を残す（会話記録が消えても値を減らさない）。"""
    merged = {}
    if HISTORY.exists():
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if "family" not in r:
                continue  # 旧形式（重み付きの値だけの行）。会話記録から作り直せる。旧版はコミット c6e1467 に残っている
            r["folder"] = folder_of(f"{PARENT_PREFIX}-{_log_name(r['folder'])}") if r["folder"] != "(親フォルダ)" else r["folder"]
            key = (r["hour"], r["folder"], r["origin"], r["family"])
            if key not in merged or sum(r[k] for k in TOKENS) > sum(merged[key][k] for k in TOKENS):
                merged[key] = r
    size = lambda r: r["cache_read"] + r["cache_write"] + r["output"] + r["input"]  # noqa: E731
    for (hour, folder, origin, fam), v in fresh.items():
        row = {"hour": hour, "folder": folder, "origin": origin, "family": fam, **v}
        old = merged.get((hour, folder, origin, fam))
        if old is None or size(row) > size(old):
            merged[(hour, folder, origin, fam)] = row
    rows = sorted(merged.values(), key=lambda r: (r["hour"], r["folder"], r["origin"], r["family"]))
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return rows


def pct_of(rows, calib, start, end, origin=None):
    total = 0.0
    for r in rows:
        h = datetime.fromisoformat(r["hour"])
        if start <= h < end and (origin is None or r["origin"] == origin):
            total += calibration.estimate_pct(r, r["family"], calib)
    return total


def forecast_buffer(rows, calib, start):
    """その枠より前の、同じ種類の日の同じ時間帯に、本人が使った量の上位25%。"""
    weekend = start.weekday() >= 5
    first = min((datetime.fromisoformat(r["hour"]) for r in rows), default=None)
    if first is None:
        return None, 0
    values = []
    day = start - timedelta(days=1)
    while day.date() >= first.date():
        if (day.weekday() >= 5) == weekend:
            values.append(pct_of(rows, calib, day, day + timedelta(hours=5), origin="user"))
        day -= timedelta(days=1)
    if not values:
        return None, 0
    values.sort()
    return round(float(values[int(round(0.75 * (len(values) - 1)))]), 1), len(values)


def windows_breakdown(rows, calib):
    snaps = [json.loads(x) for x in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    last_by_window = {}
    for s in snaps:
        # リセット時刻は取得ごとに前後へ1秒ほど揺れる。近い5分に丸めて同じ枠とみなす
        end = datetime.fromisoformat(s["five_hour"]["resets_at"]).astimezone(JST) + timedelta(seconds=150)
        key = end.replace(minute=(end.minute // 5) * 5, second=0, microsecond=0)
        if key not in last_by_window or s["captured_at"] > last_by_window[key]["captured_at"]:
            last_by_window[key] = s
    result = []
    for end, s in sorted(last_by_window.items()):
        start = end - timedelta(hours=5)
        util = s["five_hour"]["utilization"]
        auto_est = pct_of(rows, calib, start, end, origin="autopilot")
        user_logged = pct_of(rows, calib, start, end, origin="user")
        autopilot_pct = min(util, auto_est)
        fb, days = forecast_buffer(rows, calib, start)
        result.append({
            "id": end.strftime("%Y-%m-%d_%H%M"),
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "five_hour_used": util,
            "measured_at": datetime.fromisoformat(s["captured_at"]).astimezone(JST).isoformat(),
            "autopilot_pct": round(autopilot_pct, 1),
            "autopilot_est_pct": round(auto_est, 1),
            "user_pct": round(util - autopilot_pct, 1),
            "user_logged_pct": round(user_logged, 1),
            "provisional_buffer_pct": 0.0 if start < NO_FIVE_HOUR_BUFFER_UNTIL else float(FIVE_HOUR_BUFFER),
            "forecast_buffer_pct": fb, "forecast_basis_days": days,
            "by_folder": {f: {o: round(pct_of([r for r in rows if r["folder"] == f], calib, start, end, o), 1)
                              for o in ("user", "autopilot")}
                          for f in sorted({r["folder"] for r in rows
                                           if start <= datetime.fromisoformat(r["hour"]) < end})},
        })
    WINDOWS.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def refresh():
    """係数の当てはめ・履歴の更新・枠ごとの内訳を順に行う。報告書を組み立てるときにも呼ばれる。"""
    requests = load_requests()
    calib = calibration.fit(requests)
    rows = update_history(hourly(requests))
    return calib, rows, windows_breakdown(rows, calib)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    calib, rows, windows = refresh()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        json.dump(windows, sys.stdout, ensure_ascii=False, indent=2)
        return
    c = calib["coef"]
    print(f"係数: {calib['source']}（Opus 区間 {calib['opus_samples']} 件、誤差 {calib.get('opus_rmse_pct', '—')}%）"
          f" 読込 {c['cache_read']} / 書込 {c['cache_write']} / 出力 {c['output']}、Sonnet ×{calib['family_factor']['sonnet']}")
    print(f"履歴: {len(rows)} 行")
    for w in windows:
        fb = "—" if w["forecast_buffer_pct"] is None else f"{w['forecast_buffer_pct']}%（{w['forecast_basis_days']}日分）"
        print(f"  {w['window_start'][5:16]}–{w['window_end'][11:16]}  使用率 {w['five_hour_used']}% ="
              f" 本人 {w['user_pct']}%（うち会話記録 {w['user_logged_pct']}%）+ 自律実行 {w['autopilot_pct']}%"
              f" ｜ 余力: 仮置き {w['provisional_buffer_pct']:.0f}% / 予測なら {fb}")


if __name__ == "__main__":
    main()
