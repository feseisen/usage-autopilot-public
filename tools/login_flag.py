"""claude.ai のログインが切れていることを、デスクトップの印のファイルで見えるようにする。

    python tools/login_flag.py set    # 切れている間だけ、デスクトップに印を置く
    python tools/login_flag.py clear  # 取れたら消す

通知（PushNotification）は届かないことがあり、届いても本人が席にいるとは限らない。
本人が画面を見たときに「ああ切れてるな」と分かればよい、という決まりなので、
デスクトップに置いたファイル名だけで分かるようにしている（2026-09-18 本人）。

どこで失敗しても、スキャンの回を止めないために 0 で終わる。
状態は data/login_state.json にも残るので、報告書からも切れていた時間をたどれる。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "login_state.json"
FLAG_NAME = "⚠ claude.ai のログインが切れています.txt"
BODY = """claude.ai のログインが切れています。

ブラウザペイン（Claude アプリの右側）に claude.ai のログイン画面を開いてあります。
そこでログインし直すと、30分ごとの使用率スキャンがひとりでに戻ります。
このファイルは、ログインが戻ったスキャンの回が消します。

切れたのに気づいた時刻: {at}
"""


def desktop() -> Path | None:
    """デスクトップのフォルダ。OneDrive に移されている場合もあるのでレジストリを先に見る。"""
    try:
        import winreg  # Windows 以外には無い
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            p = Path(winreg.QueryValueEx(k, "Desktop")[0])
            if p.is_dir():
                return p
    except Exception:
        pass
    for p in (Path.home() / "Desktop", Path.home() / "デスクトップ"):
        if p.is_dir():
            return p
    return None


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(d: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in ("set", "clear"):
        sys.exit("使い方: python tools/login_flag.py set|clear")

    now = datetime.now(timezone.utc)
    state, d = read_state(), desktop()
    flag = d / FLAG_NAME if d else None

    if action == "set":
        if state.get("expired_since"):
            print("すでに印がある（切れたのは", state["expired_since"], "）")
            return
        write_state({"expired_since": now.isoformat(), "flag_path": str(flag) if flag else None})
        if flag:
            try:
                flag.write_text(BODY.format(at=now.astimezone().strftime("%m/%d %H:%M")), encoding="utf-8")
                print("デスクトップに印を置いた:", flag)
            except OSError as e:
                print("印を置けなかった（記録だけ残す）:", e)
        else:
            print("デスクトップのフォルダが見つからない（記録だけ残す）")
        return

    # clear
    old = state.get("expired_since")
    for p in (flag, Path(state["flag_path"]) if state.get("flag_path") else None):
        if p:
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                print("印を消せなかった:", e)
    if old:
        write_state({"expired_since": None, "last_expired": {"from": old, "to": now.isoformat()}})
        print("印を消した（切れていたのは", old, "から）")


if __name__ == "__main__":
    main()
