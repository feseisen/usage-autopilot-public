"""この5時間枠で、どのセッションに何を頼むかの「参考案」を機械的に出す（docs/design.md 3章）。

最終的な判断は指揮役（Opus）が、この参考案と他の材料を天秤にかけて行う。

入力:
- data/usage_snapshots.jsonl → tools/budget.py で予算
- data/targets.json          → tools/targets.py で対象
- <案件フォルダ>/.autopilot/plan.json → 各案件セッションが書く「次の区切り」と見積もり
- data/rounds.jsonl          → これまでに出した指示の記録
出力: 指揮役が実行する指示の一覧（JSON）。--record を付けると rounds.jsonl に記録する

プランの形式（案件セッションが書く）:
{"updated_at": ISO時刻, "next_step": "一文", "estimate_pct": 5時間枠の%, "waiting_for_user": true/false, "note": "任意"}
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from budget import compute, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = Path("C:/Users/<ユーザー名>/Claude")
ROUNDS = ROOT / "data" / "rounds.jsonl"
TARGETS = ROOT / "data" / "targets.json"
USER_ACTIVE = timedelta(minutes=30)
USER_AFTER_SENT = timedelta(hours=3)


def read_rounds():
    if not ROUNDS.exists():
        return []
    return [json.loads(x) for x in ROUNDS.read_text(encoding="utf-8").splitlines() if x.strip()]


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    budget = compute(load(), now)
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    rounds = read_rounds()
    reset5 = budget["five_hour"]["resets_local"]

    last_work, last_plan_req, window_est, last_sent = {}, {}, {}, {}
    for r in rounds:
        last_sent[r["folder"]] = ts(r["at"])
        if r["action"] == "work":
            last_work[r["folder"]] = ts(r["at"])
            if r.get("window") == reset5:
                window_est[r["folder"]] = r.get("estimate_pct", 0)
        if r["action"] == "plan":
            last_plan_req[r["folder"]] = ts(r["at"])

    actions, notes = [], []
    try:
        from resume_cost import for_targets
        resume = for_targets(targets)
    except Exception as e:  # 再開の重さが出せなくても、参考案は出す
        resume = {}
        notes.append(f"再開の重さを出せなかった: {e}")

    if budget["state"] in ("wrap", "hard"):
        for t in targets:
            if t["is_running"]:
                actions.append({"action": "wrap" if budget["state"] == "wrap" else "stop", **t})
        notes.append(budget["reason"])
        return finish(actions, notes, budget, args.record, now, reset5, resume)
    if budget["state"] == "rest":
        # 休む時間: 新しい指示は出さない。作業中のセッションは、今の区切りまで続けてよい
        notes.append(budget["reason"])
        return finish(actions, notes, budget, args.record, now, reset5, resume)
    if budget["state"] == "stale":
        notes.append(budget["reason"])
        return finish(actions, notes, budget, args.record, now, reset5, resume)

    # 作業中のセッションは、この枠で出した見積もりをまだ使い切っていないとみなす（控えめ側）
    remaining = budget["five_hour"]["remaining"]
    candidates = []
    for t in targets:
        folder = t["folder"]
        if t["is_running"]:
            remaining -= window_est.get(folder, 0)
            notes.append(f"{folder}: 作業中")
            continue
        # 本人がそのセッションを使っている最中なら割り込まない。
        # 最後の活動が30分以内で、それが指揮役の指示から3時間以上あとなら、本人の操作とみなす
        act = ts(t.get("last_activity_at"))
        sent = last_sent.get(folder)
        if act and now - act < USER_ACTIVE and (sent is None or act - sent > USER_AFTER_SENT):
            notes.append(f"{folder}: 本人が使用中のため割り込まない")
            continue
        plan_path = PROJECTS / folder / ".autopilot" / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        fresh = plan and (folder not in last_work or ts(plan["updated_at"]) > last_work[folder])
        if not fresh:
            if folder in last_plan_req and (folder not in last_work or last_plan_req[folder] > last_work[folder]):
                notes.append(f"{folder}: 見積もり待ち（依頼済み）")
            else:
                actions.append({"action": "plan", **t})
            continue
        if plan.get("waiting_for_user"):
            notes.append(f"{folder}: 本人の評価待ち — {plan.get('next_step')}")
            continue
        candidates.append((last_work.get(folder, datetime.min.replace(tzinfo=timezone.utc)), t, plan))

    candidates.sort(key=lambda c: c[0])  # しばらく進んでいない案件から
    for _, t, plan in candidates:
        est = float(plan["estimate_pct"])
        # キャッシュが切れていれば、最初の1往復で会話全体を書き直す分が見積もりに上乗せされる
        resume_pct = (resume.get(t["folder"]) or {}).get("resume_pct_now", 0.0)
        need = est + resume_pct
        if est > budget["five_hour"]["ceiling"]:
            actions.append({"action": "split", **t, "estimate_pct": est, "next_step": plan["next_step"]})
        elif need <= remaining:
            actions.append({"action": "work", **t, "estimate_pct": est, "resume_pct": resume_pct,
                            "next_step": plan["next_step"], "budget_pct": round(remaining, 1)})
            remaining -= need
        else:
            notes.append(f"{t['folder']}: 見積もり {est:.0f}% ＋ 再開の重さ {resume_pct:.0f}% が残り {remaining:.0f}% に収まらない。この枠では始めない")

    return finish(actions, notes, budget, args.record, now, reset5, resume)


def finish(actions, notes, budget, record, now, reset5, resume=None):
    if record and actions:
        with ROUNDS.open("a", encoding="utf-8") as f:
            for a in actions:
                f.write(json.dumps({"at": now.isoformat(), "window": reset5, **a}, ensure_ascii=False) + "\n")
    out = {"budget": {"state": budget["state"], "reason": budget["reason"],
                      "five_hour": budget["five_hour"], "seven_day": budget["seven_day"]},
           "actions": actions, "notes": notes,
           # 各セッションを今再開したときの最初の1往復の重さ。キャッシュが有効なうち（cache_warm_until まで）に
           # 次の指示を出せば、この分はほぼかからない（tools/resume_cost.py）
           "resume": resume or {}}
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
