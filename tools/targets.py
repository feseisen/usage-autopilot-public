"""アプリのセッション一覧から、自律実行で動かすセッションを選ぶ。

入力: 指揮役が list_sessions で得た JSON 配列（標準入力、またはファイル）
出力: data/targets.json（フォルダごとに1セッション）

規則（docs/design.md 2章）:
- C:\\Users\\<ユーザー名>\\Claude\\ 直下のフォルダごとに、アーカイブされていない最新のセッションを1つ
- 定期実行が作ったセッションは除く（題名が【で始まる、または作業フォルダが親フォルダそのもの）
- targets.md の「外すフォルダ」は除き、「使うセッション」があればそれを使う
"""
import json
import re
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
PARENT = PureWindowsPath(r"C:\Users\<ユーザー名>\Claude")
OUT = ROOT / "data" / "targets.json"


def read_targets_md():
    """targets.md の「外すフォルダ」と「使うセッション」を読む。"""
    skip, pins, section = set(), {}, None
    for line in (ROOT / "targets.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section == "使うセッション":
            m = re.match(r"^- (\S+) = (\S+)", line)
            if m:
                pins[m.group(1)] = m.group(2)
        elif section == "外すフォルダ":
            m = re.match(r"^- ([^\s—]+)", line)
            if m:
                skip.add(m.group(1))
    return skip, pins


def main():
    src = sys.stdin.read() if len(sys.argv) < 2 else Path(sys.argv[1]).read_text(encoding="utf-8")
    sessions = json.loads(src)
    skip, pins = read_targets_md()
    orch_path = ROOT / "data" / "orchestrator.json"
    orchestrator_id = json.loads(orch_path.read_text(encoding="utf-8")).get("session_id") if orch_path.exists() else None
    sessions = [s for s in sessions if s.get("sessionId") != orchestrator_id]  # 指揮役自身には指示を出さない
    chosen = {}
    for s in sessions:  # list_sessions は新しい順
        if s.get("isArchived"):
            continue
        cwd = PureWindowsPath(s.get("cwd", ""))
        if cwd.parent != PARENT:
            continue
        folder = cwd.name
        if folder in skip or s.get("title", "").startswith("【"):
            continue
        if folder in pins and s["sessionId"] != pins[folder]:
            continue
        if folder not in chosen:
            chosen[folder] = {
                "folder": folder,
                "session_id": s["sessionId"],
                "title": s.get("title"),
                "is_running": bool(s.get("isRunning")),
                "last_activity_at": s.get("lastActivityAt"),
            }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(list(chosen.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for t in chosen.values():
        print(f"{t['folder']}: 「{t['title']}」{'（作業中）' if t['is_running'] else ''}")


if __name__ == "__main__":
    main()
