"""시간가변 FCFS M(t)/M(t)/m 선석 대기열(Kolmogorov 전진방정식)과 대상 선박 추적.

상태 0..cap에 흡수 넘침상태 1개를 더한다(넘침 확률을 숨기지 않는다). 균일화(uniformization)로 푼다.
SIS 지연비율 x가 선석 생산성을 낮추는 연결 mu = mu0 * exp(-eta_B * x) * open 은 추가 가정이다.
정기선의 선석 윈도 배정과 달라 '윈도를 벗어난 선박의 대기'에 적용 범위를 한정한다.
"""
from __future__ import annotations
import math
import numpy as np


def kfe_step(q, a, mu, servers, dt):
    """q: (P, cap+2), a/mu: (P,) 구간 상수 비율."""
    rows, width = q.shape
    finite = width - 1
    rate = float(np.max(a + servers * mu))
    if rate <= 0 or dt <= 0:
        return q
    lam = rate * dt
    if lam > 30:
        raise ValueError("uniformization interval too large")
    deaths = mu[:, None] * np.minimum(np.arange(finite), servers)[None, :]

    def embedded(v):
        birth = v[:, :finite] * a[:, None] / rate
        death = v[:, :finite] * deaths / rate
        nxt = v.copy()
        nxt[:, :finite] -= birth + death
        nxt[:, 1:finite] += birth[:, :-1]
        nxt[:, -1] += birth[:, -1]          # 상한 초과 -> 흡수 넘침
        nxt[:, : finite - 1] += death[:, 1:]
        return nxt

    term = q.copy()
    prob = math.exp(-lam)
    total = prob
    out = prob * term
    j = 0
    while 1.0 - total > 1e-13 or j <= lam:
        j += 1
        term = embedded(term)
        prob *= lam / j
        out += prob * term
        total += prob
        if j > 400:
            raise ValueError("uniformization did not converge")
    out += (1.0 - total) * term
    if out.min() < -1e-12 or not np.allclose(out.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("queue probability mass check failed")
    return out


def service_rates(x_path, mu0, eta_b, open_path, mu_mult):
    """x_path: (P, T) 해당 터미널 지연비율 -> 선석 하나의 서비스율 (P, T)."""
    return mu0 * mu_mult[None, :] * np.exp(-eta_b * x_path) * open_path[None, :]


def distribution_at(t, mu, a, servers, n0, cap, t_arrival):
    """t=0에 선박 수 n0를 알 때 대상 선박 도착 직전의 선박 수 분포 (P, cap+2)."""
    P = mu.shape[0]
    q = np.zeros((P, cap + 2))
    q[:, min(n0, cap)] = 1.0
    for s in range(len(t) - 1):
        if t[s] >= t_arrival - 1e-9:
            break
        h = min(t[s + 1], t_arrival) - t[s]
        q = kfe_step(q, np.full(P, a[s]), mu[:, s], servers, h)
    return q


def cumulative_hazard(t, mu):
    inc = 0.5 * (mu[:, :-1] + mu[:, 1:]) * np.diff(t)[None, :]
    return np.concatenate([np.zeros((mu.shape[0], 1)), np.cumsum(inc, axis=1)], axis=1)


def interp_rows(t, Y, X, right=None):
    """행마다 다른 값 Y (P, T)를 공통 격자 t에서 X (P, S)로 선형보간(np.interp와 같은 끝 처리).

    right가 주어지면 t[-1]보다 큰 X에는 그 값을 쓴다. 파이썬 행 반복 없이 한 번에 계산한다.
    """
    X = np.asarray(X, dtype=float)
    idx = np.clip(np.searchsorted(t, X, side="right"), 1, len(t) - 1)
    x0, x1 = t[idx - 1], t[idx]
    y0 = np.take_along_axis(Y, idx - 1, axis=1)
    y1 = np.take_along_axis(Y, idx, axis=1)
    with np.errstate(invalid="ignore"):
        frac = np.clip((X - x0) / (x1 - x0), 0.0, 1.0)
    out = y0 + frac * (y1 - y0)
    if right is not None:
        out = np.where(X > t[-1], right, out)
    return out


def invert_rows(t, H, targets, earliest):
    """각 행의 누적 작업량 H에서 목표치에 처음 닿는 시각. 범위를 넘으면 inf.

    earliest: 스칼라 또는 targets와 같은 모양. 결과는 earliest보다 이르지 않다.
    행마다 단조인 H를 행 번호만큼 큰 값으로 밀어 이어 붙여 searchsorted 한 번으로 찾는다.
    """
    P, S = targets.shape
    T = H.shape[1]
    tg = np.asarray(targets, dtype=float)
    early = np.broadcast_to(np.asarray(earliest, dtype=float), (P, S))
    fin = np.isfinite(tg)
    big = float(max(np.max(H), np.max(tg[fin]) if fin.any() else 0.0)) + 1.0
    offset = (np.arange(P, dtype=float) * big)[:, None]
    flat = (H + offset).ravel()
    j = np.searchsorted(flat, (np.where(fin, tg, 0.0) + offset).ravel(), side="left").reshape(P, S)
    j = j - (np.arange(P) * T)[:, None]
    ok = fin & (j > 0) & (j < T)
    jj = np.clip(j, 1, T - 1)
    lo = np.take_along_axis(H, jj - 1, axis=1)
    hi = np.take_along_axis(H, jj, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.where(hi > lo, (tg - lo) / np.where(hi > lo, hi - lo, 1.0), 1.0)
    val = t[jj - 1] + frac * (t[jj] - t[jj - 1])
    out = np.full((P, S), np.inf)
    out[ok] = np.maximum(val[ok], early[ok])
    first = fin & (j == 0)
    out[first] = early[first]
    return out


def tagged_ship(t, mu, a, servers, n0, cap, t_arrival, samples, rng, own_shape=4.0, priority_frac=0.15):
    """대상 선박의 작업 시작·완료 시각과 그 배에 실린 대상 컨테이너의 양하 시각 (P, S).

    n >= m이면 n-m+1번 완료를 기다린다: 운영시간 기준 Gamma(n-m+1, 1/m)(배경 선박은 지수 서비스 근사).
    대상 선박 자기 작업량은 평균 1, 변동계수 1/sqrt(own_shape)의 감마분포(실제 하역시간의 낮은 변동 반영).
    대상 컨테이너는 선박 작업 중 균등한 위치 U에서 양하된다(작업량 U * own).
    우선 양하가 승인되면 위치가 priority_frac * U로 앞당겨진다(같은 U를 써서 비교).
    """
    q = distribution_at(t, mu, a, servers, n0, cap, t_arrival)
    P = mu.shape[0]
    counts = np.empty((P, samples), dtype=int)
    for i in range(P):
        counts[i] = rng.choice(cap + 2, size=samples, p=q[i] / q[i].sum())
    overflow = counts == cap + 1
    need = np.maximum(counts - servers + 1, 0)
    wait_work = np.where(need > 0, rng.gamma(np.maximum(need, 1), 1.0 / servers), 0.0)
    own = rng.gamma(own_shape, 1.0 / own_shape, size=(P, samples))
    pos = rng.uniform(0.0, 1.0, size=(P, samples))
    H = cumulative_hazard(t, mu)
    h_arr = interp_rows(t, H, np.full((P, 1), float(t_arrival)))
    begin = invert_rows(t, H, h_arr + wait_work, t_arrival)
    finish = invert_rows(t, H, h_arr + wait_work + own, t_arrival)
    box = invert_rows(t, H, h_arr + wait_work + pos * own, t_arrival)
    box_prio = invert_rows(t, H, h_arr + wait_work + priority_frac * pos * own, t_arrival)
    for arr in (begin, finish, box, box_prio):
        arr[overflow] = np.inf
    return {"dist": q, "begin": begin, "finish": finish, "box": box, "box_priority": box_prio,
            "wait": begin - t_arrival}
