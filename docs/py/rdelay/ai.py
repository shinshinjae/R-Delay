"""AI 모델 f_omega: 판단 시점까지 이용 가능한 설명변수로 로그 전파율 평균 z_bar를 예측한다.

입력은 기상(풍속·파고)·주말·장비 상태다. 물동량과 명절 급증은 xi로만 모형에 들어가므로 넣지 않는다
(원인 분해의 이중 반영 방지). 학습은 scripts/train_ai.py가 과거 구간 입자평활 추정치 E[z|y]를 레이블로
릿지 회귀를 적합해 docs/data/ai_model.json에 저장한다.
"""
from __future__ import annotations
import numpy as np
from . import config

FEATURES = ["wind", "wave", "weekend", "equip"]


def feature_series(t, scen: dict):
    """(len(t), n, F) 설명변수. 장비 상태는 거점별, 기상은 해상·항공에 공통으로 쓴다."""
    t = np.asarray(t, dtype=float)
    n = len(config.NODES)
    f = np.zeros((len(t), n, len(FEATURES)))
    wind = config.shock_value(scen.get("features", {}).get("wind"), t, config.FEATURE_BASE["wind"])
    wave = config.shock_value(scen.get("features", {}).get("wave"), t, config.FEATURE_BASE["wave"])
    weekend = np.zeros(len(t))
    for k, node in enumerate(config.NODES):
        f[:, k, 0] = (wind - 8.0) / 5.0
        f[:, k, 1] = wave - 1.0
        f[:, k, 2] = weekend
        f[:, k, 3] = config.shock_value(scen.get("features", {}).get(f"equip_{node['id']}"), t, 0.0)
    return f


# 합성 '참' 계수(진짜 세계). 학습 모델은 이것을 모른 채 데이터로 추정한다.
TRUE_COEF = np.array([
    # wind, wave, weekend, equip
    [0.10, 0.12, -0.05, 0.45],  # AT
    [0.08, 0.10, -0.05, 0.45],  # PN
    [0.05, 0.00, -0.08, 0.40],  # L
    [0.12, 0.04, -0.03, 0.40],  # A
])
TRUE_INTERCEPT = np.zeros(4)


def true_zbar(features):
    return TRUE_INTERCEPT[None, :] + np.einsum("tnf,nf->tn", features, TRUE_COEF)


def predict(model: dict | None, features):
    """학습된 릿지 계수로 z_bar 예측. 모델이 없으면 0(기준 전파율 그대로)."""
    if not model:
        return np.zeros(features.shape[:2])
    coef = np.asarray(model["coef"], dtype=float)
    intercept = np.asarray(model["intercept"], dtype=float)
    return intercept[None, :] + np.einsum("tnf,nf->tn", features, coef)


def fit_ridge(features, labels, lam=1.0):
    """거점별 릿지 회귀. features (T,n,F), labels (T,n)."""
    T, n, F = features.shape
    coef = np.zeros((n, F))
    intercept = np.zeros(n)
    for k in range(n):
        Xk = features[:, k, :]
        yk = labels[:, k]
        mx, my = Xk.mean(axis=0), yk.mean()
        Xc, yc = Xk - mx, yk - my
        A = Xc.T @ Xc + lam * np.eye(F)
        coef[k] = np.linalg.solve(A, Xc.T @ yc)
        intercept[k] = my - mx @ coef[k]
    return {"features": FEATURES, "coef": coef.tolist(), "intercept": intercept.tolist()}
