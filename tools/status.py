"""本人が覗いたときに、全体が1枚で分かる data/status.md を作る。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from budget import compute, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = Path("C:/Users/<ユーザー名>/Claude")
LABEL = {"go": "進めてよい", "rest": "休む時間", "wait": "この枠の予算を使い切った", "wrap": "本人用バッファに入ったため区切りで停止", "hard": "本人用バッファの半分を超えたため停止", "stale": "使用率が古い"}
ACTION = {"plan": "見積もりを依頼", "work": "作業を依頼", "split": "区切りの分割を依頼", "wrap": "区切りで止まるよう依頼", "stop": "停止"}


SCAN_INTERVAL_MIN = 30   # 使用率スキャンの定期タスクの間隔
SCAN_STALE_MIN = 45      # これより古ければ、スキャンが止まっている疑いがある


def scan_health(age_min):
    """最後の使用率の記録からの経過で、使用率スキャンが動いているかを1行で示す。"""
    if age_min <= SCAN_STALE_MIN:
        return f"使用率スキャン: 動いている（最後の記録は {age_min:.0f} 分前）"
    return (f"⚠ **使用率スキャンが止まっている疑い**: 最後の記録は {age_min:.0f} 分前（間隔は {SCAN_INTERVAL_MIN} 分）。"
            "実行中のまま残っている回が次の回を止めていないか、スケジュールタスクの実行記録を確かめる（prompts/orchestrator.md 手順1-1）")


def main():
    now = datetime.now(timezone.utc)
    b = compute(load(), now)
    f5, f7 = b["five_hour"], b["seven_day"]
    targets = json.loads((ROOT / "data" / "targets.json").read_text(encoding="utf-8"))
    rounds_path = ROOT / "data" / "rounds.jsonl"
    rounds = [json.loads(x) for x in rounds_path.read_text(encoding="utf-8").splitlines() if x.strip()] if rounds_path.exists() else []

    lines = [
        "# 自律実行の状況",
        "",
        f"更新: {now.astimezone():%m/%d %H:%M}",
        "",
        "## 使用率",
        "",
        "| 枠 | 使用率 | 自律実行の上限 | 本人用バッファ | リセット |",
        "|---|---|---|---|---|",
        f"| 5時間枠 | {f5['used']}% | {f5['ceiling']:.0f}% | {f5['buffer']:.0f}% | {f5['resets_local']} |",
        f"| 7日枠 | {f7['used']}% | — | {f7['buffer']:.0f}% | {f7['resets_local']} |",
        "",
        f"判定: **{LABEL[b['state']]}** — {b['reason']}",
        "",
        scan_health(b["snapshot_age_min"]),
        "",
        "## 案件",
        "",
        "| 案件 | セッション | 次の区切り | 見積もり | 状態 |",
        "|---|---|---|---|---|",
    ]
    for t in targets:
        plan_path = PROJECTS / t["folder"] / ".autopilot" / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
        state = "作業中" if t["is_running"] else ("本人の評価待ち" if plan.get("waiting_for_user") else "待機")
        est = f"{plan['estimate_pct']}%" if "estimate_pct" in plan else "—"
        lines.append(f"| {t['folder']} | {t['title']} | {plan.get('next_step', '（見積もり未提出）')} | {est} | {state} |")
    lines += ["", "## 直近の指示", ""]
    for r in rounds[-10:][::-1]:
        at = datetime.fromisoformat(r["at"]).astimezone()
        lines.append(f"- {at:%m/%d %H:%M} {r['folder']}: {ACTION.get(r['action'], r['action'])}"
                     + (f"（{r['next_step']}、見積もり {r['estimate_pct']}%）" if r.get("next_step") else ""))
    (ROOT / "data" / "status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("data/status.md を更新")


if __name__ == "__main__":
    main()
