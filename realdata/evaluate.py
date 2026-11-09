"""단계 3 평가: R1(후보 점수 고르기)·R2(재보정)·R3(하이브리드)를 검증 기간에서 정하고, 시험 기간은 한 번만 계산한다.

  python -m realdata.evaluate select --rdelay realdata/cache/rdelay_all_W24_P512.parquet   # 학습·검증만
  python -m realdata.evaluate final  --rdelay …                                           # 시험 1회(로그를 남김)

시험을 다시 계산하려면 --rerun-reason "이유"가 필요하고, 이유와 이전 결과 위치가 realdata/results/test_runs.json에 쌓인다(PROTOCOL 규칙 6).
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from . import baselines as B
from . import features as F
from . import metrics as M
from .paths import CACHE, RESULTS

R1_CANDIDATES = ["p_rd1", "p_rd1_fore_max", "p_cong", "x_S_fore_mean_max", "q_any"]
RSTATE = ["x_S", "x_L", "rd_mean", "z_S", "p_rd1", "p_cong", "q_any"]
SELECT_PATH = RESULTS / "rdelay_select.json"
TEST_LOG = RESULTS / "test_runs.json"


def load_all(rdelay_path: str):
    X, y, arr, tau_end = B.build_dataset()
    R = pd.read_parquet(rdelay_path)
    R = R[R["ok"]].drop(columns=["ok"])
    pred = pd.read_parquet(CACHE / "pred_baselines.parquet")
    m = B.period_masks(X.index, tau_end)
    return X, y, arr, tau_end, R, pred, m


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


class Platt:
    def fit(self, s, y):
        self.m = LogisticRegression(C=1e6, max_iter=1000).fit(np.asarray(s, float).reshape(-1, 1), y)
        return self

    def predict(self, s):
        return self.m.predict_proba(np.asarray(s, float).reshape(-1, 1))[:, 1]


class Iso:
    def fit(self, s, y):
        self.m = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4).fit(np.asarray(s, float), y)
        return self

    def predict(self, s):
        return self.m.predict(np.asarray(s, float))


def hybrid_fit(X, R, y, mask, C, feats=None):
    Z = X[feats or F.FEATURES].join(R[RSTATE], how="left")
    mdl = B.lr_pipe(C).fit(Z[mask], y[mask])
    return mdl, Z


def select(rdelay_path: str) -> dict:
    X, y, arr, tau_end, R, pred, m = load_all(rdelay_path)
    tr, va = m["train_fit"], m["val_fit"]
    idx = X.index
    Rr = R.reindex(idx)
    have = Rr.notna().all(axis=1).to_numpy()
    tr, va = tr & have, va & have
    out = {"protocol": "v1.4", "rdelay": rdelay_path, "n_train": int(tr.sum()), "n_val": int(va.sum()), "r1_candidates_val": {}}
    # R1: 후보 점수를 검증 PR-AUC로 고른다(점수 자체, 보정 전)
    best = None
    for c in R1_CANDIDATES:
        s = Rr[c].to_numpy(float)
        sc = M.scores(s[va], y[va])
        out["r1_candidates_val"][c] = {"pr_auc": sc["pr_auc"], "roc_auc": sc["roc_auc"], "nonzero_share": round(float((s[va] > 1e-6).mean()), 4)}
        if sc["pr_auc"] is not None and (best is None or sc["pr_auc"] > best[0]):
            best = (sc["pr_auc"], c)
    r1 = best[1]
    out["R1_score"] = r1
    s = Rr[r1].to_numpy(float)
    # R2: 학습 기간 재보정(Platt·isotonic) → 검증 Brier로 고른다
    cal = {}
    for name, cls in (("platt", Platt), ("isotonic", Iso)):
        f = cls().fit(s[tr], y[tr])
        cal[name] = round(M.brier(f.predict(s[va]), y[va]), 5)
    out["R2_calibration_val_brier"] = cal
    out["R2_method"] = min(cal, key=cal.get)
    # R3: S2 특징 + R-Delay 상태, 로지스틱 강도는 검증 PR-AUC로
    cand = {}
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        mdl, Z = hybrid_fit(X, Rr, y, tr, C)
        cand[str(C)] = M.scores(mdl.predict_proba(Z[va])[:, 1], y[va])["pr_auc"]
    out["R3_candidates_val"] = cand
    out["R3_C"] = float(max(cand, key=cand.get))
    # R3b: S3 스케줄 특징 + R-Delay 상태(PROTOCOL v1.4)
    cand = {}
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        mdl, Z = hybrid_fit(X, Rr, y, tr, C, F.S3_FEATURES)
        cand[str(C)] = M.scores(mdl.predict_proba(Z[va])[:, 1], y[va])["pr_auc"]
    out["R3b_candidates_val"] = cand
    out["R3b_C"] = float(max(cand, key=cand.get))
    # 검증 기간 비교표(학습 적합, 검증 평가)
    p0 = pred["S0"].to_numpy()
    f2 = (Platt if out["R2_method"] == "platt" else Iso)().fit(s[tr], y[tr])
    mdl, Z = hybrid_fit(X, Rr, y, tr, out["R3_C"])
    mdlb, Zb = hybrid_fit(X, Rr, y, tr, out["R3b_C"], F.S3_FEATURES)
    # 준비 기간(첫 주)은 R-Delay 출력이 없다 → 예측할 때만 0으로 채운다(평가하지 않는 τ)
    models = {"S0": p0, "S1": pred["S1"].to_numpy(), "S2": pred["S2"].to_numpy(), "S2b": pred["S2b"].to_numpy(), "S3": pred["S3"].to_numpy(),
              "R1": np.nan_to_num(s), "R2": f2.predict(np.nan_to_num(s)), "R3": mdl.predict_proba(Z)[:, 1], "R3b": mdlb.predict_proba(Zb)[:, 1]}
    train_rate = float(y[tr].mean())
    out["val"] = {}
    for k, p in models.items():
        ps = pd.Series(p, index=idx)
        thr = M.thresholds(ps[va], y[va], train_rate)
        out["val"][k] = {"scores": M.scores(ps[va], y[va], p0[va]),
                         "at_rate_10": M.at_threshold(ps[va], y[va], thr["rate_10"]),
                         "events_rate_10": M.event_metrics(ps, thr["rate_10"], arr, pd.Timestamp(B.PERIODS["val"][0]), pd.Timestamp(B.PERIODS["val"][1])),
                         "events_rate_matched": M.event_metrics(ps, thr["rate_matched"], arr, pd.Timestamp(B.PERIODS["val"][0]), pd.Timestamp(B.PERIODS["val"][1]))}
    out["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SELECT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# ---------- 시험 기간(한 번) ----------
def _boot(df: pd.DataFrame, models: list[str], pairs: list[tuple[str, str]], reps: int = 1000, seed: int = 20261108) -> dict:
    """주 단위 블록 부트스트랩: 모형별 PR-AUC·ROC·Brier·BSS(대 S0)와 짝 차이(같은 표본)의 95% 구간."""
    from sklearn.metrics import average_precision_score, roc_auc_score
    rng = np.random.default_rng(seed)
    wk = df.index.to_period("W")
    groups = [np.where(wk == k)[0] for k in wk.unique()]
    acc: dict[str, list] = {}
    Y = df["y"].to_numpy()
    for _ in range(reps):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        yb = Y[idx]
        if yb.min() == yb.max():
            continue
        vals = {}
        b0 = np.mean((df["S0"].to_numpy()[idx] - yb) ** 2)
        for mname in models:
            pb = df[mname].to_numpy()[idx]
            vals[f"{mname}.pr_auc"] = average_precision_score(yb, pb)
            vals[f"{mname}.roc_auc"] = roc_auc_score(yb, pb)
            vals[f"{mname}.brier"] = np.mean((pb - yb) ** 2)
            vals[f"{mname}.bss_vs_s0"] = 1 - vals[f"{mname}.brier"] / b0
        for a, b in pairs:
            vals[f"{a}-{b}.pr_auc"] = vals[f"{a}.pr_auc"] - vals[f"{b}.pr_auc"]
            vals[f"{a}-{b}.roc_auc"] = vals[f"{a}.roc_auc"] - vals[f"{b}.roc_auc"]
        for k, v in vals.items():
            acc.setdefault(k, []).append(v)
    return {k: [round(float(np.quantile(v, 0.025)), 4), round(float(np.quantile(v, 0.975)), 4)] for k, v in acc.items()}


def _future_max(series: pd.Series, grid, hours=24):
    """(τ, τ+hours]의 실제 관측 최대값(기상 oracle 상한 전용, 예측에는 쓰지 않음)."""
    return F._rolling_at(series, grid + pd.Timedelta(hours=hours), hours, "max")


def final(rdelay_path: str, rerun_reason: str | None) -> dict:
    log = json.loads(TEST_LOG.read_text(encoding="utf-8")) if TEST_LOG.exists() else []
    if log and not rerun_reason:
        raise SystemExit(f"[중단] 시험 기간을 이미 {len(log)}번 계산했습니다({log[-1]['time']}). 다시 하려면 --rerun-reason이 필요합니다.")
    base = json.loads((RESULTS / "baselines.json").read_text(encoding="utf-8"))
    sel = json.loads(SELECT_PATH.read_text(encoding="utf-8"))
    X, y, arr, tau_end, R, pred, m = load_all(rdelay_path)
    idx = X.index
    Rr = R.reindex(idx)
    have = Rr[RSTATE + [sel["R1_score"]]].notna().all(axis=1).to_numpy()
    fit = (m["train_fit"] | m["val_fit"])
    va, te = m["val"], m["test"] & have
    # 학습+검증으로 다시 적합(설정은 검증에서 고른 그대로)
    ch = base["chosen"]
    s0 = B.fit_s0(X, y, fit, use_month=ch["S0"]["month_effect"])
    S1 = B.lr_pipe(1.0).fit(X.loc[fit, F.S1_FEATURES], y[fit])
    S2 = B.lr_pipe(ch["S2"]["C"]).fit(X.loc[fit, F.FEATURES], y[fit])
    S2b = B.hgb(ch["S2b"]["max_depth"], ch["S2b"]["max_iter"]).fit(X.loc[fit, F.FEATURES], y[fit])
    r1 = Rr[sel["R1_score"]].to_numpy(float)
    cal = (Platt if sel["R2_method"] == "platt" else Iso)().fit(r1[fit & have], y[fit & have])
    R3, Z = hybrid_fit(X, Rr, y, fit & have, sel["R3_C"])
    S3 = B.lr_pipe(ch["S3"]["C"]).fit(X.loc[fit, F.S3_FEATURES], y[fit])
    R3b, Zb = hybrid_fit(X, Rr, y, fit & have, sel["R3b_C"], F.S3_FEATURES)
    preds = {"S0": s0(X), "S1": S1.predict_proba(X[F.S1_FEATURES])[:, 1], "S2": S2.predict_proba(X[F.FEATURES])[:, 1],
             "S2b": S2b.predict_proba(X[F.FEATURES])[:, 1], "S3": S3.predict_proba(X[F.S3_FEATURES])[:, 1],
             "R1": np.nan_to_num(r1), "R2": cal.predict(np.nan_to_num(r1)),
             "R3": R3.predict_proba(Z.fillna(Z[fit & have].median()))[:, 1], "R3b": R3b.predict_proba(Zb.fillna(Zb[fit & have].median()))[:, 1]}
    # 기상 oracle 상한: (τ, τ+24h]의 실제 관측 최대 풍속·파고를 '완벽한 예보'로 더한 S2
    from .io import load as _load
    asos, buoy = _load("asos"), _load("buoy")
    bus = asos[asos["stn"].astype(str) == "159"].set_index("t")["wind"]
    geo = buoy[buoy["stn"].astype(str) == "22104"].set_index("t")["wave_sig"]
    Xo = X[F.FEATURES].assign(fut_wind=_future_max(bus, idx), fut_wave=_future_max(geo, idx))
    So = B.lr_pipe(ch["S2"]["C"]).fit(Xo[fit], y[fit])
    preds["S2_oracle_weather"] = So.predict_proba(Xo)[:, 1]
    P = pd.DataFrame(preds, index=idx)
    P["y"] = y.to_numpy()
    train_rate = float(y[m["train_fit"]].mean())
    t0, t1 = pd.Timestamp(B.PERIODS["test"][0]), tau_end + pd.Timedelta(hours=2)
    out = {"protocol": "v1.4", "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "rdelay": rdelay_path, "tau_end": str(tau_end),
           "test": [str(idx[te].min()), str(idx[te].max()), int(te.sum())], "test_pos_rate": round(float(y[te].mean()), 4),
           "chosen": {"baselines": ch, "rdelay": {k: sel[k] for k in ("R1_score", "R2_method", "R3_C", "R3b_C")}},
           "threshold_note": "경보 기준은 경보 비율로 넘긴다: 재적합 모형의 검증 기간 점수에서 F1 최대·학습 기저율·10%에 해당하는 분위수",
           "models": {}}
    daily = (idx.hour == 10)
    for k in preds:
        ps = P[k]
        thr = M.thresholds(ps[va], y[va], train_rate)
        r = {"test": M.scores(ps[te], y[te], P["S0"][te]), "test_10am": M.scores(ps[te & daily], y[te & daily], P["S0"][te & daily]),
             "test_reliability": M.reliability(ps[te], y[te]), "thresholds": {kk: round(v, 4) for kk, v in thr.items()}}
        for tk, tv in thr.items():
            r[f"test_at_{tk}"] = M.at_threshold(ps[te], y[te], tv)
            r[f"test_events_{tk}"] = M.event_metrics(ps, tv, arr, t0, t1)
        out["models"][k] = r
    # 소분석(시험): 주말·공휴일·계절·태풍
    sub = {"weekend": X["weekend"].to_numpy() > 0, "holiday": X["holiday"].to_numpy() > 0, "typhoon_500km": X["ty_within_500"].to_numpy() > 0}
    for nm, months in {"winter": (12, 1, 2), "spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11)}.items():
        sub[nm] = np.isin(idx.month, months)
    out["subgroups_test"] = {}
    for nm, msk in sub.items():
        mm = te & msk
        if mm.sum() >= 20 and 0 < y[mm].mean() < 1:
            out["subgroups_test"][nm] = {"n": int(mm.sum()), "pos_rate": round(float(y[mm].mean()), 4),
                                         **{k: M.scores(P[k][mm], y[mm])["pr_auc"] for k in ("S0", "S1", "S2", "S3", "R1", "R3", "R3b")}}
        else:
            out["subgroups_test"][nm] = {"n": int(mm.sum()), "note": "표본이 적거나 한쪽 정답만 있어 계산 안 함"}
    main_models = ["S0", "S1", "S2", "S2b", "S3", "R1", "R2", "R3", "R3b"]
    out["bootstrap_95"] = _boot(P[te], main_models, [("R1", "S1"), ("R1", "S0"), ("R2", "S1"), ("R3", "S2"), ("R3b", "S3"), ("S3", "S0"),
                                                     ("S3", "S2"), ("R1", "S3")])
    out["vessel_level"] = vessel_level(X, Rr, fit, te)
    # 기록(시험은 한 번만: 다시 하면 이유와 함께 쌓인다)
    entry = {"time": out["generated"], "protocol": out["protocol"], "rdelay": rdelay_path, "reason": rerun_reason or "첫 시험 계산",
             "R3_test_pr_auc": out["models"]["R3"]["test"]["pr_auc"], "S2_test_pr_auc": out["models"]["S2"]["test"]["pr_auc"]}
    TEST_LOG.write_text(json.dumps(log + [entry], ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS / "rdelay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    P.to_parquet(CACHE / "pred_final.parquet")
    return out


def vessel_level(X: pd.DataFrame, Rr: pd.DataFrame, fit, te) -> dict:
    """T2: 선박 단위 P(12h < 대기 ≤ 168h). T2-0은 정박지로 도착한 항차(도착 직후 판단), T2-h는 도착 h시간 전 판단(낙관적 가정)."""
    from .io import load as _load
    voy = _load("voyages")
    voy = voy[voy["wait_h"] <= 168].copy()
    voy["y"] = (voy["wait_h"] > 12).astype(int)
    voy["lgrt"] = np.log1p(voy["vsslGrtg"].fillna(voy["vsslGrtg"].median()))
    fit_t = set(X.index[fit])
    te_t = set(X.index[te])
    out = {}
    specs = [("T2-0(정박지 도착, 도착 직후)", voy[voy["wait_kind"] == "정박지→선석"], 0)] + \
            [(f"T2-{h}(도착 {h}시간 전, 도착 시각을 안다는 낙관적 가정)", voy, h) for h in (24, 12, 6)]
    for name, v, h in specs:
        tau = (v["arrival_t"] - pd.Timedelta(hours=h)).dt.floor("2h")
        ok = tau.isin(X.index)
        v, tau = v[ok], tau[ok]
        feats = X.loc[tau, F.FEATURES].to_numpy()
        qcol = "q_h00" if h == 0 else f"q_h{h - 1:02d}"
        q = Rr.reindex(tau)[qcol].to_numpy(float) if qcol in Rr else np.full(len(v), np.nan)
        D = pd.DataFrame(feats, columns=F.FEATURES, index=v.index).assign(lgrt=v["lgrt"].to_numpy(), q=q)
        is_fit = tau.isin(fit_t).to_numpy()
        is_te = tau.isin(te_t).to_numpy()
        yv = v["y"].to_numpy()
        if is_te.sum() < 20 or yv[is_te].min() == yv[is_te].max():
            out[name] = {"n_test": int(is_te.sum()), "note": "표본 부족"}
            continue
        base_cols = F.FEATURES + ["lgrt"]
        b = B.lr_pipe(0.1).fit(D.loc[is_fit, base_cols], yv[is_fit])
        pb = b.predict_proba(D.loc[is_te, base_cols])[:, 1]
        r = {"n_fit": int(is_fit.sum()), "n_test": int(is_te.sum()), "pos_rate_test": round(float(yv[is_te].mean()), 4),
             "baseline_lr": M.scores(pb, yv[is_te])}
        if np.isfinite(q[is_te]).all():
            r["rdelay_queue"] = M.scores(q[is_te], yv[is_te])
            hcols = base_cols + ["q"]
            hm = B.lr_pipe(0.1).fit(D.loc[is_fit, hcols].fillna(0), yv[is_fit])
            r["hybrid_lr"] = M.scores(hm.predict_proba(D.loc[is_te, hcols].fillna(0))[:, 1], yv[is_te])
        out[name] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["select", "final"])
    ap.add_argument("--rdelay", required=True)
    ap.add_argument("--rerun-reason", default=None)
    a = ap.parse_args()
    if a.cmd == "final":
        r = final(a.rdelay, a.rerun_reason)
        print(json.dumps({k: r["models"][k]["test"] for k in r["models"]}, ensure_ascii=False))
        return
    r = select(a.rdelay)
    print("R1 후보(검증):", json.dumps(r["r1_candidates_val"], ensure_ascii=False))
    print("R1 =", r["R1_score"], "· R2 =", r["R2_method"], r["R2_calibration_val_brier"], "· R3 C =", r["R3_C"])
    print(f"{'모형':4s} {'PR-AUC':>7s} {'ROC':>6s} {'Brier':>7s} {'BSS':>7s} | 경보10% 정밀도/재현율 · 놓침 · 선행 · 오경보율")
    for k, v in r["val"].items():
        s, a10, e = v["scores"], v["at_rate_10"], v["events_rate_10"]
        print(f"{k:4s} {s['pr_auc']:>7} {s['roc_auc']:>6} {s['brier']:>7} {str(s.get('bss_vs_s0')):>7} | {a10['precision']}/{a10['recall']} · "
              f"{e['missed']}/{e['episodes']} · {e['lead_h_median']}h · {e['false_alarm_rate']}")


if __name__ == "__main__":
    main()
