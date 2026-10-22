"""합성 '참' 경로 생성, 음이항 관측, 입자필터(실시간)와 고정시차 평활(사후), 예측.

참 경로는 시연·검증용이다. 필터는 참 계수(전파율 충격, 해소율 변화)를 모르고 관측·물동량·AI 예측만 쓴다.
"""
from __future__ import annotations
import numpy as np
from . import config, sis, volume, ai, stats


def time_grids(p):
    dt = p["dt"]
    t_hist = np.round(np.arange(-p["history_h"], 1e-9, dt), 6)
    t_fore = np.round(np.arange(0.0, p["forecast_h"] + 1e-9, dt), 6)
    return t_hist, t_fore


def build_inputs(scen, p, ai_model, rng_vol):
    t_hist, t_fore = time_grids(p)
    dt = p["dt"]
    t_all = np.concatenate([t_hist, t_fore[1:]])
    t_past = np.round(np.arange(-p["history_h"] - p["window_h"], -p["history_h"] - 1e-9, dt), 6)
    X_all = volume.synthetic_volume(np.concatenate([t_past, t_all]), scen, rng_vol)
    X_past, X_now = X_all[: len(t_past)], X_all[len(t_past):]
    xi_all, ratio = volume.xi_series(t_all, X_now, theta=0.0, window_h=p["window_h"],
                                     past_X=X_past, past_t=t_past)
    feats = ai.feature_series(t_all, scen)
    n = len(config.NODES)
    berth_open = np.ones((len(t_all), n))
    mu_mult = np.ones((len(t_all), n))
    arr_mult = np.ones((len(t_all), n))
    gamma_mult = np.ones((len(t_all), n))
    staffing = np.ones((len(t_all), n))
    gate_closed = np.zeros((len(t_all), n))
    beta_jump = np.zeros((len(t_all), n))
    for k, node in enumerate(config.NODES):
        nid = node["id"]
        berth_open[:, k] = config.shock_value(scen["berth_open"].get(nid), t_all)
        mu_mult[:, k] = config.shock_value(scen["mu_mult"].get(nid), t_all)
        arr_mult[:, k] = config.shock_value(scen["arrival_mult"].get(nid), t_all)
        gamma_mult[:, k] = config.shock_value(scen["gamma_mult"].get(nid), t_all)
        staffing[:, k] = config.shock_value(scen.get("staffing", {}).get(nid), t_all)
        gate_closed[:, k] = config.shock_value(scen.get("gate_closed", {}).get(nid), t_all, 0.0)
        beta_jump[:, k] = config.shock_value(scen["beta_jump"].get(nid), t_all, 0.0)
    return {
        "t_hist": t_hist, "t_fore": t_fore, "t_all": t_all, "X": X_now, "xi": xi_all,
        "ratio": ratio, "features": feats, "zbar_ai": ai.predict(ai_model, feats),
        "zbar_true": ai.true_zbar(feats) + beta_jump, "berth_open": berth_open, "mu_mult": mu_mult,
        "arrival_mult": arr_mult, "gamma_mult": gamma_mult, "staffing": staffing, "gate_closed": gate_closed,
        # 운영자가 아는 처리능력 비율(선석 가동·장비·출근율). 필터의 입력이며 추정하지 않는다.
        # gamma_mult는 공지되지 않은 잔여 해소율 변화로, 참 경로에만 쓰인다.
        "capacity": berth_open * mu_mult * staffing,
    }


_BASELINE_CACHE = {}


def baseline_state(p, C):
    """평시 평형 x*(xi=1, z=0)를 적분으로 구한다(같은 모수면 다시 계산하지 않는다)."""
    key = (tuple(p["beta_bar"]), tuple(p["gamma"]), tuple(p["eps"]), C.tobytes())
    if key not in _BASELINE_CACHE:
        beta = np.asarray(p["beta_bar"])
        gamma = np.asarray(p["gamma"])
        eps = np.asarray(p["eps"])
        x = np.full((1, len(beta)), 0.05)
        for _ in range(6000):
            x = sis.heun_step(x, np.ones_like(beta), beta, gamma, eps, C, 0.05)
        _BASELINE_CACHE[key] = x[0].copy()
    return _BASELINE_CACHE[key].copy()


