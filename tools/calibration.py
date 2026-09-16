"""会話記録のトークン量を「5時間枠の何%か」に換算する係数を、公式の使用率から当てはめる。

■ 分かったこと（2026-09-13、Opus だけが動いた9区間）
    5時間枠% ≈ 24 × キャッシュ書き込み(100万トークン) + 187 × 出力(100万トークン)
キャッシュの読み込みはほぼ枠を使わない（係数 0.02）。誤差の二乗平均は 0.34%。
長い会話を読み直すだけなら軽く、会話を作り直す（キャッシュを書き直す）と重い。
1時間以上止めた会話の最初の1往復が重いのは、キャッシュが切れて書き直しになるため。

■ 当てはめ方
- 公式の使用率の記録が続けて2つあり、同じ5時間枠の中にある区間を使う
- 係数（キャッシュ読み込み・キャッシュ書き込み・出力）は、Opus だけが動いた区間から負にならない最小二乗で出す
- Sonnet の係数（Opus を1としたとき）は、Sonnet が区間の3割以上を占める区間が MIN_SONNET_SAMPLES 以上
  溜まったら出す。それまでは 1.0（Opus と同じ）とする
- 結果は data/calibration.json に保存する。区間が MIN_SAMPLES に満たないときは既定値を使う

使い方:
    python tools/calibration.py      # 当てはめ直して保存し、要約を表示
"""
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "calibration.json"
SNAPSHOTS = ROOT / "data" / "usage_snapshots.jsonl"

TOKEN_KEYS = ("cache_read", "cache_write", "output")
DEFAULT = {"coef": {"cache_read": 0.02, "cache_write": 24.22, "output": 187.56, "input": 0.0},
           "family_factor": {"opus": 1.0, "sonnet": 1.0, "haiku": 1.0}}
MIN_SAMPLES = 5
MIN_SONNET_SAMPLES = 5
MIN_RATIO_WEEKLY_DELTA = 5   # r は、7日枠がこれ以上動いた記録から出す（1%刻みの丸めを抑えるため）
MIN_UPTIME_HOURS = 72        # 稼働率は、これだけ観測してから配分に使う


def family(model):
    for f in ("opus", "sonnet", "haiku"):
        if f in (model or ""):
            return f
    return None


def load():
    """保存済みの係数。無ければ既定値。"""
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {**DEFAULT, "source": "既定値"}


def estimate_pct(tokens, fam, calib):
    """1件または合計のトークン量（100万単位ではなく個数）を、5時間枠の%に換算する。"""
    c = calib["coef"]
    base = sum(tokens.get(k, 0) / 1e6 * c.get(k, 0.0) for k in ("cache_read", "cache_write", "output", "input"))
    return base * calib["family_factor"].get(fam, 1.0)


def _nnls(X, Y):
    """列が少ないので、列の組み合わせを総当たりして負の係数が出ない最良の最小二乗を選ぶ。"""
    best = None
    n = X.shape[1]
    for k in range(1, n + 1):
        for cols in itertools.combinations(range(n), k):
            c = np.linalg.lstsq(X[:, cols], Y, rcond=None)[0]
            if (c < 0).any():
                continue
            full = np.zeros(n)
            full[list(cols)] = c
            r = float(((X @ full - Y) ** 2).sum())
            if best is None or r < best[1]:
                best = (full, r)
    return best[0]


