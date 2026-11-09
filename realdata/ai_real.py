"""f_ω 실자료 재학습(지시서 단계 3.6, PROTOCOL v1.1 5절).

특징(τ까지 관측): 부산 ASOS 풍속(최근 6시간 최대), 거제도 부이 유의파고(최근 6시간 최대), 주말, 공휴일, 태풍 근접도.
레이블: AI 없이 돌린 실자료 입자필터의 6시간 고정시차 평활 E[z_S | y](τ−6h 시각의 값). train_ai.py와 같은 2단계 방식
(평활 → 릿지)이며, 학습 기간으로 맞추고 검증 기간으로 시간순 평가한다. 결과는 realdata/results/ai_model_real.json
(docs/data/ai_model.json은 건드리지 않는다)과 realdata/cache/zbar_real.parquet(2시간 격자의 z̄_S 예측).

실행: python -m realdata.ai_real --rdelay realdata/cache/rdelay_all_W24_P512.parquet
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from . import baselines as B
from .paths import CACHE, REPO, RESULTS

sys.path.insert(0, str(REPO / "docs" / "py"))
from rdelay import ai  # noqa: E402

AI_FEATURES = ["wind_busan_max6", "wave_geoje_max6", "weekend", "holiday", "ty_close"]


def feature_frame(X: pd.DataFrame, train_mask) -> tuple[pd.DataFrame, dict]:
    f = pd.DataFrame(index=X.index)
    f["wind_busan_max6"] = X["wind_busan_max6"]
    f["wave_geoje_max6"] = X["wave_geoje_max6"]
    f["weekend"] = X["weekend"]
    f["holiday"] = X["holiday"]
    f["ty_close"] = np.clip(1.0 - X["ty_dist_km"] / 1000.0, 0.0, 1.0)  # 1000km 안에서 가까울수록 1
    med = f[train_mask].median()
    f = f.fillna(med)
    mu, sd = f[train_mask].mean(), f[train_mask].std().replace(0, 1.0)
    return (f - mu) / sd, {"mean": mu.round(5).to_dict(), "std": sd.round(5).to_dict(), "median_fill": med.round(5).to_dict()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rdelay", required=True, help="AI 없이 돌린 전체 기간 결과(z_S_smooth_lag 포함)")
    ap.add_argument("--lam", type=float, default=10.0)
    a = ap.parse_args()
    X, y, arr, tau_end = B.build_dataset()
    m = B.period_masks(X.index, tau_end)
    R = pd.read_parquet(a.rdelay)
    R = R[R["ok"]]
    lab = R["z_S_smooth_lag"].copy()
    lab.index = lab.index - pd.Timedelta(hours=6)  # τ−6h 시각의 평활값
    f, norm = feature_frame(X, m["train_fit"])
    d = f.join(lab.rename("z"), how="inner").dropna()
    tr = (d.index >= B.PERIODS["train"][0]) & (d.index < B.PERIODS["train"][1])
    va = (d.index >= B.PERIODS["val"][0]) & (d.index < B.PERIODS["val"][1])
    feats = d[AI_FEATURES].to_numpy()[:, None, :]
    model = ai.fit_ridge(feats[tr], d[["z"]].to_numpy()[tr], lam=a.lam)
    pred = ai.predict(model, feats)[:, 0]
    def r2(mask):
        e = d["z"].to_numpy()[mask] - pred[mask]
        v = d["z"].to_numpy()[mask].var()
        return None if v <= 0 else round(float(1 - (e ** 2).mean() / v), 4)
    out = {"features": AI_FEATURES, "coef": model["coef"][0], "intercept": model["intercept"][0], "lam": a.lam, "normalize": norm,
           "label": "AI 없이 돌린 실자료 입자필터의 6시간 고정시차 평활 E[z_S|y]", "n_train": int(tr.sum()), "n_val": int(va.sum()),
           "r2_train": r2(tr), "r2_val": r2(va), "corr_val": round(float(np.corrcoef(d["z"][va], pred[va])[0, 1]), 4),
           "label_sd_train": round(float(d["z"][tr].std()), 4)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "ai_model_real.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    zb = pd.DataFrame({"zbar_S": ai.predict(model, f[AI_FEATURES].to_numpy()[:, None, :])[:, 0]}, index=f.index)
    zb.to_parquet(CACHE / "zbar_real.parquet")
    print(json.dumps({k: v for k, v in out.items() if k != "normalize"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
