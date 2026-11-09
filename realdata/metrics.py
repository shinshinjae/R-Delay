"""평가 지표(PROTOCOL v1 7절): PR-AUC·ROC-AUC·Brier·기술점수·신뢰도, 경보 기준, 사건 단위(T3), 주 단위 블록 부트스트랩."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

LEAD_WINDOW_H = 72   # 에피소드 시작 전 이 시간 안의 첫 경보로 선행시간을 잰다
FA_WINDOW_H = 48     # 경보 시작 뒤 이 시간 안에 초과 선박 도착이 없으면 오경보
EPISODE_GAP_H = 48   # 초과 선박 도착이 이 시간 안에 이어지면 한 에피소드


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def scores(p, y, p_ref=None) -> dict:
    """확률 점수 p와 0/1 정답 y. p_ref(보통 S0)가 있으면 Brier 기술점수."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    out = {"n": int(len(y)), "pos_rate": round(float(y.mean()), 4),
           "pr_auc": round(float(average_precision_score(y, p)), 4) if y.any() else None,
           "roc_auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.mean() < 1 else None,
           "brier": round(brier(p, y), 4)}
    if p_ref is not None:
        b0 = brier(p_ref, y)
        out["bss_vs_s0"] = None if b0 <= 1e-12 else round(1 - brier(p, y) / b0, 4)
    return out


def reliability(p, y, bins: int = 10) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, int)
    edges = np.linspace(0, 1, bins + 1)
    k = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        m = k == b
        if m.any():
            out.append({"bin": [round(edges[b], 2), round(edges[b + 1], 2)], "n": int(m.sum()),
                        "mean_p": round(float(p[m].mean()), 4), "obs": round(float(y[m].mean()), 4)})
    return out


def thresholds(p_val, y_val, train_rate: float) -> dict:
    """검증 기간에서 경보 기준: (a) F1 최대, (b) 경보 비율 = 학습 기저율."""
    p, y = np.asarray(p_val, float), np.asarray(y_val, int)
    cand = np.unique(p)  # 모든 점수값을 기준 후보로(전수 탐색)
    best, best_f1 = cand[0], -1.0
    for c in cand:
        a = p >= c
        tp = float((a & (y == 1)).sum())
        prec = tp / max(a.sum(), 1)
        rec = tp / max(y.sum(), 1)
        f1 = 0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
        if f1 > best_f1:
            best, best_f1 = c, f1
    rate_thr = float(np.quantile(p, 1 - train_rate))
    # (c) 경보 비율 10%: 배차 담당자가 실제로 대응할 수 있는 경보량(PROTOCOL v1.1)
    return {"f1_max": float(best), "rate_matched": rate_thr, "rate_10": float(np.quantile(p, 0.9))}


def at_threshold(p, y, thr: float) -> dict:
    p, y = np.asarray(p, float), np.asarray(y, int)
    a = p >= thr
    tp = int((a & (y == 1)).sum())
    return {"threshold": round(float(thr), 4), "alarm_rate": round(float(a.mean()), 4),
            "precision": round(tp / max(int(a.sum()), 1), 4), "recall": round(tp / max(int(y.sum()), 1), 4)}


def episodes(arrivals: pd.Series, gap_h: int = EPISODE_GAP_H) -> pd.DataFrame:
    a = pd.Series(pd.to_datetime(arrivals)).sort_values().reset_index(drop=True)
    if a.empty:
        return pd.DataFrame(columns=["start", "end", "ships"])
    grp = (a.diff() > pd.Timedelta(hours=gap_h)).cumsum()
    return a.groupby(grp).agg(["min", "max", "size"]).rename(columns={"min": "start", "max": "end", "size": "ships"}).reset_index(drop=True)


