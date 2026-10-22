"""물동량 보정 다층 SIS와 지연 재생산수.

비율식: dx_i/dt = (1-x_i) * [eps_i + xi_i * beta_i * sum_j Ct_ij x_j] - gamma_i x_i
Ct = I + kappa*tau0*M (비율 단위). eps=0이면 계획서 식(1)과 같다.
R_d = rho(diag(xi*beta*(1-x)) Ct Gamma^-1): 현재 상태를 고정한 전파 행렬의 스펙트럼 반지름.
"""
from __future__ import annotations
import numpy as np
from . import config


def coupling(kappa=1.0, tau0=1.0, overrides=None) -> np.ndarray:
    n = len(config.NODES)
    M = np.zeros((n, n))
    table = dict(config.COUPLING)
    if overrides:
        table.update(overrides)
    for (i, j), v in table.items():
        M[config.IDX[i], config.IDX[j]] = v
    if kappa < 0:
        raise ValueError("kappa must be nonnegative")
    return np.eye(n) + kappa * tau0 * M


def rhs(x, xi, beta, gamma, eps, C):
    """x: (P,n) 비율, xi/beta: (P,n) 또는 (n,), gamma/eps: (n,)."""
    force = eps + xi * beta * (x @ C.T)
    return (1.0 - x) * force - gamma * x


def heun_step(x, xi, beta, gamma, eps, C, dt):
    k1 = rhs(x, xi, beta, gamma, eps, C)
    k2 = rhs(np.clip(x + dt * k1, 0.0, 1.0), xi, beta, gamma, eps, C)
    y = x + 0.5 * dt * (k1 + k2)
    if not np.all(np.isfinite(y)):
        raise ValueError("SIS integration produced non-finite values")
    # 불변성 [0,1]은 연속계에서 보장된다. 이산화 오차로 1e-9보다 크게 벗어나면 오류로 알린다.
    if np.any(y < -1e-9) or np.any(y > 1 + 1e-9):
        raise ValueError("SIS step left [0,1]; reduce dt")
    return np.clip(y, 0.0, 1.0)


def new_delay_rate(x, xi, beta, eps, C, N):
    """신규 지연 발생률(건/h) = N(1-x)(eps + xi*beta*Ct x)."""
    return N * (1.0 - x) * (eps + xi * beta * (x @ C.T))


def next_gen(x, xi, beta, gamma, C):
    """K = diag(xi*beta*(1-x)) C Gamma^-1, 입자별 (P,n,n)."""
    x = np.atleast_2d(x)
    a = np.broadcast_to(xi * beta * (1.0 - x), x.shape)
    return a[:, :, None] * C[None, :, :] / np.asarray(gamma)[None, None, :]


def spectral_radius(K) -> np.ndarray:
    K = np.asarray(K)
    if K.ndim == 2:
        K = K[None]
    return np.max(np.abs(np.linalg.eigvals(K)), axis=-1).real


def elasticities(K):
    """Perron 탄력성 e_ij = v_i K_ij u_j / (rho v.u), 합 1. 기약 행렬에서 정의."""
    K = np.asarray(K, dtype=float)
    vals, right = np.linalg.eig(K)
    k = int(np.argmax(vals.real))
    rho = float(vals[k].real)
    u = np.abs(right[:, k].real)
    lvals, left = np.linalg.eig(K.T)
    v = np.abs(left[:, int(np.argmax(lvals.real))].real)
    denom = rho * float(v @ u)
    if denom <= 0:
        raise ValueError("elasticity undefined")
    E = (v[:, None] * K * u[None, :]) / denom
    return rho, E


def amplification(K, horizon=6, N=None):
    """하류 증폭: sum_{n=1..H} K^n. 열 j는 거점 j 지연 1단위가 H세대 동안 각 거점에 낳는 기대 지연.

    K는 비율 단위다. N(거점 규모)을 주면 개수 단위 D K^n D^-1 (D = diag N)로 바꿔
    '지연 1건이 낳는 기대 지연 건수'를 돌려준다(원소는 닮음변환에 불변이 아니다).
    """
    K = np.asarray(K, dtype=float)
    acc = np.zeros_like(K)
    P = np.eye(len(K))
    for _ in range(horizon):
        P = P @ K
        acc += P
    if N is not None:
        N = np.asarray(N, dtype=float)
        acc = acc * N[:, None] / N[None, :]
    return acc
