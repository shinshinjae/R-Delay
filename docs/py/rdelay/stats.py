"""확률 보조 함수(NumPy만 사용)."""
from __future__ import annotations
import math
import numpy as np

_lgamma = np.vectorize(math.lgamma, otypes=[float])


def logsumexp(a, axis=None):
    a = np.asarray(a, dtype=float)
    m = np.max(a, axis=axis, keepdims=True)
    m = np.where(np.isfinite(m), m, 0.0)
    out = np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True)) + m
    return np.squeeze(out, axis=axis) if axis is not None else float(out.reshape(()))


def nb_logpmf(y, mean, size):
    """음이항 log P(Y=y), 평균 mean, 산포 size(r). Var = mean + mean^2/size.

    mean=0이면 y=0에서 0, 그 외 -inf(퇴화분포).
    """
    y = np.asarray(y, dtype=float)
    mean = np.asarray(mean, dtype=float)
    if np.any(y < 0) or size <= 0 or np.any(mean < 0):
        raise ValueError("invalid negative-binomial arguments")
    y_b, m_b = np.broadcast_arrays(y, mean)
    out = np.full(m_b.shape, -np.inf)
    pos = m_b > 0
    if np.any(pos):
        yy, mm = y_b[pos], m_b[pos]
        out[pos] = (_lgamma(yy + size) - _lgamma(size) - _lgamma(yy + 1.0)
                    + size * np.log(size / (size + mm)) + yy * np.log(mm / (size + mm)))
    out[(~pos) & (y_b == 0)] = 0.0
    return out


def betabinom_logpmf(k, n, p, concentration):
    """현재 지연 표본 수: n건 중 k건 지연, pi ~ Beta(phi*p, phi*(1-p)). p=0/1은 퇴화분포."""
    k = np.asarray(k, dtype=float)
    p = np.asarray(p, dtype=float)
    n = np.asarray(n, dtype=float)
    k_b, p_b, n_b = np.broadcast_arrays(k, p, n)
    out = np.full(p_b.shape, -np.inf)
    mid = (p_b > 0) & (p_b < 1)
    if np.any(mid):
        kk, pp, nn = k_b[mid], p_b[mid], n_b[mid]
        a, b = concentration * pp, concentration * (1 - pp)
        out[mid] = (_lgamma(nn + 1) - _lgamma(kk + 1) - _lgamma(nn - kk + 1)
                    + _lgamma(kk + a) + _lgamma(nn - kk + b) - _lgamma(nn + a + b)
                    - (_lgamma(a) + _lgamma(b) - _lgamma(a + b)))
    out[(p_b == 0) & (k_b == 0)] = 0.0
    out[(p_b == 1) & (k_b == n_b)] = 0.0
    return out


def betabinom_sample(rng, n, p, concentration):
    p = np.clip(np.asarray(p, dtype=float), 1e-9, 1 - 1e-9)
    pi = rng.beta(concentration * p, concentration * (1 - p))
    return rng.binomial(np.asarray(n), pi)


def nb_sample(rng, mean, size):
    mean = np.asarray(mean, dtype=float)
    lam = rng.gamma(size, np.maximum(mean, 0.0) / size)
    return rng.poisson(lam)


def normalize_log_weights(logw):
    total = logsumexp(logw)
    if not np.isfinite(total):
        raise ValueError("observation impossible under every particle")
    w = np.exp(logw - total)
    return w / w.sum(), float(total)


def ess(w):
    w = np.asarray(w, dtype=float)
    return float(1.0 / np.sum(w * w))


def systematic_resample(w, rng):
    w = np.asarray(w, dtype=float)
    n = len(w)
    positions = (rng.random() + np.arange(n)) / n
    cum = np.cumsum(w)
    cum[-1] = 1.0
    return np.searchsorted(cum, positions, side="right")


def weighted_quantile(values, w, probs):
    values = np.asarray(values, dtype=float)
    w = np.asarray(w, dtype=float)
    order = np.argsort(values)
    cum = np.cumsum(w[order])
    cum = cum / cum[-1]
    idx = np.searchsorted(cum, np.asarray(probs, dtype=float), side="left")
    return values[order[np.minimum(idx, len(values) - 1)]]


def weighted_quantiles_rows(values, w, probs):
    """values: (P, T) 각 열마다 가중 분위수 -> (len(probs), T)."""
    values = np.asarray(values, dtype=float)
    out = np.empty((len(probs), values.shape[1]))
    for j in range(values.shape[1]):
        out[:, j] = weighted_quantile(values[:, j], w, probs)
    return out


def wilson_upper(p, n, z=1.645):
    """이항 비율 p(표본 n)의 단측 Wilson 상한. p=0에서도 0보다 큰 상한을 준다."""
    if n <= 0:
        return 1.0
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(max(p * (1 - p) / n + z * z / (4 * n * n), 0.0))
    return min(1.0, (centre + half) / denom)


def wald_upper_ess(p, ess, z=1.645, alpha=0.05):
    """계획서 식 p_hat + z_0.95 * sqrt(p_hat(1 - p_hat) / ESS)에 0건 경계 보정을 더한 상한.

    p_hat이 0에 가까우면 계획서 식은 상한도 0이 되므로, 실패가 한 건도 없을 때의 단측 상한
    1 - alpha^(1/ESS)(이른바 3/n 규칙)보다 작아지지 않게 한다.
    모형 안의 몬테카를로 오차만 반영한다(모수·모형 오차는 포함하지 않는다).
    """
    if ess <= 0:
        return 1.0
    wald = p + z * math.sqrt(max(p * (1.0 - p), 0.0) / ess)
    floor = 1.0 - alpha ** (1.0 / ess)
    return min(1.0, max(wald, floor))


def weighted_mean_quantiles(values, w, probs):
    """values: (P, S) 입자별 표본. 입자 가중치 w를 표본에 나눠 준 가중 분위수.

    예측 구간 안에 끝나지 않은 표본(inf)도 확률 질량에 넣고, 분위수가 inf면 None을 돌려준다.
    """
    values = np.asarray(values, dtype=float)
    P, S = values.shape
    ww = np.repeat(np.asarray(w, dtype=float) / S, S)
    q = weighted_quantile(values.ravel(), ww, probs)
    return [float(v) if np.isfinite(v) else None for v in q]
