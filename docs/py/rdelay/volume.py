"""물동량 보정계수 xi (선행연구 식의 물류 이전).

m_i = X_i / K_i,  L_i = mean(m_i),  L_ref = 같은 도메인 계층 평균,  c_i = m_i / L_i
b_i(t) = 후행 창 (t-w, t] 평균(요일·시간대 계절지수로 나눈 뒤),  xi_i = (L_i/L_ref)^theta * b_i
실시간 경보에는 후행 창만 쓴다(미래 정보 차단). 중심 창은 사후 분석에만.
"""
from __future__ import annotations
import numpy as np
from . import config

CAPACITY = np.array([30.0, 80.0, 400.0, 40.0])     # 거점 기준규모(시간당 처리 가능량, 합성)
UTILIZATION = np.array([0.92, 0.74, 0.70, 0.65])   # 평시 평균 가동률(합성) -> 구조적 수준 L_i


def hourly_profile(t):
    """시간대 계절지수(평균 1): 낮에 높고 새벽에 낮다."""
    hour = (np.asarray(t, dtype=float) + 10.0) % 24.0  # t=0 이 10:00
    return 1.0 + 0.35 * np.sin(2 * np.pi * (hour - 8.0) / 24.0)


def synthetic_volume(t, scen: dict, rng=None, noise=0.04):
    """합성 시간당 물동량 X_i(t) (P, n) 아님 -> (len(t), n)."""
    t = np.asarray(t, dtype=float)
    n = len(config.NODES)
    X = np.empty((len(t), n))
    prof = hourly_profile(t)
    for k, node in enumerate(config.NODES):
        mult = config.shock_value(scen.get("volume", {}).get(node["id"]), t)
        X[:, k] = CAPACITY[k] * UTILIZATION[k] * prof * mult
    if rng is not None and noise > 0:
        X *= np.exp(noise * rng.standard_normal(X.shape))
    return X


def xi_series(t, X, theta=0.0, window_h=24.0, dt=None, past_X=None, past_t=None):
    """후행 창 xi_i(t). past_X/past_t로 창 초기값을 채운다(시작 시점 이전 기록)."""
    t = np.asarray(t, dtype=float)
    if past_X is not None:
        tt = np.concatenate([np.asarray(past_t, dtype=float), t])
        XX = np.vstack([past_X, X])
    else:
        tt, XX = t, X
    m = XX / CAPACITY
    L = CAPACITY * UTILIZATION / CAPACITY        # 평시 평균 m_i = 가동률
    c = m / L / hourly_profile(tt)[:, None]
    # 도메인 계층별 L_ref
    domains = [nd["domain"] for nd in config.NODES]
    ratio = np.ones(len(domains))
    for d in set(domains):
        idx = [k for k, dd in enumerate(domains) if dd == d]
        ratio[idx] = L[idx] / np.mean(L[idx])
    out = np.empty((len(t), len(domains)))
    start = len(tt) - len(t)
    for s in range(len(t)):
        cur = tt[start + s]
        mask = (tt > cur - window_h) & (tt <= cur)
        out[s] = c[mask].mean(axis=0)
    return out * ratio ** theta, ratio