def event_metrics(score: pd.Series, thr: float, arrivals: pd.Series, t0, t1, return_leads: bool = False) -> dict:
    """T3: 기간 [t0, t1)에 시작한 에피소드의 선행시간·놓침, 그 기간 경보 시작의 오경보.
    score는 2시간 격자 전체(기간 밖 포함)의 점수. 경보 = score ≥ thr.
    - 선행시간·놓침: 에피소드 시작 전 72시간 [start−72h, start)의 첫 경보(τ = start의 경보는 (τ, τ+24h]라 그 시작을 예측하지 못함)
    - 덮는 경보 놓침(보조): 자기 창 (τ, τ+24h]가 시작을 덮는 경보, 즉 [start−24h, start)에 경보가 없으면 놓침
    - 오경보: 경보 시작 뒤 48시간 안에 초과 선박 도착이 없음. 48시간 창이 기간 끝을 넘는 경보 시작은 세지 않는다.
      보조: 직전 48시간 안에 초과 선박이 왔던 경보 시작(막 끝난 에피소드의 뒤)을 빼고 센 오경보율
    return_leads: 잡은 에피소드의 선행시간 목록도 돌려준다(그림용, 다른 값은 같다)"""
    s = score.sort_index()
    alarm = s >= thr
    eps = episodes(arrivals)
    eps = eps[(eps["start"] >= t0) & (eps["start"] < t1)]
    leads, missed, missed_cover = [], 0, 0
    at = alarm[alarm].index
    for st in eps["start"]:
        w = at[(at >= st - pd.Timedelta(hours=LEAD_WINDOW_H)) & (at < st)]
        if len(w):
            leads.append((st - w[0]) / pd.Timedelta(hours=1))
        else:
            missed += 1
        if not ((at >= st - pd.Timedelta(hours=24)) & (at < st)).any():
            missed_cover += 1
    onset = alarm & ~alarm.shift(1, fill_value=False)
    fa_end = pd.Timestamp(t1) - pd.Timedelta(hours=FA_WINDOW_H)
    on = onset[(onset.index >= t0) & (onset.index < fa_end) & onset].index
    arr = np.sort(pd.to_datetime(arrivals).to_numpy(dtype="datetime64[ns]"))
    arr = arr[arr < np.datetime64(pd.Timestamp(t1))]  # 기간 끝 뒤의 도착은 보지 않는다
    g = on.to_numpy(dtype="datetime64[ns]")
    nxt = np.searchsorted(arr, g, side="right")
    has = (nxt < len(arr)) & (arr[np.minimum(nxt, len(arr) - 1)] <= g + np.timedelta64(FA_WINDOW_H, "h"))
    prev = nxt - 1
    recent = (prev >= 0) & (arr[np.maximum(prev, 0)] > g - np.timedelta64(FA_WINDOW_H, "h"))
    fa = int((~has).sum())
    fa_ex = int((~has & ~recent).sum())
    q = (lambda v: None) if not leads else (lambda v: round(float(np.quantile(leads, v)), 1))
    extra = {"leads_h": [round(float(v), 1) for v in leads]} if return_leads else {}
    return {**extra, "episodes": int(len(eps)), "missed": missed, "missed_rate": round(missed / max(len(eps), 1), 3),
            "missed_covering": missed_cover, "missed_covering_rate": round(missed_cover / max(len(eps), 1), 3),
            "lead_h_median": q(0.5), "lead_h_q25": q(0.25), "lead_h_q75": q(0.75),
            "alarm_onsets": int(len(on)), "false_alarms": fa, "false_alarm_rate": round(fa / max(len(on), 1), 3),
            "false_alarm_rate_excl_after_episode": round(fa_ex / max(int((~recent).sum()), 1), 3)}


def block_bootstrap(df: pd.DataFrame, fn, reps: int = 1000, seed: int = 20261108, block: str = "W") -> dict:
    """주 단위 블록을 다시 뽑아 fn(df_boot) → dict의 95% 구간."""
    rng = np.random.default_rng(seed)
    keys = df.index.to_period(block)
    blocks = [df[keys == k] for k in keys.unique()]
    vals: dict[str, list] = {}
    for _ in range(reps):
        pick = rng.integers(0, len(blocks), len(blocks))
        r = fn(pd.concat([blocks[i] for i in pick]))
        for k, v in r.items():
            if v is not None:
                vals.setdefault(k, []).append(v)
    return {k: [round(float(np.quantile(v, 0.025)), 4), round(float(np.quantile(v, 0.975)), 4)] for k, v in vals.items()}
