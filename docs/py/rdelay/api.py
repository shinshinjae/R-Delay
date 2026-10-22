"""웹·스크립트 공용 진입점: run(options) -> JSON으로 바꿀 수 있는 dict.

한 번 실행에 4종 화물을 모두 평가한다(경보·원인은 거점 공통이고, 화물마다 경로·마감만 다르다).
난수는 둘로 나눈다. world: 합성 세계(물동량·참 경로·관측), seed: 추정(입자필터·예측·대기열·화물 표본).
검증용 선택지: mask_notices(운영자 공지를 추정에서 가림), misspec(추정에 쓰는 정적 모수를 일부러 틀리게 줌).
"""
from __future__ import annotations
import copy
import numpy as np
from . import __version__, config, sis, engine, queue, cargo, stats, ai

STRIDE = 3  # 화면 표시는 30분 간격
CARGO_ORDER = list(config.CARGO)
FACTOR_LABELS = {"volume": "물량", "capacity": "처리능력 저하", "beta": "기상·장비"}


def _r(a, nd=4):
    a = np.asarray(a, dtype=float)
    return np.where(np.isfinite(a), np.round(a, nd), None).tolist()


def _per_cargo(value, selected, name):
    """숫자 하나면 선택 화물에만, dict면 화물별로 적용."""
    if value is None:
        return {}
    if isinstance(value, dict):
        bad = [k for k in value if k not in config.CARGO]
        if bad:
            raise ValueError(f"unknown cargo in {name}: {bad}")
        return {k: float(v) for k, v in value.items()}
    return {selected: float(value)}


