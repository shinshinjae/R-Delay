"""단계 3: R-Delay를 실자료에 적용(해상 S = 신항 정박지 대기, 육상 L = 배후 고속도로). PROTOCOL v1.1 5·6절.

엔진(docs/py/rdelay)의 입자필터·예측·대기열·통계 함수를 그대로 쓰고, 판단 시각 τ마다 과거 W시간 창으로
필터 → 24시간 예측 → 대기열을 돌린다(앱과 같은 운영 방식). 항공은 자료가 없어 뺀다.

관측(2시간마다, τ까지):
  S  H = 정박지에서 2시간 넘게 기다린 배 / (정박지 대기 + 신항 선석 체류), Y = 2시간 동안 새로 2시간을 넘긴 배
  L  H = 혼잡 콘존 수 / 측정이 있는 유효 콘존 수(학습 기간 기준속도의 60% 미만), Y = 새로 혼잡해진 콘존 수
정적 모수는 학습 기간 자료로 빠르게 맞춘다(calibrate). 시험 기간은 보지 않는다.

실행: python -m realdata.rdelay_real calibrate | run --period val --window 24 [--jobs 8]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from scipy.optimize import nnls
from scipy.special import gammainc

from . import features as F
from .io import load
from .paths import CACHE, REPO, RESULTS

sys.path.insert(0, str(REPO / "docs" / "py"))
from rdelay import engine, queue as Q, sis, stats  # noqa: E402

DT = 1.0 / 6.0
DELAY_H = 2.0          # 정박지 대기가 이 시간을 넘으면 '지연'
WAIT_X = 12.0
TRAIN = ("2023-01-08", "2025-01-01")
# 마지막 판단 시각은 자료에서 정한다(features.data_tau_end: 도착 자료 끝 − 7일). 2026-11-04까지 자료에서는 2026-10-29 00:00.
PERIODS = {"train": ("2023-01-08", "2025-01-01"), "val": ("2025-01-01", "2025-07-01"), "test": ("2025-07-01", "2100-01-01")}
PARAMS_PATH = RESULTS / "rdelay_params.json"


# ---------- 관측 계열(2시간 격자) ----------
def _road_hourly(road: pd.DataFrame, fit: dict) -> pd.DataFrame:
    """콘존·시간별 혼잡 여부(측정이 있는 유효 콘존만)."""
    r = road[road["zone_id"].isin(fit["zones"]) & (road["speed"] > 0)].copy()
    idx = pd.MultiIndex.from_arrays([r["zone_id"], r["t"].dt.day_of_week, r["t"].dt.hour])
    r["ref"] = fit["ref"].reindex(idx).to_numpy()
    r = r.dropna(subset=["ref"])
    r["cong"] = r["speed"] < fit["ratio"] * r["ref"]
    return r[["t", "zone_id", "cong"]]


def build_obs(data: dict, road_fit: dict, end=None) -> pd.DataFrame:
    if end is None:
        end = F.data_tau_end(load("voyages"))
    grid = F.tau_grid("2023-01-01", end)
    ev = F.container_events(data["cntl"])
    anc = F.anchorage_intervals(ev)
    npa = anc[anc["np_anchor"]]
    brt = F.berth_intervals(ev)
    arr = F.arrivals(ev)
    arr_np = arr[arr["np"]]
    out = pd.DataFrame(index=grid)
    out["anchor_np"] = F._active_count(npa["start"], npa["end"], grid)
    out["berthed_np"] = F._active_count(brt["start"], brt["end"], grid)
    out["H_S"] = F._active_count(npa["start"], npa["end"], grid, min_age_h=DELAY_H)
    out["n_S"] = out["anchor_np"] + out["berthed_np"]
    dur = (npa["end"] - npa["start"]).dt.total_seconds() / 3600
    late = npa[npa["end"].isna() | (dur > DELAY_H)]
    out["Y_S"] = F._count_in(late["start"] + pd.Timedelta(hours=DELAY_H), grid, 2.0)
    # 물동량 보정 ξ_S: 총톤수 가중 도착량(최근 24시간) / 같은 양의 최근 28일 평균(τ까지)
    g24 = pd.Series(F._count_in(arr_np["t"], grid, 24, weights=arr_np["grt"]), index=grid)
    base = g24.rolling(28 * 12, min_periods=12 * 7).mean()
    out["xi_S"] = (g24 / base).clip(0.5, 2.0).fillna(1.0)
    # 도로: 시간별 혼잡 → 2시간 관측(그 시간이 끝난 시각 기준, τ까지 확정된 두 시간)
    rh = _road_hourly(data["road"], road_fit)
    cong = rh.pivot_table(index="t", columns="zone_id", values="cong", aggfunc="max")
    hours = pd.date_range(grid[0] - pd.Timedelta(hours=4), grid[-1], freq="h")
    cong = cong.reindex(hours)
    valid = cong.notna()
    c = cong.fillna(False).astype(bool)
    newly = c & ~c.shift(1, fill_value=False)
    h1 = grid - pd.Timedelta(hours=1)  # τ−1h에 시작해 τ에 끝난 시간
    h2 = grid - pd.Timedelta(hours=2)
    out["H_L"] = c.reindex(h1).sum(axis=1).to_numpy()
    out["n_L"] = valid.reindex(h1).sum(axis=1).to_numpy()
    out["Y_L"] = (newly.reindex(h1).sum(axis=1).to_numpy() + newly.reindex(h2).sum(axis=1).to_numpy())
    out["mask_L"] = out["n_L"] > 0
    return out


# ---------- 빠른 보정(학습 기간) ----------
def _run_lengths(flags: pd.DataFrame) -> np.ndarray:
    """콘존별 연속 혼잡 시간 길이."""
    lengths = []
    for col in flags.columns:
        v = flags[col].to_numpy(dtype=bool)
        if not v.any():
            continue
        d = np.diff(np.concatenate([[0], v.astype(int), [0]]))
        s, e = np.where(d == 1)[0], np.where(d == -1)[0]
        lengths += list(e - s)
    return np.asarray(lengths, float)


def calibrate(data: dict, obs: pd.DataFrame, road_fit: dict) -> dict:
    tr = (obs.index >= TRAIN[0]) & (obs.index < TRAIN[1])
    o = obs[tr]
    ev = F.container_events(data["cntl"])
    anc = F.anchorage_intervals(ev)
    npa = anc[anc["np_anchor"] & (anc["start"] >= TRAIN[0]) & (anc["start"] < TRAIN[1])]
    dur = ((npa["end"] - npa["start"]).dt.total_seconds() / 3600).dropna()
    gamma_S = 1.0 / float((dur[dur > DELAY_H] - DELAY_H).mean())
    rh = _road_hourly(data["road"], road_fit)
    rh = rh[(rh["t"] >= TRAIN[0]) & (rh["t"] < TRAIN[1])]
    flags = rh.pivot_table(index="t", columns="zone_id", values="cong", aggfunc="max").fillna(False).astype(bool)
    rl = _run_lengths(flags)
    gamma_L = 1.0 / float(rl.mean()) if len(rl) else 1.0
    N_S = float(o["n_S"].mean())
    N_L = float(len(road_fit["zones"]))
    xS = (o["H_S"] / o["n_S"].clip(lower=1)).to_numpy()
    xL = np.where(o["mask_L"], o["H_L"] / o["n_L"].clip(lower=1), np.nan)
    xL = pd.Series(xL).ffill().fillna(0).to_numpy()
    xi = o["xi_S"].to_numpy()
    # 발생률 회귀: Y(a)/(2·N·(1−x(a−2h))) ≈ ε + β·ξ·(x_i + m_ij x_j)  (NNLS, 계수는 모두 0 이상)
    def fit(Y, N, xi_, x_own, x_other, ok):
        r = Y[1:] / (2.0 * N * (1.0 - x_own[:-1]))
        A = np.column_stack([np.ones(len(r)), xi_[:-1] * x_own[:-1], xi_[:-1] * x_other[:-1]])
        k = ok[1:] & ok[:-1] & np.isfinite(r)
        coef, _ = nnls(A[k], r[k])
        eps, beta, bm = coef
        mu = 2.0 * N * (1.0 - x_own[:-1][k]) * (A[k] @ coef)
        Yk = Y[1:][k]
        inv_r = max(float(((Yk - mu) ** 2 - mu).sum() / max((mu ** 2).sum(), 1e-9)), 1e-3)
        return float(eps), float(beta), (float(bm / beta) if beta > 1e-9 else 0.0), float(min(max(1.0 / inv_r, 0.5), 200.0))
    okS = np.ones(len(o), bool)
    okL = o["mask_L"].to_numpy()
    eS, bS, mSL, rS = fit(o["Y_S"].to_numpy(float), N_S, xi, xS, xL, okS)
    eL, bL, mLS, rL = fit(o["Y_L"].to_numpy(float), N_L, np.ones(len(o)), xL, xS, okL)
    # 대기열(학습 기간): 선석 수 = 동시 접안 척수 95분위, μ0 = 1/평균 선석 체류(상위 1% 자름), 도착률 = 요일·시각 평균
    brt = F.berth_intervals(ev)
    b = brt[(brt["start"] >= TRAIN[0]) & (brt["start"] < TRAIN[1])]
    stay = ((b["end"] - b["start"]).dt.total_seconds() / 3600).dropna()
    stay = stay[stay <= stay.quantile(0.99)]
    arr = F.arrivals(ev)
    an = arr[arr["np"] & (arr["t"] >= TRAIN[0]) & (arr["t"] < TRAIN[1])]
    hours = pd.date_range(TRAIN[0], pd.Timestamp(TRAIN[1]) - pd.Timedelta(hours=1), freq="h")
    cnt = an.groupby(an["t"].dt.floor("h")).size().reindex(hours, fill_value=0)
    prof = cnt.groupby([cnt.index.day_of_week, cnt.index.hour]).mean()
    xS_q90 = float(np.quantile(xS, 0.9))
    return {
        "nodes": ["S", "L"], "N": [N_S, N_L], "gamma": [gamma_S, gamma_L], "eps": [eS, eL], "beta_bar": [bS, bL],
        "coupling": {"S<-L": mSL, "L<-S": mLS}, "nb_size_S_L": [rS, rL], "nb_size": float(np.sqrt(rS * rL)),
        "x_mean_train": [float(xS.mean()), float(np.nanmean(xL))], "x_S_q90": xS_q90,
        "queue": {"servers": int(np.ceil(np.percentile(o["berthed_np"], 95))), "mu0": float(1.0 / stay.mean()),
                  "arrival_mean_per_h": float(cnt.mean()), "eta_b": 0.8, "cap": 60,
                  "arrival_profile": {f"{d}-{h}": float(v) for (d, h), v in prof.items()}},
        "delay_h": DELAY_H, "ou_rate": 0.25, "ou_sigma": 0.15, "filter_sigma_mult": 1.3, "rejuvenate_sd": 0.10,
        "snap_conc": 40.0, "report_rate": 1.0, "train": list(TRAIN), "road_zones": len(road_fit["zones"]),
    }


# ---------- τ 하나 ----------
_G: dict = {}


def _init(obs: pd.DataFrame, params: dict, zbar: pd.DataFrame | None):
    _G["obs"] = obs
    _G["params"] = params
    _G["zbar"] = zbar
    _G["t_index"] = {t: i for i, t in enumerate(obs.index)}
    m = params["coupling"]
    _G["C"] = np.array([[1.0, m["S<-L"]], [m["L<-S"], 1.0]])
    prof = params["queue"]["arrival_profile"]
    _G["prof"] = np.array([[prof.get(f"{d}-{h}", params["queue"]["arrival_mean_per_h"]) for h in range(24)] for d in range(7)])


def _p(params: dict, particles: int) -> dict:
    return {"beta_bar": params["beta_bar"], "gamma": params["gamma"], "eps": params["eps"], "ou_rate": params["ou_rate"],
            "ou_sigma": params["ou_sigma"], "nb_size": np.asarray(params["nb_size_S_L"]), "report_rate": params["report_rate"],
            "snap_conc": params["snap_conc"], "particles": particles, "filter_sigma_mult": params["filter_sigma_mult"],
            "rejuvenate_sd": params["rejuvenate_sd"], "dt": DT, "smooth_lag_h": 6.0, "snap_n": [1, 1]}


def queue_score(xS_paths: np.ndarray, w: np.ndarray, tau: pd.Timestamp, n0: int, qp: dict, prof: np.ndarray, rng, K: int = 48) -> dict:
    """(τ, τ+24h]에 도착할 배 중 12시간 넘게 기다릴 배가 1척 이상일 확률(대기열 근사)."""
    idx = stats.systematic_resample(w / w.sum(), rng)[:: max(1, len(w) // K)][:K]
    x = xS_paths[idx]  # (K, T)
    t = np.round(np.arange(0.0, 24.0 + 1e-9, DT), 6)
    m, cap = qp["servers"], qp["cap"]
    mu = qp["mu0"] * np.exp(-qp["eta_b"] * x)
    hrs = tau + pd.to_timedelta(t, unit="h")
    a = prof[hrs.day_of_week, hrs.hour]
    q = np.zeros((len(idx), cap + 2))
    q[:, min(n0, cap)] = 1.0
    E = 0.0
    p_each = []
    states = np.arange(cap + 2)
    need = np.maximum(states - m + 1, 0)
    for s in range(len(t) - 1):
        q = Q.kfe_step(q, np.full(len(idx), a[s]), mu[:, s], m, DT)
        if (s + 1) % 6 == 3:  # 매시 반(0.5, 1.5, …시간)에 도착하는 배
            j = s + 1
            mu12 = mu[:, j: j + 72].mean(axis=1) if j + 1 < mu.shape[1] else mu[:, -1]
            lam = 12.0 * m * mu12  # 12시간 동안 끝나는 작업 수의 평균
            pw = np.where(need[None, :] > 0, 1.0 - gammainc(np.maximum(need[None, :], 1), lam[:, None]), 0.0)
            pw[:, -1] = 1.0  # 넘침 상태
            pr = float((q * pw).sum(axis=1).mean())
            p_each.append(pr)
            E += a[j] * 1.0 * pr
    out = {"q_any": float(1.0 - np.exp(-E)), "q_exp": float(E), "q_max": float(max(p_each) if p_each else 0.0)}
    # 선박 단위(T2)용: τ+h+0.5시간에 도착하는 배가 12시간 넘게 기다릴 확률
    out.update({f"q_h{h:02d}": float(v) for h, v in enumerate(p_each)})
    return out


def _setup(tau: pd.Timestamp, W: int):
    """τ 하나의 필터 입력(run_tau·attribution_tau 공통). τ까지의 관측이 빠지면 None."""
    obs, params = _G["obs"], _G["params"]
    t_hist = np.round(np.arange(-W, 1e-9, DT), 6)
    t_fore = np.round(np.arange(0.0, 24.0 + 1e-9, DT), 6)
    t_all = np.concatenate([t_hist, t_fore[1:]])
    every = int(round(2.0 / DT))
    obs_idx = list(range(every, len(t_hist), every))
    a_times = tau + pd.to_timedelta(t_hist[obs_idx], unit="h")
    o = obs.reindex(a_times)
    if o["n_S"].isna().any():
        return None
    Y = o[["Y_S", "Y_L"]].to_numpy(float).astype(int)
    Hs = o[["H_S", "H_L"]].to_numpy(float).astype(int)
    snap = np.column_stack([o["n_S"].to_numpy(float), o["n_L"].to_numpy(float)]).astype(int)
    mask = np.column_stack([o["n_S"].to_numpy() > 0, o["mask_L"].to_numpy(bool)])
    # ξ: 2시간 값을 그 구간에 그대로, τ 뒤(예측)는 τ의 값을 유지
    abs_t = tau + pd.to_timedelta(t_all, unit="h")
    xi_idx = pd.DatetimeIndex(np.minimum(abs_t.floor("2h").to_numpy(), np.datetime64(tau)))
    xi_S = obs["xi_S"].reindex(xi_idx).fillna(1.0).to_numpy()
    xi = np.column_stack([xi_S, np.ones(len(t_all))])
    zb = np.zeros((len(t_all), 2))
    if _G.get("zbar") is not None:
        zz = _G["zbar"]["zbar_S"].reindex(xi_idx).fillna(0.0).to_numpy()
        zb[:, 0] = zz
    s0 = obs.reindex([tau - pd.Timedelta(hours=W)])
    x0S = float(np.clip(s0["H_S"].iloc[0] / max(s0["n_S"].iloc[0], 1), 0.02, 0.98)) if s0["n_S"].notna().all() else params["x_mean_train"][0]
    x0L = float(np.clip(s0["H_L"].iloc[0] / max(s0["n_L"].iloc[0], 1), 0.01, 0.98)) if (s0["n_L"].fillna(0) > 0).all() else max(params["x_mean_train"][1], 0.01)
    inp = {"t_hist": t_hist, "t_fore": t_fore, "t_all": t_all, "xi": xi, "zbar_ai": zb, "capacity": np.ones((len(t_all), 2)),
           "N": params["N"], "x0": [x0S, x0L], "snap_n": snap, "obs_mask": mask}
    return inp, obs_idx, Y, Hs


def run_tau(args) -> dict:
    tau, W, particles, seed = args
    obs, params = _G["obs"], _G["params"]
    tau = pd.Timestamp(tau)
    st = _setup(tau, W)
    if st is None:
        return {"tau": tau, "ok": False}
    inp, obs_idx, Y, Hs = st
    p = _p(params, particles)
    rng = np.random.default_rng([seed, int(tau.value // 10**9)])
    C = _G["C"]
    pf = engine.particle_filter(inp, obs_idx, Y, Hs, p, C, rng)
    fc = engine.forecast(pf, inp, p, C, rng)
    w = fc["w"]
    xs = fc["x"][:, :, 0]
    thr = params["x_S_q90"]
    p_cong = float(w @ (xs[:, 1:].max(axis=1) > thr))
    rdq = np.asarray(fc["rd_q"])
    n0 = int(obs.loc[tau, "n_S"])
    qs = queue_score(xs, w, tau, n0, params["queue"], _G["prof"], rng)
    post = pf["post"]
    return {"tau": tau, "ok": True, "p_rd1": float(pf["filter"]["rd_p1"][-1]), "rd_mean": float(pf["filter"]["rd_mean"][-1]),
            "p_rd1_fore_max": float(rdq[:, 3].max()), "x_S": float(pf["filter"]["x_mean"][-1, 0]), "x_L": float(pf["filter"]["x_mean"][-1, 1]),
            "z_S": float(post["w"] @ post["z"][:, 0]), "z_L": float(post["w"] @ post["z"][:, 1]), "p_cong": p_cong,
            "x_S_fore_mean_max": float((w[:, None] * xs).sum(axis=0).max()), "ess": float(post["ess"]),
            "z_S_smooth_lag": float(pf["smooth"]["z"][len(inp["t_hist"]) - 1 - int(round(6 / DT)), 0]),
            **qs}


def attribution_tau(args) -> dict:
    """앱의 원인 판정(docs/py/rdelay/api.py '요인별 반사실')과 같은 계산을 실자료 판단 시각에 한다.

    판단 시각의 가중 입자(재표본추출 전)에서 R_d = ρ(diag(ξ β (1 − x)) C Γ⁻¹)를 구하고, 한 요인씩 평시 값으로 되돌린다:
      물동량 보정 제거 ξ = 1 · 전파율 상승 제거 z = 0(β = β̄) · 처리능력 저하 제거는 실자료에 공지 처리능력 자료가 없어(1로 둠) 계산하지 않는다.
    rd_zmean(z를 사후 평균 하나로 고정)을 함께 내어 z 효과를 '평균 이동'(rd_zmean − rd_noz)과 '불확실성 몫'(rd_now − rd_zmean)으로 나눈다.
    같은 seed·같은 순서로 난수를 쓰므로 사후 입자는 run_tau와 같다(rd_now = run_tau의 rd_mean)."""
    tau, W, particles, seed = args
    params = _G["params"]
    tau = pd.Timestamp(tau)
    st = _setup(tau, W)
    if st is None:
        return {"tau": tau, "ok": False}
    inp, obs_idx, Y, Hs = st
    p = _p(params, particles)
    rng = np.random.default_rng([seed, int(tau.value // 10**9)])
    C = _G["C"]
    pf = engine.particle_filter(inp, obs_idx, Y, Hs, p, C, rng)
    post = pf["post"]
    x0, z0, w0 = post["x"], post["z"], post["w"]
    off = len(inp["t_hist"]) - 1
    xi0 = inp["xi"][off]
    bb = np.asarray(p["beta_bar"])
    g = np.asarray(p["gamma"]) * inp["capacity"][off]
    beta0 = bb * np.exp(z0)
    r_now = sis.spectral_radius(sis.next_gen(x0, xi0, beta0, g, C))
    r_vol = sis.spectral_radius(sis.next_gen(x0, np.ones_like(xi0), beta0, g, C))
    r_beta = sis.spectral_radius(sis.next_gen(x0, xi0, np.broadcast_to(bb, x0.shape), g, C))
    # z = 0 반사실의 차이는 'z 평균이 평시보다 높음'과 'z가 불확실해서 생기는 몫(옌센 부등식)'이 섞여 있다 → z를 사후 평균으로 고정한 값도 함께
    r_zmean = sis.spectral_radius(sis.next_gen(x0, xi0, np.broadcast_to(bb * np.exp(w0 @ z0), x0.shape), g, C))
    return {"tau": tau, "ok": True, "rd_now": float(w0 @ r_now), "rd_noxi": float(w0 @ r_vol), "rd_noz": float(w0 @ r_beta),
            "rd_zmean": float(w0 @ r_zmean), "p_rd1": float(w0 @ (r_now > 1.0)), "x_S": float(w0 @ x0[:, 0]),
            "xi_S": float(xi0[0]), "z_S": float(w0 @ z0[:, 0]), "z_L": float(w0 @ z0[:, 1])}


def run_many(taus, W: int, particles: int, jobs: int, obs, params, zbar=None, seed: int = 20261108, fn=None) -> pd.DataFrame:
    tasks = [(t, W, particles, seed) for t in taus]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init, initargs=(obs, params, zbar)) as ex:
        rows = list(ex.map(fn or run_tau, tasks, chunksize=16))
    df = pd.DataFrame(rows).set_index("tau")
    df.attrs["seconds"] = round(time.time() - t0, 1)
    return df


def load_inputs(rebuild: bool = False):
    """관측 표(rdelay_obs.parquet)는 캐시한다. 자료가 늘어 마지막 판단 시각이 캐시보다 뒤면, 또는 rebuild면 다시 만든다."""
    data = {n: load(n) for n in ("cntl", "road")}
    road_fit = F.fit_road(load("road"))
    end = F.data_tau_end(load("voyages"))
    obs_path = CACHE / "rdelay_obs.parquet"
    obs = pd.read_parquet(obs_path) if (obs_path.exists() and not rebuild) else None
    if obs is None or obs.index.max() < end:
        obs = build_obs(data, road_fit, end)
        obs.to_parquet(obs_path)
    return data, road_fit, obs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["calibrate", "run"])
    ap.add_argument("--period", default="val", choices=list(PERIODS) + ["all"])
    ap.add_argument("--window", type=int, default=24)
    ap.add_argument("--particles", type=int, default=512)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--zbar", default="", help="f_ω 사전평균 parquet(없으면 0)")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    data, road_fit, obs = load_inputs()
    if a.cmd == "calibrate":
        params = calibrate(data, obs, road_fit)
        RESULTS.mkdir(parents=True, exist_ok=True)
        PARAMS_PATH.write_text(json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in params.items() if k != "queue"}, ensure_ascii=False, indent=1))
        print("queue:", {k: v for k, v in params["queue"].items() if k != "arrival_profile"})
        return
    params = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
    tau_end = F.data_tau_end(load("voyages"))
    if a.period == "all":
        lo, hi = pd.Timestamp("2023-01-08"), tau_end
    else:
        lo, hi = pd.Timestamp(PERIODS[a.period][0]), min(pd.Timestamp(PERIODS[a.period][1]) - pd.Timedelta(hours=2), tau_end)
    taus = [t for t in obs.index if lo <= t <= hi and t - pd.Timedelta(hours=a.window) >= obs.index[0]]
    if a.limit:
        taus = taus[: a.limit]
    zbar = pd.read_parquet(a.zbar) if a.zbar else None
    df = run_many(taus, a.window, a.particles, a.jobs, obs, params, zbar)
    tag = f"rdelay_{a.period}_W{a.window}_P{a.particles}{'_ai' if a.zbar else ''}"
    df.to_parquet(CACHE / f"{tag}.parquet")
    print(f"{tag}: τ {len(df)} · {df.attrs['seconds']}초 · ok {int(df['ok'].sum())}")


if __name__ == "__main__":
    main()