def ou_step(z, mean, rate, sigma, dt, rng):
    decay = np.exp(-rate * dt)
    sd = sigma * np.sqrt(-np.expm1(-2 * rate * dt) / (2 * rate))
    return mean + (z - mean) * decay + sd * rng.standard_normal(z.shape)


def simulate_truth(inp, p, C, rng):
    t = inp["t_all"]
    n = len(config.NODES)
    N = np.array([nd["N"] for nd in config.NODES], dtype=float)
    beta_bar = np.asarray(p["beta_bar"])
    gamma0 = np.asarray(p["gamma"])
    eps = np.asarray(p["eps"])
    x = baseline_state(p, C)[None, :]
    z = inp["zbar_true"][0][None, :].copy()
    xs = np.zeros((len(t), n))
    zs = np.zeros((len(t), n))
    rates = np.zeros((len(t), n))
    xs[0], zs[0] = x[0], z[0]
    dt = p["dt"]
    cap = inp.get("capacity", np.ones((len(t), n)))
    for s in range(len(t) - 1):
        gamma = gamma0 * inp["gamma_mult"][s] * cap[s]
        beta = beta_bar * np.exp(z)
        rates[s] = sis.new_delay_rate(x, inp["xi"][s], beta, eps, C, N)[0]
        x = sis.heun_step(x, inp["xi"][s], beta, gamma, eps, C, dt)
        z = ou_step(z, inp["zbar_true"][s + 1][None, :], p["ou_rate"], p["ou_sigma"], dt, rng)
        xs[s + 1], zs[s + 1] = x[0], z[0]
    beta = beta_bar * np.exp(zs[-1])
    rates[-1] = sis.new_delay_rate(xs[-1:], inp["xi"][-1], beta, eps, C, N)[0]
    gam = gamma0[None, :] * inp["gamma_mult"] * cap  # 참 세계의 해소율은 시간가변일 수 있다
    K = (inp["xi"] * beta_bar * np.exp(zs) * (1 - xs))[:, :, None] * C[None] / gam[:, None, :]
    rd = sis.spectral_radius(K)
    return {"x": xs, "z": zs, "rate": rates, "rd": rd}


def observe(truth, inp, p, rng):
    """관측 시각마다 (1) 직전 구간 신규 지연 건수 Y ~ NB(q*적분 발생률, r),
    (2) 현재 지연 표본 수 H ~ BetaBinomial(n, phi*x, phi*(1-x))(터미널·운송 현황 표본).
    두 관측은 서로 다른 자료원이며 입자 상태가 주어지면 조건부 독립으로 둔다."""
    t = inp["t_hist"]
    every = int(round(p["obs_every_h"] / p["dt"]))
    obs_idx = list(range(every, len(t), every))
    rate = truth["rate"][: len(t)]
    n_snap = np.asarray(p["snap_n"])
    ys, hs = [], []
    for k in obs_idx:
        seg = rate[k - every: k + 1]
        mean = p["report_rate"] * p["dt"] * (seg[:-1] + seg[1:]).sum(axis=0) / 2.0
        ys.append(stats.nb_sample(rng, mean, p["nb_size"]))
        hs.append(stats.betabinom_sample(rng, n_snap, truth["x"][k], p["snap_conc"]))
    return obs_idx, np.array(ys, dtype=int), np.array(hs, dtype=int)


