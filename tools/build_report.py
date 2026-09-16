"""data/reports/*.json（5時間枠ごとの報告）を1枚のページにまとめ、report/index.html を作る。

過去の報告もすべてページに埋め込むので、左の一覧からワンタッチで切り替えられる。
報告の形は report/README.md。判断ログ（decisions/ と各案件の .autopilot/decisions/）も集めて載せる。
"""
import json
import re
from datetime import datetime, timedelta
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "data" / "reports"
TEMPLATE = ROOT / "report" / "template.html"
OUT = ROOT / "report" / "index.html"


PROJECTS = Path("C:/Users/<ユーザー名>/Claude")
FIELD = re.compile(r"^- \*\*(.+?)\*\*[:：]\s*(.*)$")


def parse_decisions(path, folder):
    """判断ログの Markdown を、見出し（### ）ごとの項目に分ける。"""
    date = path.stem
    items, cur, key = [], None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            cur = {"folder": folder, "date": date, "title": line[4:].strip(), "fields": {}}
            items.append(cur)
            key = None
            continue
        if cur is None:
            continue
        m = FIELD.match(line)
        if m:
            key = m.group(1)
            cur["fields"][key] = m.group(2).strip()
        elif key and line.strip():
            cur["fields"][key] += "\n" + line.strip()
    for i, it in enumerate(items):
        it["id"] = f"{folder}/{date}/{i + 1}"
        grade = it["fields"].get("本人の採点", "")
        it["graded"] = bool(grade) and not grade.startswith("（未）")
    return items


def collect_decisions():
    found = []
    for p in sorted((ROOT / "decisions").glob("????-??-??.md")):
        found += parse_decisions(p, "usage-autopilot")
    for p in sorted(PROJECTS.glob("*/.autopilot/decisions/????-??-??.md")):
        found += parse_decisions(p, p.parent.parent.parent.name)
    found.sort(key=lambda d: (d["date"], d["id"]), reverse=True)
    return found


def embed(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def rhythm_check(r):
    """その枠が「動く時間」だったか（申告の空き時間の割合）と、本人が使いそうだと見た量を出す。

    実際に本人が使った量（usage.user_pct）と並べて、申告・学んだ使い方と実際のずれを見るため。
    学んだ使い方は今の記録から出すので、過去の枠では「今の見立てで見たら」の値になる。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import schedule
    rhythm = json.loads(schedule.RHYTHM.read_text(encoding="utf-8")) if schedule.RHYTHM.exists() else {"free": []}
    learned = schedule.learned_user_pct_per_hour()
    start = datetime.fromisoformat(r["window_start"])
    end = datetime.fromisoformat(r["window_end"])
    free_steps = steps = 0
    expected = 0.0
    t = start
    while t < end:
        loc = t.astimezone(schedule.JST)
        free = schedule.is_declared_free(loc, rhythm)
        steps += 1
        free_steps += free
        if not free:
            expected += learned.get((loc.weekday() < 5, loc.hour), schedule.UNKNOWN_USER_PCT_PER_H) / 6
        t += timedelta(minutes=10)
    share = free_steps / steps if steps else 0.0
    return {"declared_free_share": round(share, 2), "mode": "run" if share >= 0.5 else "rest",
            "user_pct_expected": round(expected, 1)}


def attach_usage_split(reports):
    """使用量の履歴を更新し、各回の5時間枠に「本人」と「自律実行」の推定内訳を差し込む。

    報告の JSON（指揮役が書く）には手を入れず、ページを組み立てるときだけ足す。
    報告に既に内訳が書かれていれば、そちらを優先する。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from usage_history import refresh
    _, _, window_list = refresh()
    windows = {w["id"]: w for w in window_list}
    for r in reports:
        w = windows.get(r["id"])
        u = r.setdefault("usage", {})
        a = r.setdefault("autopilot", {})
        if "resumes" not in a:
            # キャッシュが切れた状態から再開した1往復の回数と推定消費（tools/resume_cost.py）
            from resume_cost import cold_resumes
            a["resumes"] = cold_resumes(datetime.fromisoformat(r["window_start"]), datetime.fromisoformat(r["window_end"]))
        if "rhythm" not in a:
            a["rhythm"] = rhythm_check(r)
        if not w:
            continue
        if u.get("user_pct") is None:
            # 自律実行は会話記録からの換算、本人は報告に書かれた使用率との差
            # （使用率スキャンが枠の途中で止まると、履歴側の最後の使用率は実際より小さいため、報告の値を使う）
            used = u.get("five_hour_used", w["five_hour_used"])
            u["autopilot_pct"] = round(min(used, w["autopilot_est_pct"]), 1)
            u["user_pct"] = round(used - u["autopilot_pct"], 1)
        u.setdefault("buffer_check", {
            "provisional_pct": w["provisional_buffer_pct"],
            "forecast_pct": w["forecast_buffer_pct"],
            "forecast_basis_days": w["forecast_basis_days"],
            "user_pct": u["user_pct"],
        })


def main():
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in REPORTS.glob("*.json")]
    reports.sort(key=lambda r: r["window_start"], reverse=True)
    attach_usage_split(reports)
    decisions = collect_decisions()
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*REPORTS_JSON*/", embed(reports))
            .replace("/*DECISIONS_JSON*/", embed(decisions)))
    OUT.write_text(html, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    pending = sum(1 for d in decisions if not d["graded"])
    print(f"report/index.html を作成（報告 {len(reports)} 件、判断ログ {len(decisions)} 件うち未採点 {pending} 件）")


if __name__ == "__main__":
    main()
