# 使用率スキャン（定期タスク用）

決まった手順をそのとおりに実行するだけの作業。判断はせず、本人に質問しない。出力は日本語で1行。
**最後に開いたタブを閉じて、すぐに終わる。**開いたままにすると実行中の扱いのまま残り、次の回が始まらなくなる（2026-09-13 に約6時間止まった）。

1. `mcp__Claude_Browser__tabs_create` で新しいタブを開き、そのタブで `mcp__Claude_Browser__navigate` を使って `https://claude.ai` を開く（`preview_start` は使わない）
2. `C:\Users\<ユーザー名>\Claude\usage-autopilot\tools\fetch_usage.js` の中身をそのまま、そのタブで `mcp__Claude_Browser__javascript_tool` で実行する
3. 返ってきた文字列をそのまま `C:\Users\<ユーザー名>\Claude\usage-autopilot\data\usage_latest.json` に Write で保存する
4. Bash で `python C:/Users/<ユーザー名>/Claude/usage-autopilot/tools/record_usage.py < C:/Users/<ユーザー名>/Claude/usage-autopilot/data/usage_latest.json` を実行する
5. 1で開いたタブを `mcp__Claude_Browser__tabs_close` で閉じる
6. 「5時間枠 N% / 7日枠 M%」と1行だけ書いて終わる

- ログイン画面が出た、または取得に失敗した → タブを閉じ、PushNotification で「claude.ai のログインが切れていて使用率を取れない。ブラウザペインでログインしてほしい」と送って終わる
- コミット・push・他のファイルの編集はしない
