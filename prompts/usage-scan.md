# 使用率スキャン（定期タスク用）

決まった手順をそのとおりに実行するだけの作業。判断はせず、本人に質問しない。出力は日本語で1行。**途中の書き出し（「次はこれをする」といった独り言）も日本語にする。**
**取得できた回は、最後にタブを閉じてすぐに終わる。**開いたままにすると実行中の扱いのまま残り、次の回が始まらなくなる（2026-09-13 に約6時間止まった）。
**ログインが要る回だけは、ログイン画面のタブを閉じずに残す。**PC を再起動するとログインが切れるので、本人がブラウザペインを開いたらそのままログインできる状態にしておく（2026-09-17 本人）。

1. `mcp__Claude_Browser__tabs_context` でタブを確かめる。
   - `claude.ai` のタブがすでにある（前の回が残したログイン画面）→ **そのタブをそのまま使い、`navigate` はしない。**本人がログインを入力している最中かもしれないため。2の `javascript_tool` は同じ `claude.ai` の中で動くので、ログインが済んでいればそのまま通る
   - `claude.ai` のタブがない → `mcp__Claude_Browser__tabs_create` で新しいタブを開き、そのタブで `mcp__Claude_Browser__navigate` を使って `https://claude.ai` を開く（`preview_start` は使わない）
2. `C:\Users\<ユーザー名>\Claude\usage-autopilot\tools\fetch_usage.js` の中身をそのまま、そのタブで `mcp__Claude_Browser__javascript_tool` で実行する
3. 返ってきた文字列をそのまま `C:\Users\<ユーザー名>\Claude\usage-autopilot\data\usage_latest.json` に Write で保存する
4. Bash で `python C:/Users/<ユーザー名>/Claude/usage-autopilot/tools/record_usage.py < C:/Users/<ユーザー名>/Claude/usage-autopilot/data/usage_latest.json` を実行する
5. 1で使ったタブを `mcp__Claude_Browser__tabs_close` で閉じる
6. Bash で `python C:/Users/<ユーザー名>/Claude/usage-autopilot/tools/login_flag.py clear` を実行する（ログイン切れの印が残っていれば消える。無ければ何もしない）
7. 「5時間枠 N% / 7日枠 M%」と1行だけ書いて終わる

## ログインが要るとき

URL がログイン画面（`/login` など）に移った、ページにログインのボタンや入力欄が出ている、または2の実行が失敗した（`orgs[0]` が読めない、403 など）ときは、3〜7をせずに次をする。**この回で新しくタブを開いたか、前の回が残したタブを使ったかで分かれる。**

### この回で新しくタブを開いた場合（切れたのに気づいたのが今回）

1. そのタブで `mcp__Claude_Browser__navigate` を使って `https://claude.ai/login` を開く
2. `mcp__Claude_Browser__tabs_select` でそのタブを前に出す。**タブは閉じない**
3. Bash で `python C:/Users/<ユーザー名>/Claude/usage-autopilot/tools/login_flag.py set` を実行する（デスクトップに印のファイルが出る。失敗しても続ける）
4. PushNotification で「claude.ai のログインが切れています。ブラウザペインにログイン画面を開いてあるので、ログインしてください」と送る（送れなくても続ける）
5. 同じ文を1行書いて終わる

### 前の回が残したタブを使った場合（前の回ですでに知らせている）

- `navigate`・`tabs_select`・`login_flag.py`・PushNotification のどれもしない。タブはそのまま残す
  - 本人が入力している最中のログイン画面を開き直すと、入力が消えるため
  - 同じ知らせを30分ごとに送らないため（切れてからの1回目だけ送る）
- 「claude.ai のログインが切れたままです」と1行書いて終わる

次の回は、1で残ったタブを使い回す。ログイン画面のタブが回ごとに増えることはない。

- メールアドレスやパスワードの入力、ログインのボタンを押す操作はしない（本人がする）
- コミット・push・他のファイルの編集はしない