def particle_filter(inp, obs_idx, Y, Hs, p, C, rng):
    P = int(p["particles"])
    t = inp["t_hist"]
    T = len(t)
    n = len(config.NODES)
    dt = p["dt"]
    N = np.array([nd["N"] for nd in config.NODES], dtype=float)
    beta_bar = np.asarray(p["beta_bar"])
    gamma = np.asarray(p["gamma"])
    eps = np.asarray(p["eps"])
    x0 = baseline_state(p, C)
    conc = 60.0
    x = rng.beta(conc * x0, conc * (1 - x0), size=(P, n))
    sd0 = p["ou_sigma"] / np.sqrt(2 * p["ou_rate"])
    z = inp["zbar_ai"][0][None, :] + sd0 * rng.standard_normal((P, n))
    w = np.full(P, 1.0 / P)
    traj_x = np.zeros((P, T, n))
    traj_z = np.zeros((P, T, n))
    traj_x[:, 0], traj_z[:, 0] = x, z
    traj_rd = np.zeros((P, T))  # 입자 경로별 R_d를 한 번만 계산해 평활에 다시 쓴다
    lag = int(round(p["smooth_lag_h"] / dt))
    obs_pos = {k: j for j, k in enumerate(obs_idx)}
    inc = np.zeros((P, n))
    sig_f = p["ou_sigma"] * p["filter_sigma_mult"]
    rj = p["rejuvenate_sd"]
    filt = {"x_mean": np.zeros((T, n)), "x_q10": np.zeros((T, n)), "x_q90": np.zeros((T, n)),
            "rd_mean": np.zeros(T), "rd_q10": np.zeros(T), "rd_q90": np.zeros(T), "rd_p1": np.zeros(T)}
    smooth = {"x": np.full((T, n), np.nan), "rd": np.full(T, np.nan), "z": np.full((T, n), np.nan)}
    log = []
    post = None

    cap = inp.get("capacity", np.ones((len(inp["t_all"]), n)))

    def gam(s):
        return gamma * cap[s]

    def rd_particles(xx, zz, s):
        K = sis.next_gen(xx, inp["xi"][s], beta_bar * np.exp(zz), gam(s), C)
        return sis.spectral_radius(K)

    def record_filter(s):
        filt["x_mean"][s] = w @ x
        for k in range(n):
            q = stats.weighted_quantile(x[:, k], w, [0.1, 0.9])
            filt["x_q10"][s, k], filt["x_q90"][s, k] = q
        r = rd_particles(x, z, s)
        traj_rd[:, s] = r
        filt["rd_mean"][s] = w @ r
        filt["rd_q10"][s], filt["rd_q90"][s] = stats.weighted_quantile(r, w, [0.1, 0.9])
        filt["rd_p1"][s] = float(w @ (r > 1.0))

    def record_smooth(s_target):
        smooth["x"][s_target] = w @ traj_x[:, s_target]
        smooth["z"][s_target] = w @ traj_z[:, s_target]
        smooth["rd"][s_target] = w @ traj_rd[:, s_target]

    record_filter(0)
    for s in range(T - 1):
        beta = beta_bar * np.exp(z)
        r0 = sis.new_delay_rate(x, inp["xi"][s], beta, eps, C, N)
        x = sis.heun_step(x, inp["xi"][s], beta, gam(s), eps, C, dt)
        z = ou_step(z, inp["zbar_ai"][s + 1][None, :], p["ou_rate"], sig_f, dt, rng)
        r1 = sis.new_delay_rate(x, inp["xi"][s + 1], beta_bar * np.exp(z), eps, C, N)
        inc += 0.5 * dt * (r0 + r1)
        traj_x[:, s + 1], traj_z[:, s + 1] = x, z
        if (s + 1) in obs_pos:
            y = Y[obs_pos[s + 1]]
            hh = Hs[obs_pos[s + 1]]
            ll = (stats.nb_logpmf(y[None, :], p["report_rate"] * inc, p["nb_size"]).sum(axis=1)
                  + stats.betabinom_logpmf(hh[None, :], np.asarray(p["snap_n"])[None, :], x,
                                           p["snap_conc"]).sum(axis=1))
            with np.errstate(divide="ignore"):
                w, evidence = stats.normalize_log_weights(np.log(w) + ll)
            e = stats.ess(w)
            resampled = e < P / 2
            log.append({"t": float(t[s + 1]), "y": y.tolist(), "h": hh.tolist(), "ess": round(e, 1),
                        "resampled": bool(resampled), "log_evidence": round(evidence, 3)})
            inc[:] = 0.0
            record_filter(s + 1)
            if s + 1 == T - 1:  # 판단 시각의 가중 입자(재표본추출·재생 잡음 전)를 그대로 보관한다
                post = {"x": x.copy(), "z": z.copy(), "w": w.copy(), "ess": float(e)}
            if resampled:
                idx = stats.systematic_resample(w, rng)
                x, z = x[idx], z[idx]
                traj_x, traj_z, traj_rd = traj_x[idx], traj_z[idx], traj_rd[idx]
                w = np.full(P, 1.0 / P)
                if rj > 0:  # 입자 재생: 같은 조상의 복제본을 로짓 공간에서 조금 흩뜨린다
                    lx = np.log(np.clip(x, 1e-6, 1 - 1e-6) / np.clip(1 - x, 1e-6, 1))
                    x = 1.0 / (1.0 + np.exp(-(lx + rj * rng.standard_normal(x.shape))))
                    z = z + rj * rng.standard_normal(z.shape)
                    traj_x[:, s + 1], traj_z[:, s + 1] = x, z
                    traj_rd[:, s + 1] = rd_particles(x, z, s + 1)
        else:
            record_filter(s + 1)
        if s + 1 - lag >= 0:
            record_smooth(s + 1 - lag)
    for s_target in range(max(0, T - lag), T):
        record_smooth(s_target)
    if post is None:
        post = {"x": x.copy(), "z": z.copy(), "w": w.copy(), "ess": float(stats.ess(w))}
    return {"filter": filt, "smooth": smooth, "log": log, "x": x, "z": z, "w": w, "post": post}