def intervals(requests):
    """同じ5時間枠の中で続けて取れた使用率の区間ごとに、モデル別のトークン合計と公式の増え方を返す。"""
    snaps = [json.loads(x) for x in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = []
    reqs = sorted(requests, key=lambda r: r["ts"])
    for a, b in zip(snaps, snaps[1:]):
        ra = datetime.fromisoformat(a["five_hour"]["resets_at"])
        rb = datetime.fromisoformat(b["five_hour"]["resets_at"])
        if abs((ra - rb).total_seconds()) > 120:
            continue
        ta, tb = datetime.fromisoformat(a["captured_at"]), datetime.fromisoformat(b["captured_at"])
        sums = {f: dict.fromkeys(TOKEN_KEYS, 0) for f in ("opus", "sonnet", "haiku")}
        for r in reqs:
            if ta <= r["ts"] < tb and r["family"] in sums:
                for k in TOKEN_KEYS:
                    sums[r["family"]][k] += r[k]
        out.append({"start": ta, "end": tb, "delta": b["five_hour"]["utilization"] - a["five_hour"]["utilization"], "sums": sums})
    return out


def _load_snaps():
    snaps = [json.loads(x) for x in SNAPSHOTS.read_text(encoding="utf-8").splitlines() if x.strip()]
    for s in snaps:
        s["_t"] = datetime.fromisoformat(s["captured_at"])
    return sorted(snaps, key=lambda s: s["_t"])


def _same_reset(a, b, key):
    ra, rb = (datetime.fromisoformat(x[key]["resets_at"]) for x in (a, b))
    return abs((ra - rb).total_seconds()) <= 120


def fit_ratio(requests, calib):
    """7日枠1%あたりの5時間枠%（r）を出す。

    同じ7日枠の中で続けて取れた使用率の区間を順につなげ、5時間枠でどれだけ使ったかの合計を
    7日枠の増え方で割る。区間が同じ5時間枠の中なら公式の増え方を、5時間枠をまたぐなら
    会話記録から換算した量を使う。7日枠は1%刻みなので、長くつなげるほど丸めの誤差が小さくなる。
    """
    snaps = _load_snaps()
    reqs = sorted(requests, key=lambda r: r["ts"])
    best = None
    run = None  # 同じ7日枠の中で続いている区間のまとまり
    for a, b in zip(snaps, snaps[1:]):
        if not _same_reset(a, b, "seven_day"):
            run = None
            continue
        if _same_reset(a, b, "five_hour"):
            used5 = b["five_hour"]["utilization"] - a["five_hour"]["utilization"]
            basis = "公式"
        else:
            used5 = sum(estimate_pct(r, r["family"], calib) for r in reqs if a["_t"] <= r["ts"] < b["_t"])
            basis = "換算"
        if run is None:
            run = {"start": a, "five_hour_sum": 0.0, "official": 0.0, "estimated": 0.0}
        run["five_hour_sum"] += used5
        run["official" if basis == "公式" else "estimated"] += used5
        weekly = b["seven_day"]["utilization"] - run["start"]["seven_day"]["utilization"]
        if best is None or weekly > best["weekly_delta"]:
            best = {"value": round(run["five_hour_sum"] / weekly, 2) if weekly > 0 else None,
                    "weekly_delta": weekly, "five_hour_sum": round(run["five_hour_sum"], 1),
                    "from_official": round(run["official"], 1), "from_estimate": round(run["estimated"], 1),
                    "since": run["start"]["captured_at"], "until": b["captured_at"]}
    if best is None or best["weekly_delta"] < MIN_RATIO_WEEKLY_DELTA:
        return {"value": None, "note": f"7日枠が {MIN_RATIO_WEEKLY_DELTA}% 以上動いた記録がない", **(best or {})}
    return best


def fit_uptime(requests):
    """アプリが開いていた時間の割合。

    使用率の記録（定期タスクはアプリが開いているときだけ動く）か、会話記録の応答がある1時間を
    「開いていた」と数える。観測の始まりは最初の使用率の記録。MIN_UPTIME_HOURS に満たないうちは
    tools/budget.py は使わない。
    """
    snaps = _load_snaps()
    if not snaps:
        return {"value": None, "observed_hours": 0}
    start = snaps[0]["_t"].replace(minute=0, second=0, microsecond=0)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = int((now - start).total_seconds() // 3600) + 1
    open_hours = set()
    for s in snaps:
        open_hours.add(s["_t"].replace(minute=0, second=0, microsecond=0))
    for r in requests:
        if r["ts"] >= start:
            open_hours.add(r["ts"].replace(minute=0, second=0, microsecond=0))
    return {"value": round(len(open_hours) / hours, 3) if hours else None,
            "observed_hours": hours, "open_hours": len(open_hours),
            "since": start.isoformat(), "min_hours_to_use": MIN_UPTIME_HOURS}


def fit(requests):
    ivs = intervals(requests)
    opus_only = [iv for iv in ivs if not any(iv["sums"][f][k] for f in ("sonnet", "haiku") for k in TOKEN_KEYS)
                 and any(iv["sums"]["opus"][k] for k in TOKEN_KEYS)]
    result = json.loads(json.dumps(DEFAULT))
    result["fitted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if len(opus_only) >= MIN_SAMPLES:
        X = np.array([[iv["sums"]["opus"][k] / 1e6 for k in TOKEN_KEYS] for iv in opus_only])
        Y = np.array([iv["delta"] for iv in opus_only], float)
        c = _nnls(X, Y)
        result["coef"].update({k: round(float(v), 3) for k, v in zip(TOKEN_KEYS, c)})
        result["opus_samples"] = len(opus_only)
        result["opus_rmse_pct"] = round(float(np.sqrt(((X @ c - Y) ** 2).mean())), 2)
        result["source"] = "当てはめ"
    else:
        result["opus_samples"] = len(opus_only)
        result["source"] = "既定値（区間不足）"

    # Sonnet: Opus 分を差し引いた残りと、Sonnet のトークンを Opus の係数で換算した量の比
    num = den = 0.0
    sonnet_samples = 0
    for iv in ivs:
        s_base = estimate_pct(iv["sums"]["sonnet"], "opus", {**result, "family_factor": {"opus": 1.0}})
        o_base = estimate_pct(iv["sums"]["opus"], "opus", {**result, "family_factor": {"opus": 1.0}})
        if s_base <= 0 or s_base < 0.3 * (s_base + o_base):
            continue
        sonnet_samples += 1
        num += (iv["delta"] - o_base) * s_base
        den += s_base * s_base
    result["sonnet_samples"] = sonnet_samples
    if sonnet_samples >= MIN_SONNET_SAMPLES and den > 0:
        result["family_factor"]["sonnet"] = round(max(0.1, num / den), 2)
    result["ratio"] = fit_ratio(requests, result)
    result["uptime"] = fit_uptime(requests)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from usage_history import load_requests
    r = fit(load_requests())
    sys.stdout.reconfigure(encoding="utf-8")
    c = r["coef"]
    print(f"係数（{r['source']}、Opus だけの区間 {r['opus_samples']} 件、誤差 {r.get('opus_rmse_pct', '—')}%）")
    print(f"  5時間枠% = {c['cache_read']} × キャッシュ読み込み + {c['cache_write']} × キャッシュ書き込み + {c['output']} × 出力（いずれも100万トークンあたり）")
    print(f"  Sonnet の係数: {r['family_factor']['sonnet']}（Sonnet が主の区間 {r['sonnet_samples']} 件。{MIN_SONNET_SAMPLES} 件未満なら 1.0 のまま）")
    q = r["ratio"]
    print(f"r（7日枠1%あたりの5時間枠%）: {q.get('value')}（7日枠 {q.get('weekly_delta')}% に対し5時間枠の合計 {q.get('five_hour_sum')}%。"
          f"うち公式 {q.get('from_official')}% / 換算 {q.get('from_estimate')}%）")
    u = r["uptime"]
    print(f"稼働率: {u.get('value')}（観測 {u.get('observed_hours')} 時間のうち {u.get('open_hours')} 時間。{MIN_UPTIME_HOURS} 時間溜まるまで配分には使わない）")


if __name__ == "__main__":
    main()
