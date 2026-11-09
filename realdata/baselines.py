"""단계 2 기준선 S0~S2(S2b)를 학습 기간에 맞추고 검증 기간에서 고른다. 시험 기간은 계산하지 않는다(PROTOCOL v1).

실행: python -m realdata.baselines
결과: realdata/results/baselines.json(학습·검증 지표, 고른 설정), realdata/cache/pred_baselines.parquet(전 격자 예측)
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import features as F
from . import metrics as M
from .io import load
from .paths import CACHE, RESULTS

PERIODS = {"train": ("2023-01-08", "2025-01-01"), "val": ("2025-01-01", "2025-07-01"), "test": ("2025-07-01", "2100-01-01")}
EMBARGO_H = 24


def period_masks(grid: pd.DatetimeIndex, data_tau_end: pd.Timestamp) -> dict:
    """적합·선택용 마스크(경계 24시간 제외)와 평가용 마스크."""
    out = {}
    for name, (a, b) in PERIODS.items():
        a, b = pd.Timestamp(a), min(pd.Timestamp(b), data_tau_end + pd.Timedelta(hours=2))
        out[name] = (grid >= a) & (grid < b)
        out[name + "_fit"] = out[name] & (grid + pd.Timedelta(hours=EMBARGO_H) < b)
    return out


def build_dataset():
    data = {n: load(n) for n in ("cntl", "asos", "buoy", "typhoon_track", "road", "voyages")}
    tau_end = F.data_tau_end(data["voyages"])  # 도착 자료 끝 − 7일(PROTOCOL 1절)
    grid = F.tau_grid("2023-01-01", tau_end)
    X = F.build_features(data, grid, voy=data["voyages"])
    y = F.targets(data["voyages"], grid)
    arr = F.qualifying_arrivals(data["voyages"])
    return X, y, arr, tau_end


# ---------- S0 기후값 ----------
def fit_s0(X, y, fit_mask, use_month=False):
    d = pd.DataFrame({"dow": X["dow"], "slot": X["slot"], "month": X["month"], "y": y})[fit_mask]
    base = d["y"].mean()
    tab = d.groupby(["dow", "slot"])["y"].agg(["sum", "size"])
    rate = (tab["sum"] + 1) / (tab["size"] + 2)
    mfac = None
    if use_month:
        m = d.groupby("month")["y"].agg(["sum", "size"])
        mfac = ((m["sum"] + 1) / (m["size"] + 2)) / base

    def predict(Xn):
        p = rate.reindex(pd.MultiIndex.from_arrays([Xn["dow"], Xn["slot"]])).to_numpy()
        p = np.where(np.isnan(p), base, p)
        if mfac is not None:
            p = np.clip(p * mfac.reindex(Xn["month"]).fillna(1.0).to_numpy(), 1e-4, 1 - 1e-4)
        return p
    return predict


# ---------- S1·S2 ----------
def lr_pipe(C: float):
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                         LogisticRegression(C=C, max_iter=2000))


def hgb(depth: int, iters: int):
    return HistGradientBoostingClassifier(max_depth=depth, learning_rate=0.05, max_iter=iters, min_samples_leaf=40,
                                          l2_regularization=1.0, random_state=20261108)


def main():
    t0 = time.time()
    X, y, arr, tau_end = build_dataset()
    grid = X.index
    m = period_masks(grid, tau_end)
    tr, va = m["train_fit"], m["val_fit"]
    train_rate = float(y[tr].mean())
    preds, chosen, cand = {}, {}, {}

    # S0: 월 효과는 검증 Brier가 나아질 때만
    s0 = fit_s0(X, y, tr, use_month=False)
    s0m = fit_s0(X, y, tr, use_month=True)
    b_plain, b_month = M.brier(s0(X[va]), y[va]), M.brier(s0m(X[va]), y[va])
    cand["S0"] = {"dow_slot": round(b_plain, 5), "dow_slot_month": round(b_month, 5)}
    use_month = b_month < b_plain
    preds["S0"] = (s0m if use_month else s0)(X)
    chosen["S0"] = {"month_effect": bool(use_month)}

    # S1: 2변수 로지스틱
    m1 = lr_pipe(1.0).fit(X.loc[tr, F.S1_FEATURES], y[tr])
    preds["S1"] = m1.predict_proba(X[F.S1_FEATURES])[:, 1]
    chosen["S1"] = {"features": F.S1_FEATURES, "C": 1.0}

    # S2: 로지스틱, 강도는 검증 PR-AUC로
    best = None
    cand["S2"] = {}
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        mdl = lr_pipe(C).fit(X.loc[tr, F.FEATURES], y[tr])
        ap = M.scores(mdl.predict_proba(X.loc[va, F.FEATURES])[:, 1], y[va])["pr_auc"]
        cand["S2"][str(C)] = ap
        if best is None or ap > best[0]:
            best = (ap, C, mdl)
    preds["S2"] = best[2].predict_proba(X[F.FEATURES])[:, 1]
    lr = best[2][-1]
    names = best[2][:-1].get_feature_names_out(F.FEATURES)
    chosen["S2"] = {"C": best[1], "coef_top": sorted(([n, round(float(c), 3)] for n, c in zip(names, lr.coef_[0])), key=lambda t: -abs(t[1]))[:15]}

    # S2b: gradient boosting
    best = None
    cand["S2b"] = {}
    for depth in (2, 3):
        for iters in (150, 300):
            mdl = hgb(depth, iters).fit(X.loc[tr, F.FEATURES], y[tr])
            ap = M.scores(mdl.predict_proba(X.loc[va, F.FEATURES])[:, 1], y[va])["pr_auc"]
            cand["S2b"][f"d{depth}_i{iters}"] = ap
            if best is None or ap > best[0]:
                best = (ap, (depth, iters), mdl)
    preds["S2b"] = best[2].predict_proba(X[F.FEATURES])[:, 1]
    chosen["S2b"] = {"max_depth": best[1][0], "max_iter": best[1][1]}

    # S3: 스케줄 기준선(PROTOCOL v1.4, 시험 전 등록), 강도는 검증 PR-AUC로
    best = None
    cand["S3"] = {}
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        mdl = lr_pipe(C).fit(X.loc[tr, F.S3_FEATURES], y[tr])
        ap = M.scores(mdl.predict_proba(X.loc[va, F.S3_FEATURES])[:, 1], y[va])["pr_auc"]
        cand["S3"][str(C)] = ap
        if best is None or ap > best[0]:
            best = (ap, C, mdl)
    preds["S3"] = best[2].predict_proba(X[F.S3_FEATURES])[:, 1]
    chosen["S3"] = {"C": best[1], "features": F.S3_FEATURES}

    # 지표(학습: 적합한 기간 그대로, 검증: 고르는 기간). 시험은 계산하지 않는다.
    res = {"protocol": "v1", "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "tau_end": str(tau_end),
           "target": "T1: (τ, τ+24h]에 신항 컨테이너선 도착 중 12h < 대기 ≤ 168h가 1척 이상",
           "periods": {k: [str(grid[v].min()), str(grid[v].max()), int(v.sum())] for k, v in m.items() if v.any() and not k.startswith("test")},
           "train_pos_rate": round(train_rate, 4), "features": F.FEATURES, "candidates_val": cand, "chosen": chosen, "models": {}}
    daily = grid.hour == 10
    for name, p in preds.items():
        ps = pd.Series(p, index=grid)
        thr = M.thresholds(ps[va], y[va], train_rate)
        r = {"train": M.scores(ps[tr], y[tr], preds["S0"][tr]),
             "val": M.scores(ps[va], y[va], preds["S0"][va]),
             "val_10am": M.scores(ps[va & daily], y[va & daily], preds["S0"][va & daily]),
             "val_reliability": M.reliability(ps[va], y[va]),
             "thresholds": {k: round(v, 4) for k, v in thr.items()}}
        for k, v in thr.items():
            r[f"val_at_{k}"] = M.at_threshold(ps[va], y[va], v)
            r[f"val_events_{k}"] = M.event_metrics(ps, v, arr, pd.Timestamp(PERIODS["val"][0]), pd.Timestamp(PERIODS["val"][1]))
        res["models"][name] = r
    res["elapsed_s"] = round(time.time() - t0, 1)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "baselines.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    # 시험 기간 정답은 캐시에 남기지 않는다(시험은 evaluate final에서 한 번만)
    yy = y.astype(float).where(grid < pd.Timestamp(PERIODS["test"][0]))
    pd.DataFrame({**preds, "y": yy}, index=grid).to_parquet(CACHE / "pred_baselines.parquet")
    print(f"{'모형':5s} {'검증 PR-AUC':>11s} {'ROC':>6s} {'Brier':>7s} {'BSS':>7s} | 경보 10%: 정밀도/재현율 · 놓친 에피소드 · 선행(중앙) · 오경보율")
    for name, r in res["models"].items():
        v, a, e = r["val"], r["val_at_rate_10"], r["val_events_rate_10"]
        print(f"{name:5s} {v['pr_auc']:>11} {v['roc_auc']:>6} {v['brier']:>7} {str(v.get('bss_vs_s0')):>7} | {a['precision']}/{a['recall']} "
              f"· {e['missed']}/{e['episodes']} · {e['lead_h_median']}h · {e['false_alarm_rate']}")
    print(f"검증 양성 비율 {res['models']['S0']['val']['pos_rate']} · 학습 {train_rate:.3f} · {res['elapsed_s']}초")


if __name__ == "__main__":
    main()