def forecast(pf, inp, p, C, rng):
    """판단 시각의 가중 입자(재표본추출 전)를 가중치를 유지한 채 예측 구간으로 진행한다.

    재표본추출을 하지 않으므로 예측 분위수·확률은 가중 추정치이며, 정보량은 ESS = 1/sum(w^2)로 잰다.
    """
    post = pf["post"]
    x, z, w = post["x"].copy(), post["z"].copy(), post["w"].copy()
    P = x.shape[0]
    t_f = inp["t_fore"]
    off = len(inp["t_hist"]) - 1
    n = len(config.NODES)
    beta_bar = np.asarray(p["beta_bar"])
    gamma = np.asarray(p["gamma"])
    eps = np.asarray(p["eps"])
    dt = p["dt"]
    xs = np.zeros((P, len(t_f), n))
    zs = np.zeros((P, len(t_f), n))
    xs[:, 0], zs[:, 0] = x, z
    cap = inp.get("capacity", np.ones((len(inp["t_all"]), n)))
    for s in range(len(t_f) - 1):
        g = off + s
        x = sis.heun_step(x, inp["xi"][g], beta_bar * np.exp(z), gamma * cap[g], eps, C, dt)
        z = ou_step(z, inp["zbar_ai"][g + 1][None, :], p["ou_rate"], p["ou_sigma"] * p["filter_sigma_mult"], dt, rng)
        xs[:, s + 1], zs[:, s + 1] = x, z
    stride = 3
    rd_t, rd_q = [], []
    for s in range(0, len(t_f), stride):
        K = sis.next_gen(xs[:, s], inp["xi"][off + s], beta_bar * np.exp(zs[:, s]), gamma * cap[off + s], C)
        r = sis.spectral_radius(K)
        rd_t.append(float(t_f[s]))
        rd_q.append([float(v) for v in stats.weighted_quantile(r, w, [0.1, 0.5, 0.9])] + [float(w @ (r > 1))])
    return {"x": xs, "z": zs, "w": w, "ess": float(stats.ess(w)), "rd_t": rd_t, "rd_q": rd_q}
