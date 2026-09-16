# 報告書

指揮役が5時間枠ごとに書く報告。`data/reports/<id>.json` に1枠1ファイルで置き、`python tools/build_report.py` で `report/index.html` にまとめて、同じアーティファクトに上書きで公開する（URL は `data/report_artifact.json`）。

## 1件の形

```json
{
  "id": "2026-09-13_1100",
  "window_start": "2026-09-13T06:00:00+09:00",
  "window_end": "2026-09-13T11:00:00+09:00",
  "written_at": "2026-09-13T11:05:00+09:00",
  "headline": "この枠を一文で",
  "summary": ["1分で読める要点を2〜3行"],
  "usage": {
    "five_hour_used": 78, "five_hour_ceiling": 80, "five_hour_buffer": 20,
    "seven_day_start": 6, "seven_day_end": 14, "seven_day_buffer": 5, "seven_day_resets": "09/13 19:00"
  },
  "projects": [
    {
      "folder": "<案件C>", "title": "セッションの題名",
      "state": "進行 | 評価待ち | 待機 | 停止",
      "did": ["やったこと（案件の .autopilot/report.md から）"],
      "estimate_pct": 35, "actual_pct": 28.4,
      "commits": ["487a495 コミットの一行目"],
      "artifacts": [{"label": "カタログ画面 v2", "url": "https://..."}],
      "decisions_added": 2
    }
  ],
  "needs_user": [{"folder": "…", "what": "本人に見てほしいこと", "link": "任意"}],
  "autopilot": {
    "stops": ["止めた・区切りを頼んだ出来事と理由"],
    "estimate_notes": ["見積もりと実績のずれの傾向"],
    "orchestrator_pct": 4.1,
    "issues": ["自律実行の仕組みについての気づき・問題"]
  }
}
```

- 実績（`actual_pct`・`orchestrator_pct`）は `tools/actuals.py` の推定値
- 数字が取れないものは `null`
