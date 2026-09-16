// ブラウザペインで https://claude.ai を開いた状態で javascript_tool に渡す。
// 返り値を tools/record_usage.py に標準入力で渡すと data/usage_snapshots.jsonl に追記される。
const orgs = await fetch('/api/organizations').then(r => r.json());
const u = await fetch(`/api/organizations/${orgs[0].uuid}/usage`).then(r => r.json());
JSON.stringify({
  plan: (orgs[0].capabilities || []).join(','),
  five_hour: u.five_hour && { utilization: u.five_hour.utilization, resets_at: u.five_hour.resets_at },
  seven_day: u.seven_day && { utilization: u.seven_day.utilization, resets_at: u.seven_day.resets_at },
  seven_day_opus: u.seven_day_opus,
  seven_day_sonnet: u.seven_day_sonnet,
  extra_usage_enabled: !!(u.extra_usage && u.extra_usage.is_enabled),
})
