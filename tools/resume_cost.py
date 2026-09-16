"""セッションを再開するときの重さを見積もる。

会話は毎回、それまでの内容をキャッシュから読み込んで続く。キャッシュの読み込みはほぼ枠を使わないが、
キャッシュが切れていると会話全体を書き直すため、1往復で枠を大きく使う（tools/calibration.py）。
キャッシュの有効時間は、会話記録の cache_creation に残る種類で判別する（ephemeral_1h なら60分、5m なら5分）。

■ 出すもの
- 案件ごと（指揮役の対象のセッションと、指揮役自身）: 会話の大きさ、最後の応答からの経過、キャッシュが
  切れる時刻、今再開したときの見込みの重さ（5時間枠%）
- 裏付け: 過去の会話記録で、キャッシュが切れてから再開した1往復と、切れる前に続けた1往復の重さの比較

■ セッションと会話記録の結び付け
アプリの記録（%APPDATA%/Claude/claude-code-sessions/**/local_*.json）の cliSessionId が、
会話記録のファイル名（~/.claude/projects/<フォルダ>/<cliSessionId>.jsonl）になっている。

使い方:
    python tools/resume_cost.py            # 対象（data/targets.json）と指揮役の再開の重さ、過去の裏付けを表示
    python tools/resume_cost.py --json
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calibration  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_LOG = Path.home() / ".claude" / "projects"
APP_SESSIONS = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code-sessions"
TARGETS = ROOT / "data" / "targets.json"
ORCHESTRATOR = ROOT / "data" / "orchestrator.json"


def session_files():
    """local_... のセッションID → 会話記録のファイル。"""
    out = {}
    for p in APP_SESSIONS.glob("*/*/local_*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cli = d.get("cliSessionId")
        if not cli:
            continue
        hits = list(PROJECTS_LOG.glob(f"*/{cli}.jsonl"))
        if hits:
            out[d["sessionId"]] = {"file": hits[0], "title": d.get("title"), "cwd": d.get("cwd")}
    return out


def requests_of(path):
    """会話記録1つ分の応答を、時刻順に返す。"""
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
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
        cc = u.get("cache_creation") or {}
        out.append({"ts": datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")), "family": fam,
                    "input": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
                    "cache_write": u.get("cache_creation_input_tokens", 0), "output": u.get("output_tokens", 0),
                    "ttl_min": 60 if cc.get("ephemeral_1h_input_tokens") else (5 if cc.get("ephemeral_5m_input_tokens") else None)})
    out.sort(key=lambda r: r["ts"])
    return out


def context_tokens(r):
    """その応答のあとに続けるとき、読み込み直す会話の大きさ。"""
    return r["input"] + r["cache_read"] + r["cache_write"] + r["output"]


def ttl_of(reqs):
    for r in reversed(reqs):
        if r["ttl_min"]:
            return r["ttl_min"]
    return 5  # 分からなければ短い方


def current_cost(reqs, calib, now):
    """今このセッションに指示を送ったら、最初の1往復でどれだけ使うかの見込み。"""
    if not reqs:
        return None
    last = reqs[-1]
    ttl = ttl_of(reqs)
    ctx = context_tokens(last)
    warm_until = last["ts"] + timedelta(minutes=ttl)
    cold = now >= warm_until
    cold_pct = calibration.estimate_pct({"cache_write": ctx}, last["family"], calib)
    return {
        "context_tokens": ctx,
        "idle_min": round((now - last["ts"]).total_seconds() / 60),
        "cache_ttl_min": ttl,
        "cache_warm_until": warm_until.astimezone().isoformat(timespec="minutes"),
        "cache_cold": cold,
        "resume_pct_now": round(cold_pct if cold else 0.0, 1),
        "resume_pct_if_cold": round(cold_pct, 1),
    }


def evidence(calib):
    """過去の会話記録から、キャッシュが切れてから再開した1往復と、切れる前に続けた1往復を比べる。"""
    cold, warm = [], []
    for path in PROJECTS_LOG.glob("C--Users-<ユーザー名>-Claude*/*.jsonl"):
        reqs = requests_of(path)
        for prev, cur in zip(reqs, reqs[1:]):
            gap = (cur["ts"] - prev["ts"]).total_seconds() / 60
            ttl = cur["ttl_min"] or prev["ttl_min"] or 5
            if gap < 3:
                continue  # 1ターンの中の続きの応答は比べない
            ctx = context_tokens(prev)
            if ctx < 50_000:
                continue  # 小さい会話は差が出にくい
            rec = {"gap_min": gap, "context": ctx,
                   "rewrite_share": cur["cache_write"] / ctx,
                   "pct": calibration.estimate_pct(cur, cur["family"], calib),
                   "pct_per_100k": calibration.estimate_pct(cur, cur["family"], calib) / (ctx / 100_000)}
            (cold if gap >= ttl else warm).append(rec)

    def summary(rows):
        if not rows:
            return {"count": 0}
        return {"count": len(rows),
                "median_rewrite_share": round(statistics.median(r["rewrite_share"] for r in rows), 3),
                "median_pct": round(statistics.median(r["pct"] for r in rows), 2),
                "median_pct_per_100k_context": round(statistics.median(r["pct_per_100k"] for r in rows), 2)}
    return {"cold": summary(cold), "warm": summary(warm)}


def cold_resumes(start, end, calib=None):
    """期間の中で、キャッシュが切れた状態から再開した1往復の回数と、その推定消費（5時間枠%）。報告書の欄に使う。"""
    from usage_history import folder_of
    calib = calib or calibration.load()
    by_folder = {}
    for path in PROJECTS_LOG.glob("C--Users-<ユーザー名>-Claude*/*.jsonl"):
        reqs = requests_of(path)
        for prev, cur in zip(reqs, reqs[1:]):
            if not (start <= cur["ts"] < end):
                continue
            ttl = cur["ttl_min"] or prev["ttl_min"] or 5
            if (cur["ts"] - prev["ts"]).total_seconds() / 60 < ttl:
                continue
            folder = folder_of(path.parent.name)
            rec = by_folder.setdefault(folder, {"count": 0, "pct": 0.0})
            rec["count"] += 1
            rec["pct"] += calibration.estimate_pct(cur, cur["family"], calib)
    for rec in by_folder.values():
        rec["pct"] = round(rec["pct"], 1)
    return {"count": sum(r["count"] for r in by_folder.values()),
            "pct": round(sum(r["pct"] for r in by_folder.values()), 1),
            "by_folder": dict(sorted(by_folder.items(), key=lambda x: -x[1]["pct"]))}


def for_targets(targets, calib=None, now=None):
    """指揮役の対象のセッション（と指揮役自身）の再開の重さ。tools/allocate.py から使う。"""
    calib = calib or calibration.load()
    now = now or datetime.now(timezone.utc)
    files = session_files()
    out = {}
    ids = [(t["folder"], t["session_id"]) for t in targets]
    if ORCHESTRATOR.exists():
        oid = json.loads(ORCHESTRATOR.read_text(encoding="utf-8")).get("session_id")
        if oid:
            ids.append(("(指揮役)", oid))
    for folder, sid in ids:
        f = files.get(sid)
        out[folder] = {"session_id": sid, **(current_cost(requests_of(f["file"]), calib, now) or {})} if f else {"session_id": sid, "note": "会話記録が見つからない"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    calib = calibration.load()
    targets = json.loads(TARGETS.read_text(encoding="utf-8")) if TARGETS.exists() else []
    result = {"sessions": for_targets(targets, calib), "evidence": evidence(calib)}
    sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        return
    print("■ 今再開したときの最初の1往復の重さ（5時間枠%）")
    for folder, c in result["sessions"].items():
        if "context_tokens" not in c:
            print(f"  {folder}: {c.get('note')}")
            continue
        state = "キャッシュ切れ" if c["cache_cold"] else f"キャッシュ有効（{c['cache_warm_until'][11:16]} まで）"
        print(f"  {folder}: 会話 {c['context_tokens']/1000:.0f}k トークン / 最後の応答から {c['idle_min']} 分 / {state}"
              f" → 今なら {c['resume_pct_now']}%、切れていれば {c['resume_pct_if_cold']}%")
    e = result["evidence"]
    print("■ 過去の記録での比較（会話が5万トークン以上のとき、ターンの最初の1往復）")
    for key, label in (("cold", "キャッシュが切れてから再開"), ("warm", "切れる前に続けた")):
        s = e[key]
        if not s["count"]:
            print(f"  {label}: 記録なし")
            continue
        print(f"  {label}: {s['count']} 件、書き直した割合の中央値 {s['median_rewrite_share']*100:.0f}%、"
              f"1往復の中央値 {s['median_pct']}%、会話10万トークンあたり {s['median_pct_per_100k_context']}%")


if __name__ == "__main__":
    main()