def params_from(overrides: dict | None) -> dict:
    p = dict(config.DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in p:
            p[k] = type(p[k])(v) if not isinstance(p[k], list) else list(v)
    if p["particles"] not in (64, 128, 256, 512, 1024):
        raise ValueError("particles must be one of 64, 128, 256, 512, 1024")
    if not 0.0 <= p["kappa"] <= 3.0:
        raise ValueError("kappa must be in [0, 3]")
    if p["world"] < 1 or p["seed"] < 1:
        raise ValueError("world and seed must be positive integers")
    return p


def classify(p_spread, p_cond, band=(0.35, 0.65)):
    """계획서 부록 (A7)의 배타적 분류에 두 단계 판정 보류를 더한 규칙.

    1단계: 확산 확률 P(R_d > 1)이 보류 구간 안이면 '확산 경계'(경보는 0.5 기준으로 울릴 수 있지만 원인은 판정하지 않음).
    2단계: 확산이 확실할 때 조건부 확률 P(R_d^(xi=1) > 1 | R_d > 1)로 물량/처리능력을 가르고,
           그 확률이 보류 구간 안이면 '원인 보류'.
    """
    if p_spread < band[0]:
        return "비확산"
    if p_spread <= band[1]:
        return "확산 경계"
    if p_cond < band[0]:
        return "물량 주도형"
    if p_cond > band[1]:
        return "처리능력형"
    return "원인 보류"


def classify_truth(rd, rd_noxi):
    if rd <= 1.0:
        return "비확산"
    return "물량 주도형" if rd_noxi <= 1.0 else "처리능력형"


def _wq(values, w, probs):
    return [float(v) for v in stats.weighted_quantile(values, w, probs)]


def _mask_notices(inp, ai_model):
    """운영자 공지(선석 가동·장비·출근율, 장비 고장 특징)를 추정에서 지운 입력. 참 경로에는 쓰지 않는다."""
    m = dict(inp)
    ones = np.ones_like(inp["capacity"])
    for key in ("capacity", "berth_open", "mu_mult", "staffing"):
        m[key] = ones.copy()
    feats = inp["features"].copy()
    feats[:, :, ai.FEATURES.index("equip")] = 0.0
    m["features"] = feats
    m["zbar_ai"] = ai.predict(ai_model, feats)
    m["gate_closed"] = np.zeros_like(inp["gate_closed"])
    return m


def run(options: dict | None = None, ai_model: dict | None = None) -> dict:
    options = dict(options or {})
    scen_name = options.pop("scenario", "base")
    selected = options.pop("cargo", CARGO_ORDER[0])
    if selected not in config.CARGO:
        raise ValueError(f"unknown cargo: {selected}")
    eps_over = _per_cargo(options.pop("epsilon", None), selected, "epsilon")
    cost_over = _per_cargo(options.pop("fail_cost", None), selected, "fail_cost")
    use_h2 = options.pop("h2", None)  # None이면 시나리오 기본값, 참/거짓이면 켜고 끈다
    mask = bool(options.pop("mask_notices", False))
    misspec = dict(options.pop("misspec", None) or {})
    for v in eps_over.values():
        if not 0.0 < v < 0.5:
            raise ValueError("epsilon must be in (0, 0.5)")
    for v in cost_over.values():
        if v < 0:
            raise ValueError("fail_cost must be nonnegative")
    p = params_from(options)
    scen = config.scenario(scen_name)
    if use_h2 is not None:
        scen["h2"] = bool(use_h2)
    hidden = []  # 추정이 모르는 참 변화(검증 설명용)
    if any(scen.get("beta_jump", {}).values()):
        hidden.append("숨은 전파율 상승")
    if any(scen.get("gamma_mult", {}).values()):
        hidden.append("공지되지 않은 생산성 저하")

    # 추정에 쓰는 모수(p_f)는 참 세계 모수(p)와 같다. 검증에서만 일부러 틀리게 줄 수 있다.
    p_f = copy.deepcopy(p)
    for key, factor in misspec.items():
        if key in ("beta_bar", "gamma", "eps"):
            p_f[key] = [v * float(factor) for v in p_f[key]]
        elif key == "kappa":
            p_f["kappa"] = p_f["kappa"] * float(factor)
        else:
            raise ValueError(f"unknown misspec key: {key}")

    rng_vol, rng_truth, rng_obs = (np.random.default_rng(s) for s in np.random.SeedSequence(int(p["world"])).spawn(3))
    rng_pf, rng_fc, rng_q = (np.random.default_rng(s) for s in np.random.SeedSequence(int(p["seed"])).spawn(3))
    C = sis.coupling(p["kappa"], p["tau0"])
    C_f = sis.coupling(p_f["kappa"], p_f["tau0"])
    inp = engine.build_inputs(scen, p, ai_model, rng_vol)
    truth = engine.simulate_truth(inp, p, C, rng_truth)
    obs_idx, Y, Hs = engine.observe(truth, inp, p, rng_obs)
    inp_f = _mask_notices(inp, ai_model) if mask else inp
    pf = engine.particle_filter(inp_f, obs_idx, Y, Hs, p_f, C_f, rng_pf)
    fc = engine.forecast(pf, inp_f, p_f, C_f, rng_fc)

    nodes = config.NODES
    n = len(nodes)
    N = np.array([nd["N"] for nd in nodes], dtype=float)
    th, tf = inp["t_hist"], inp["t_fore"]
    off = len(th) - 1
    beta_bar = np.asarray(p_f["beta_bar"])
    gamma_base = np.asarray(p_f["gamma"])
    cap0 = inp_f["capacity"][off]
    gamma = gamma_base * cap0  # 공지된 처리능력 반영

    # 판단 시각의 가중 입자(재표본추출 전)
    post = pf["post"]
    x0, z0, w0 = post["x"], post["z"], post["w"]
    xi0 = inp["xi"][off]
    beta0 = beta_bar * np.exp(z0)
    K0 = sis.next_gen(x0, xi0, beta0, gamma, C_f)
    rd0 = sis.spectral_radius(K0)
    spread = rd0 > 1.0
    p_spread = float(w0 @ spread)
    x_mean = w0 @ x0
    thr = np.asarray(p["congestion_threshold"])
    congested = [nodes[k]["short"] for k in range(n) if x_mean[k] > thr[k]]
    band = tuple(p["hold_band"])

    def cond_p(r_cf):
        """확산 중인 입자들 안에서 반사실 R_d가 1을 넘는 확률 P(R_cf > 1 | R_d > 1)."""
        mass = float(w0 @ spread)
        return float(w0 @ (spread & (r_cf > 1.0)) / mass) if mass > 1e-12 else 0.0

    decomp = {}
    for theta in (0.0, 0.5, 1.0):
        scale = inp["ratio"] ** (-theta)            # beta_theta = beta_0 * ratio^-theta (적합 불변)
        r_nx = sis.spectral_radius(sis.next_gen(x0, np.ones(n), beta0 * scale, gamma, C_f))
        pc = cond_p(r_nx)
        decomp[str(theta)] = {"rd_noxi_median": round(_wq(r_nx, w0, [0.5])[0], 3),
                              "p_noxi_gt1": round(float(w0 @ (r_nx > 1.0)), 3), "p_noxi_given_spread": round(pc, 3),
                              "label": classify(p_spread, pc, band)}
    labels = {d["label"] for d in decomp.values()}
    main_label = decomp["0.0"]["label"]
    cause = main_label if len(labels) == 1 else f"{main_label}(θ에 따라 달라짐: 혼합)"

    # 요인별 반사실: 한 요인씩 평시 값으로 되돌렸을 때의 R_d
    cf = {
        "volume": sis.spectral_radius(sis.next_gen(x0, np.ones(n), beta0, gamma, C_f)),
        "capacity": sis.spectral_radius(sis.next_gen(x0, xi0, beta0, gamma_base, C_f)),
        "beta": sis.spectral_radius(sis.next_gen(x0, xi0, np.broadcast_to(beta_bar, x0.shape), gamma, C_f)),
    }

    def summary(r):
        q = _wq(r, w0, [0.1, 0.5, 0.9])
        return {"q10": round(q[0], 3), "median": round(q[1], 3), "q90": round(q[2], 3),
                "p_gt1": round(float(w0 @ (r > 1.0)), 3), "p_given_spread": round(cond_p(r), 3)}
    factors = [{"key": "now", "label": "현재", "what": "모든 요인 포함", **summary(rd0)},
               {"key": "volume", "label": "물량 보정 제거", "what": "ξ = 1 (평시 물동량)", **summary(cf["volume"])},
               {"key": "capacity", "label": "처리능력 저하 제거", "what": "공지 처리능력 100%(선석·장비·출근율)", **summary(cf["capacity"])},
               {"key": "beta", "label": "전파율 상승 제거", "what": "z = 0 (기상·장비·돌발 충격 없음)", **summary(cf["beta"])}]
    # 혼자서도 연쇄를 멈추는 요인(확산 중 입자 기준 P < 보류 하한)
    cofactors = [FACTOR_LABELS[k] for k in ("volume", "capacity", "beta")
                 if p_spread > band[1] and cond_p(cf[k]) < band[0]]
    # 적체만 있을 때: R_d를 가장 크게 낮추는 요인
    drop = {k: float(w0 @ rd0) - float(w0 @ cf[k]) for k in cf}
    main_factor = max(drop, key=drop.get)
    congestion_factor = FACTOR_LABELS[main_factor] if drop[main_factor] > 0.02 else None

    if p_spread >= 0.5 and congested:
        alarm = "확산·적체 경보"
    elif p_spread >= 0.5:
        alarm = "확산 경보"
    elif congested:
        alarm = "적체 경보"
    else:
        alarm = "정상"

    # 합성 참값 판정(검증용; 실제 운영에서는 알 수 없음)
    gamma_true = np.asarray(p["gamma"]) * inp["gamma_mult"][off] * inp["capacity"][off]
    beta_true = np.asarray(p["beta_bar"]) * np.exp(truth["z"][off])
    x_true = truth["x"][off][None, :]
    rd_true = float(sis.spectral_radius(sis.next_gen(x_true, xi0, beta_true, gamma_true, C))[0])
    rd_true_nx = float(sis.spectral_radius(sis.next_gen(x_true, np.ones(n), beta_true, gamma_true, C))[0])
    truth_now = {"rd": round(rd_true, 3), "rd_noxi": round(rd_true_nx, 3), "label": classify_truth(rd_true, rd_true_nx),
                 "x": _r(truth["x"][off], 3),
                 "congested": [nodes[k]["short"] for k in range(n) if truth["x"][off][k] > thr[k]]}

    Kbar = np.einsum("p,pij->ij", w0, K0)
    rho_bar, E = sis.elasticities(Kbar)
    shares = E.sum(axis=1)
    self_share = float(np.trace(E))
    edges = []
    for i in range(n):
        for j in range(n):
            if i != j and E[i, j] > 1e-4:
                edges.append({"from": nodes[j]["id"], "to": nodes[i]["id"], "e": round(float(E[i, j]), 4)})
    edges.sort(key=lambda e: -e["e"])
    amp = sis.amplification(Kbar, horizon=6, N=N)
    gen_hours = float(np.mean(1.0 / gamma))  # 한 세대 ≈ 평균 해소시간

    # 예측 경로(가중 입자)
    w_f = fc["w"]
    ess_f = fc["ess"]
    term = config.IDX["AT"]
    qcfg = config.QUEUE["AT"]
    icfg = config.CARGO["import_factory"]
    open_path = inp_f["berth_open"][off:, term]
    mu_mult = inp_f["mu_mult"][off:, term] * inp_f["staffing"][off:, term]
    mu = queue.service_rates(fc["x"][:, :, term], qcfg["mu0"], qcfg["eta_b"], open_path, mu_mult)
    a = qcfg["arrival"] * inp["arrival_mult"][off:, term]
    ship = queue.tagged_ship(tf, mu, a, qcfg["servers"], scen["n0"]["AT"], config.QUEUE["cap"],
                             icfg["ship_arrival"], 8, rng_q, priority_frac=icfg["priority"]["frac"])
    dist = w_f @ ship["dist"]
    wait = ship["wait"]
    p_wait12 = float(w_f @ np.mean(~(wait <= 12.0), axis=1))
    wait_q = stats.weighted_mean_quantiles(np.where(np.isfinite(wait), wait, np.inf), w_f, [0.5, 0.9])
    cap_t = inp_f["capacity"][off:, term]
    paths = {"L": fc["x"][:, :, config.IDX["L"]], "T": fc["x"][:, :, term], "A": fc["x"][:, :, config.IDX["A"]],
             "capT": cap_t}
    gate_closed = inp_f["gate_closed"][off:, term]
    cargo_out = {}
    for name in CARGO_ORDER:
        ccfg = config.CARGO[name]
        cargo_out[name] = cargo.evaluate(
            tf, ccfg, paths, w_f, ess_f, scen, eps_over.get(name, ccfg["epsilon"]), rng_q,
            ship=ship if ccfg["kind"] == "import" else None, fail_cost=cost_over.get(name),
            gate_closed=gate_closed if ccfg["kind"] == "export" else None)
    ranking = sorted(
        ({"cargo": k, "label": v["label"], "deadline_label": v["deadline_label"], "epsilon": v["epsilon"],
          "plan_p_fail": next(r["p_fail"] for r in v["candidates"] if r["is_plan"]),
          "plan_p_upper": next(r["p_upper"] for r in v["candidates"] if r["is_plan"]),
          "status": v["decision"]["status"], "choice": v["decision"]["choice"] or v["decision"].get("least"),
          "notice": v["notice"] is not None} for k, v in cargo_out.items()),
        key=lambda r: -r["plan_p_fail"])

    # 화물차 기사용: 도착 시각별 터미널 반출 소요(화물 준비 완료 가정, 공지 처리능력 반영)
    rate = cargo._rate([paths["L"], paths["T"]], [icfg["eta_l"], icfg["eta_y"]], icfg["h0"], cap_t[None, :])
    w_cum = queue.cumulative_hazard(tf, rate)
    gate = []
    for h in np.arange(0.0, 14.0 + 1e-9, 1.0):
        done = cargo._process(tf, w_cum, np.full((w_cum.shape[0], 1), h))[:, 0]
        dur = np.where(np.isfinite(done), done - h, np.inf)
        q = _wq(dur, w_f, [0.1, 0.5, 0.9])
        gate.append({"t": float(h), "q10": round(q[0], 2), "q50": round(q[1], 2), "q90": round(q[2], 2)})
    best_med = min(g["q50"] for g in gate)
    for g in gate:
        g["recommended"] = g["q50"] <= best_med + 0.1
    gate_flat = all(g["recommended"] for g in gate)

    # 합성 복원 검증(참값을 아는 시나리오에서만 가능)
    rd_true_h = truth["rd"][: len(th)]
    f_mean = pf["filter"]["rd_mean"]
    sm = pf["smooth"]["rd"]
    ok = np.isfinite(sm)
    inside = (rd_true_h >= pf["filter"]["rd_q10"]) & (rd_true_h <= pf["filter"]["rd_q90"])
    last6 = th >= -6.0
    valid = {"rmse_filter": round(float(np.sqrt(np.mean((f_mean - rd_true_h) ** 2))), 4),
             "rmse_smooth": round(float(np.sqrt(np.mean((sm[ok] - rd_true_h[ok]) ** 2))), 4),
             "coverage_80": round(float(np.mean(inside)), 3), "coverage_80_last6h": round(float(np.mean(inside[last6])), 3),
             "inside_now": bool(_wq(rd0, w0, [0.1])[0] <= rd_true <= _wq(rd0, w0, [0.9])[0]),
             "ess_now": round(post["ess"], 1)}

    hs = slice(0, len(th), STRIDE)
    fs = slice(0, len(tf), STRIDE)
    xq = np.stack([stats.weighted_quantiles_rows(fc["x"][:, fs, k], w_f, [0.1, 0.5, 0.9]) for k in range(n)], axis=2)
    return {
        "meta": {"version": __version__, "scenario": scen_name, "scenario_label": scen["label"],
                 "scenario_summary": scen["summary"], "selected_cargo": selected, "params": p,
                 "synthetic": True, "now_label": "판단 시각 10:00", "particles": int(p["particles"]),
                 "world": int(p["world"]), "seed": int(p["seed"]), "h2": bool(scen.get("h2")),
                 "mask_notices": mask, "misspec": misspec, "hidden_shocks": hidden},
        "nodes": [{k: nd[k] for k in ("id", "name", "short", "domain")} for nd in nodes],
        "t_hist": _r(th[hs], 3), "t_fore": _r(tf[fs], 3),
        "xi": {"hist": _r(inp["xi"][: len(th)][hs]), "fore": _r(inp["xi"][off:][fs])},
        "x": {"filter_mean": _r(pf["filter"]["x_mean"][hs]), "filter_q10": _r(pf["filter"]["x_q10"][hs]),
              "filter_q90": _r(pf["filter"]["x_q90"][hs]), "smooth": _r(pf["smooth"]["x"][hs]),
              "truth": _r(truth["x"][: len(th)][hs]), "fore_q10": _r(xq[0]), "fore_q50": _r(xq[1]),
              "fore_q90": _r(xq[2]), "threshold": list(map(float, thr))},
        "rd": {"filter_mean": _r(f_mean[hs]), "filter_q10": _r(pf["filter"]["rd_q10"][hs]),
               "filter_q90": _r(pf["filter"]["rd_q90"][hs]), "p_gt1": _r(pf["filter"]["rd_p1"][hs]),
               "smooth": _r(sm[hs]), "truth": _r(rd_true_h[hs]), "truth_fore": _r(truth["rd"][off:][fs]),
               "fore_t": fc["rd_t"], "fore_q": _r(fc["rd_q"])},
        "observations": [dict(o, y=dict(zip([nd["id"] for nd in nodes], o["y"])),
                              h=dict(zip([nd["id"] for nd in nodes], o["h"]))) for o in pf["log"]],
        "snap_n": list(p["snap_n"]),
        "now": {"rd_median": round(_wq(rd0, w0, [0.5])[0], 3),
                "rd_q10": round(_wq(rd0, w0, [0.1])[0], 3),
                "rd_q90": round(_wq(rd0, w0, [0.9])[0], 3),
                "p_spread": round(p_spread, 3), "x_mean": _r(x_mean, 3), "congested": congested,
                "alarm": alarm, "cause": cause, "decomposition": decomp, "factors": factors, "hold_band": list(band),
                "cofactors": cofactors, "congestion_factor": congestion_factor,
                "ratio": _r(inp["ratio"], 3), "xi": _r(xi0, 3), "capacity": _r(cap0, 3), "ess": round(post["ess"], 1)},
        "truth_now": truth_now,
        "contrib": {"rho": round(rho_bar, 3), "node_share": _r(shares, 4), "self_share": round(self_share, 4),
                    "edges": edges[:8], "matrix": _r(E, 4), "amplification_to_air": _r(amp[config.IDX["A"]], 3),
                    "generation_hours": round(gen_hours, 2)},
        "queue": {"terminal": nodes[term]["name"], "dist": _r(dist[:21], 4),
                  "overflow": round(float(dist[-1]), 6), "p_wait_gt12": round(p_wait12, 3),
                  "wait_median_h": None if wait_q[0] is None else round(wait_q[0], 2),
                  "wait_q90_h": None if wait_q[1] is None else round(wait_q[1], 2),
                  "servers": qcfg["servers"], "ship_arrival": icfg["ship_arrival"]},
        "cargo": cargo_out,
        "cargo_order": CARGO_ORDER,
        "ranking": ranking,
        "gate": gate,
        "gate_flat": gate_flat,
        "validation": valid,
    }


def presets():
    return {"scenarios": {k: {"label": v["label"], "summary": v["summary"], "h2": bool(v.get("h2"))}
                          for k, v in config.SCENARIOS.items()},
            "cargo": {k: {"label": v["label"], "deadline_label": v["deadline_label"], "epsilon": v["epsilon"],
                          "fail_cost": v["fail_cost"], "kind": v["kind"]} for k, v in config.CARGO.items()},
            "defaults": {"kappa": config.DEFAULTS["kappa"], "particles": config.DEFAULTS["particles"],
                         "world": config.DEFAULTS["world"], "seed": config.DEFAULTS["seed"]}}
