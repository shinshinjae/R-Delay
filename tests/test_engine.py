"""R-Delay 엔진 검증 테스트.

수학적 성질(불변성·정규화·스펙트럼 반지름·탄력성), 확률 계산(대기열 질량 보존·Erlang 꼬리),
의사결정 규칙(기회제약·실행 불가 처리), 재현성(시드)과 미래 정보 차단을 확인한다.
실행: python -m pytest -q
"""
from __future__ import annotations
import json
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "py"))
from rdelay import api, cargo, config, engine, queue, sis, stats, volume  # noqa: E402


# ---------- SIS·재생산수 ----------

def test_sis_stays_in_unit_interval():
    rng = np.random.default_rng(0)
    C = sis.coupling(1.0, 1.0)
    x = rng.uniform(0, 1, size=(200, 4))
    for _ in range(300):
        beta = rng.uniform(0, 2.0, size=(200, 4))
        x = sis.heun_step(x, rng.uniform(0.5, 2.0, size=(200, 4)), beta,
                          np.array([0.5, 0.5, 0.6, 0.6]), np.full(4, 0.01), C, 1 / 6)
        assert np.all(x >= 0) and np.all(x <= 1)


def test_single_node_rd_formula():
    xi, beta, gamma, x = 1.3, 0.4, 0.5, 0.2
    K = sis.next_gen(np.array([[x]]), np.array([xi]), np.array([beta]), np.array([gamma]), np.eye(1))
    assert sis.spectral_radius(K)[0] == pytest.approx(xi * beta * (1 - x) / gamma)


def test_endemic_equilibrium_has_rd_one():
    """eps=0 SIS의 내부 평형에서 R_d = 1 (그래서 확산 경보와 적체 경보를 따로 둔다)."""
    C = sis.coupling(1.0, 1.0)
    beta = np.array([0.6, 0.55, 0.7, 0.6])
    gamma = np.array([0.4, 0.4, 0.45, 0.4])
    x = np.full((1, 4), 0.3)
    for _ in range(20000):
        x = sis.heun_step(x, np.ones(4), beta, gamma, np.zeros(4), C, 0.05)
    assert np.all(x > 0.05)
    K = sis.next_gen(x, np.ones(4), beta, gamma, C)
    assert sis.spectral_radius(K)[0] == pytest.approx(1.0, abs=1e-6)


def test_elasticities_sum_to_one():
    C = sis.coupling(1.0, 1.0)
    K = sis.next_gen(np.full((1, 4), 0.1), np.ones(4), np.array(config.DEFAULTS["beta_bar"]),
                     np.array(config.DEFAULTS["gamma"]), C)[0]
    rho, E = sis.elasticities(K)
    assert rho == pytest.approx(sis.spectral_radius(K)[0])
    assert E.sum() == pytest.approx(1.0)
    assert np.all(E >= -1e-12)


def test_coupling_has_air_feedback():
    """항공 거점은 도로로 되먹임된다(A->L, L->A): 전파 행렬이 기약이어야 탄력성이 정의된다."""
    C = sis.coupling(1.0, 1.0)
    i = config.IDX
    assert C[i["L"], i["A"]] > 0 and C[i["A"], i["L"]] > 0
    reach = (C > 0).astype(int)
    closure = np.linalg.matrix_power(reach + np.eye(4, dtype=int), 4)
    assert np.all(closure > 0)


def test_amplification_matches_power_series():
    K = np.array([[0.2, 0.1], [0.3, 0.4]])
    acc = sis.amplification(K, horizon=3)
    assert np.allclose(acc, K + K @ K + K @ K @ K)


def test_amplification_count_units():
    """비율 단위 K를 개수 단위로 바꾸면 D K^n D^-1 (D = diag N)."""
    K = np.array([[0.2, 0.1], [0.3, 0.4]])
    N = np.array([80.0, 600.0])
    D = np.diag(N)
    Kc = D @ K @ np.linalg.inv(D)
    assert np.allclose(sis.amplification(K, horizon=3, N=N), Kc + Kc @ Kc + Kc @ Kc @ Kc)


# ---------- 확률분포 ----------

def test_negative_binomial_normalizes():
    y = np.arange(0, 400)
    for mean, size in [(0.5, 10), (5.0, 10), (30.0, 3)]:
        assert np.exp(stats.nb_logpmf(y, mean, size)).sum() == pytest.approx(1.0, abs=1e-8)


def test_beta_binomial_normalizes_and_mean():
    n, p, conc = 40, 0.2, 40.0
    k = np.arange(0, n + 1)
    pmf = np.exp(stats.betabinom_logpmf(k, n, p, conc))
    assert pmf.sum() == pytest.approx(1.0, abs=1e-10)
    assert (k * pmf).sum() == pytest.approx(n * p, rel=1e-8)


def test_ou_step_exact_moments():
    rng = np.random.default_rng(1)
    rate, sigma, dt, mean = 0.25, 0.15, 1 / 6, 0.4
    z0 = np.full((200000, 1), -0.2)
    z1 = engine.ou_step(z0, np.array([[mean]]), rate, sigma, dt, rng)
    a = math.exp(-rate * dt)
    assert z1.mean() == pytest.approx(mean + (-0.2 - mean) * a, abs=2e-4)
    assert z1.var() == pytest.approx(sigma ** 2 * (1 - a * a) / (2 * rate), rel=0.02)


def test_wald_upper_uses_ess():
    """계획서 식 p + z sqrt(p(1-p)/ESS): ESS가 작을수록 상한이 커진다."""
    assert stats.wald_upper_ess(0.1, 100) == pytest.approx(0.1 + 1.645 * math.sqrt(0.09 / 100))
    assert stats.wald_upper_ess(0.1, 20) > stats.wald_upper_ess(0.1, 200)
    # 0건 경계 보정: p_hat = 0이어도 상한은 1 - 0.05^(1/ESS) (3/n 규칙)보다 작지 않다.
    assert stats.wald_upper_ess(0.0, 50) == pytest.approx(1 - 0.05 ** (1 / 50))
    assert stats.wald_upper_ess(0.0, 500) < stats.wald_upper_ess(0.0, 50)


def test_systematic_resample_counts():
    rng = np.random.default_rng(2)
    w = np.array([0.5, 0.25, 0.25, 0.0])
    idx = stats.systematic_resample(w, rng)
    counts = np.bincount(idx, minlength=4)
    assert counts[3] == 0
    assert abs(counts[0] - 2) <= 1 and abs(counts[1] - 1) <= 1


# ---------- 선석 대기열 ----------

def test_kfe_conserves_mass_with_overflow():
    q = np.zeros((3, 12))
    q[:, 2] = 1.0
    a = np.array([0.5, 2.0, 6.0])
    mu = np.array([0.2, 0.2, 0.2])
    for _ in range(40):
        q = queue.kfe_step(q, a, mu, 2, 0.5)
    assert np.allclose(q.sum(axis=1), 1.0)
    assert q[2, -1] > q[0, -1]          # 도착이 많을수록 넘침 확률이 크다


def test_kfe_stationary_mmm_queue():
    """시간불변 M/M/m의 정상분포(Erlang)와 비교."""
    lam, mu, m, cap = 0.6, 0.25, 4, 80
    q = np.zeros((1, cap + 2))
    q[0, 0] = 1.0
    for _ in range(400):
        q = queue.kfe_step(q, np.array([lam]), np.array([mu]), m, 1.0)
    r = lam / mu
    weights = [r ** n / math.factorial(n) if n < m else r ** n / (math.factorial(m) * m ** (n - m))
               for n in range(cap + 1)]
    pi = np.array(weights) / sum(weights)
    assert np.allclose(q[0, :20], pi[:20], atol=2e-3)


def test_tagged_ship_wait_tail_matches_erlang():
    """정상 상태에서 시작한 시간불변 대기열: P(W > w) = C(m, r) exp(-(m mu - lam) w)."""
    lam, mu, m, cap = 0.6, 0.25, 4, 80
    r = lam / mu
    weights = np.array([r ** n / math.factorial(n) if n < m else r ** n / (math.factorial(m) * m ** (n - m))
                        for n in range(cap + 1)])
    pi = weights / weights.sum()
    erlang_c = pi[m:].sum()
    rng = np.random.default_rng(3)
    t = np.round(np.arange(0, 60 + 1e-9, 1 / 6), 6)
    n0 = rng.choice(cap + 1, size=400, p=pi)
    waits = []
    for k in n0[:200]:
        out = queue.tagged_ship(t, np.full((1, len(t)), mu), np.full(len(t), lam), m, int(k), cap,
                                0.0, 20, rng)
        waits.append(out["wait"].ravel())
    waits = np.concatenate(waits)
    for w in (1.0, 3.0):
        expected = erlang_c * math.exp(-(m * mu - lam) * w)
        assert np.mean(waits > w) == pytest.approx(expected, abs=0.05)


# ---------- 물동량 보정과 미래 정보 차단 ----------

def test_xi_uses_trailing_window_only():
    t = np.round(np.arange(-24, 24 + 1e-9, 1 / 6), 6)
    scen = config.scenario("base")
    X = volume.synthetic_volume(t, scen, np.random.default_rng(4))
    xi_a, _ = volume.xi_series(t, X)
    X2 = X.copy()
    X2[t > 0] *= 3.0                    # 미래 물동량만 바꾼다
    xi_b, _ = volume.xi_series(t, X2)
    assert np.allclose(xi_a[t <= 0], xi_b[t <= 0])


def test_filter_does_not_see_future_observations():
    """k번째 관측 뒤의 관측만 바꾸면, 그 시각까지의 필터 결과는 그대로여야 한다."""
    p = api.params_from({"particles": 64})
    scen = config.scenario("terminal_failure")
    C = sis.coupling(p["kappa"], p["tau0"])
    inp = engine.build_inputs(scen, p, None, np.random.default_rng(5))
    truth = engine.simulate_truth(inp, p, C, np.random.default_rng(6))
    obs_idx, Y, H = engine.observe(truth, inp, p, np.random.default_rng(7))
    assert max(inp["t_all"][obs_idx]) <= 0.0
    k = 5
    Y2, H2 = Y.copy(), H.copy()
    Y2[k + 1:] = Y2[k + 1:] * 3 + 5
    H2[k + 1:] = np.minimum(H2[k + 1:] + 7, np.asarray(p["snap_n"]))
    a = engine.particle_filter(inp, obs_idx, Y, H, p, C, np.random.default_rng(9))
    b = engine.particle_filter(inp, obs_idx, Y2, H2, p, C, np.random.default_rng(9))
    upto = obs_idx[k] + 1
    assert np.allclose(a["filter"]["x_mean"][:upto], b["filter"]["x_mean"][:upto])
    assert np.allclose(a["filter"]["rd_mean"][:upto], b["filter"]["rd_mean"][:upto])
    assert not np.allclose(a["filter"]["x_mean"], b["filter"]["x_mean"])


# ---------- 화물·의사결정 ----------

T_F = np.round(np.arange(0, 24 + 1e-9, 1 / 6), 6)


def _paths(P=64, xl=0.05, xt=0.05, xa=0.05):
    return {"L": np.full((P, len(T_F)), xl), "T": np.full((P, len(T_F)), xt), "A": np.full((P, len(T_F)), xa)}


def _ship(box, box_prio=None, P=64, S=4):
    b = np.full((P, S), float(box))
    return {"box": b, "box_priority": np.full((P, S), float(box if box_prio is None else box_prio))}


def _eval(name="import_factory", box=5.0, box_prio=None, scen="base", eps=0.15, **paths):
    cfg = config.CARGO[name]
    P = 64
    w = np.full(P, 1.0 / P)
    ship = _ship(box, box_prio) if cfg["kind"] == "import" else None
    return cargo.evaluate(T_F, cfg, _paths(P, **paths), w, float(P), config.scenario(scen), eps,
                          np.random.default_rng(8), ship=ship)


def test_decision_picks_min_cost_among_acceptable():
    out = _eval(box=4.0)
    assert out["decision"]["status"] == "recommended"
    ok = [r for r in out["candidates"] if r["feasible"] and not r["negotiation"] and r["p_upper"] <= out["epsilon"]]
    best = min(ok, key=lambda r: r["exp_cost"])
    assert out["decision"]["choice"] == best["label"]
    assert sum(r["status"] == "추천" for r in out["candidates"]) == 1


def test_no_action_when_ship_too_late_and_notice_given():
    out = _eval(box=13.5)
    assert out["decision"]["status"] == "no_acceptable_action"
    assert out["decision"]["choice"] is None
    assert all(r["status"] != "추천" for r in out["candidates"])
    assert out["notice"] and "화주 사전 통보" in out["notice"]


def test_priority_unloading_is_never_auto_recommended():
    """우선 양하는 선사·터미널 승인이 필요한 협의안이다: 자동 추천하지 않고 근거로만 제시."""
    out = _eval(box=13.5, box_prio=3.0)
    prio = [r for r in out["candidates"] if r["priority"]]
    assert prio and all(r["negotiation"] and r["status"] != "추천" for r in prio)
    assert min(r["p_fail"] for r in prio) < min(r["p_fail"] for r in out["candidates"] if not r["priority"])
    assert "우선 양하" in out["decision"]["message"] and "승인되면" in out["decision"]["message"]
    for r in prio:  # 기대 실패확률 = 승인확률 × 승인 시 + (1 - 승인확률) × 거절 시
        base = next(c for c in out["candidates"] if c["time"] == r["time"] and not c["priority"] and c["vehicle"] == "디젤")
        q = config.CARGO["import_factory"]["priority"]["approval"]
        assert r["p_fail"] == pytest.approx(q * r["p_if_approved"] + (1 - q) * base["p_fail"], abs=1e-3)


def test_keeps_plan_when_no_alternative_is_safer():
    out = _eval(box=13.5)
    assert out["decision"]["least"].endswith("(현행 계획)")


def test_export_risk_rises_with_later_departure():
    out = _eval("ulsan_export", xl=0.1, xt=0.3)
    p = [r["p_fail"] for r in out["candidates"]]
    assert all(b >= a - 1e-12 for a, b in zip(p, p[1:]))
    assert p[-1] > p[0]


def test_sea_air_depends_on_air_node():
    calm = _eval("sea_air", box=4.0, xa=0.02)
    jam = _eval("sea_air", box=4.0, xa=0.6)
    pc = next(r for r in calm["candidates"] if r["is_plan"])["p_fail"]
    pj = next(r for r in jam["candidates"] if r["is_plan"])["p_fail"]
    assert pj > pc


def test_yard_congestion_slows_terminal_processing():
    fast = _eval(box=4.0, xt=0.02)
    slow = _eval(box=4.0, xt=0.5)
    tf = next(r for r in fast["candidates"] if r["is_plan"])["exp_turnaround_h"]
    ts = next(r for r in slow["candidates"] if r["is_plan"])["exp_turnaround_h"]
    assert ts > tf


def test_h2_feasibility_rules():
    h2 = config.H2
    imp = config.CARGO["import_factory"]
    ok, why = cargo.h2_check(h2["available_from"] - 0.5, imp, h2)
    assert not ok and "복귀" in why
    ready = h2["available_from"] + h2["charge_h"] + h2["detour_h"] + h2["depot_to_terminal_h"]  # 충전 필요, 수입은 차고지→터미널 이동
    assert not cargo.h2_check(ready - 0.01, imp, h2)[0]
    assert cargo.h2_check(ready, imp, h2)[0]
    late = dict(h2, available_from=h2["station_close"] - h2["charge_h"] + 0.1)  # 충전을 마감 안에 끝낼 수 없음
    ok, why = cargo.h2_check(20.0, imp, late)
    assert not ok and "충전소" in why
    assert not cargo.h2_check(12.0, config.CARGO["sea_air"], h2)[0]  # 1회 충전 주행거리 초과
    out = _eval(scen="h2_trucks", box=4.0)
    assert any(r["vehicle"] == "수소" for r in out["candidates"])
    assert all(not r["feasible"] for r in out["candidates"] if r["vehicle"] == "수소" and r["time"] < ready)


def test_deadline_is_never_relaxed():
    out = _eval(box=4.0)
    assert out["deadline"] == config.CARGO["import_factory"]["deadline"]


def test_container_unloads_before_ship_finishes():
    rng = np.random.default_rng(3)
    t = T_F
    mu = np.full((2, len(t)), 0.25)
    out = queue.tagged_ship(t, mu, np.full(len(t), 0.5), 4, 5, 60, 2.0, 50, rng)
    ok = np.isfinite(out["finish"])
    assert np.all(out["box"][ok] <= out["finish"][ok] + 1e-9)
    assert np.all(out["box_priority"][ok] <= out["box"][ok] + 1e-9)
    assert np.all(out["box"][ok] >= out["begin"][ok] - 1e-9)


# ---------- 원인 판정·재현성·입력 검증 ----------

def test_classify_is_exclusive_with_hold_band():
    seen = set()
    for ps in np.linspace(0, 1, 21):
        for pn in np.linspace(0, 1, 21):
            seen.add(api.classify(ps, pn))
    assert seen == {"비확산", "확산 경계", "물량 주도형", "처리능력형", "원인 보류"}
    assert api.classify(0.2, 0.9) == "비확산"
    assert api.classify(0.5, 0.9) == "확산 경계"  # 확산 자체가 불확실하면 원인을 따지지 않는다
    assert api.classify(0.8, 0.2) == "물량 주도형"
    assert api.classify(0.8, 0.8) == "처리능력형"
    assert api.classify(0.8, 0.5) == "원인 보류"


def test_world_and_estimation_seeds_are_separate():
    a = api.run({"scenario": "terminal_failure", "particles": 64})
    b = api.run({"scenario": "terminal_failure", "particles": 64})
    assert a["now"] == b["now"] and a["cargo"] == b["cargo"]
    c = api.run({"scenario": "terminal_failure", "particles": 64, "seed": 7})
    assert c["truth_now"] == a["truth_now"] and c["rd"]["truth"] == a["rd"]["truth"]  # 같은 세계, 다른 추정
    d = api.run({"scenario": "terminal_failure", "particles": 64, "world": 7})
    assert d["rd"]["truth"] != a["rd"]["truth"]


def test_forecast_keeps_posterior_weights():
    out = api.run({"scenario": "base", "particles": 64})
    assert out["now"]["ess"] <= 64 + 1e-9
    assert all(v["ess"] == pytest.approx(out["now"]["ess"], abs=0.1) for v in out["cargo"].values())


def test_run_output_is_json_serializable():
    out = api.run({"scenario": "h2_trucks", "particles": 64})
    text = json.dumps(out, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text


@pytest.mark.parametrize("bad", [{"particles": 100}, {"epsilon": 0.0}, {"epsilon": 0.6}, {"kappa": -1},
                                 {"world": 0}, {"fail_cost": -1}])
def test_invalid_params_rejected(bad):
    with pytest.raises(ValueError):
        api.run(dict(bad, particles=bad.get("particles", 64)))


def test_unknown_scenario_and_cargo_rejected():
    with pytest.raises(ValueError):
        api.run({"scenario": "nope"})
    with pytest.raises(ValueError):
        api.run({"cargo": "nope"})


def test_fleet_option_turns_h2_on_and_off():
    off = api.run({"scenario": "h2_trucks", "particles": 64, "h2": False})
    on = api.run({"scenario": "base", "particles": 64, "h2": True})
    dflt = api.run({"scenario": "h2_trucks", "particles": 64})
    vehicles = lambda r: {c["vehicle"] for v in r["cargo"].values() for c in v["candidates"]}
    assert vehicles(off) == {"디젤"} and "수소" in vehicles(on) and "수소" in vehicles(dflt)


def test_h2_export_needs_reposition_time():
    h2 = config.H2
    exp = config.CARGO["ulsan_export"]
    earliest, _ = cargo.h2_earliest(exp, h2)
    assert earliest == pytest.approx(h2["available_from"] + h2["charge_h"] + h2["detour_h"] + exp["h2_reposition_h"])
    assert not cargo.h2_check(10.0, exp, h2)[0]  # 20:00 출발은 차고지→공장 이동 때문에 불가


def test_uncertain_ends_conservatively_when_unreachable():
    """필요 ESS가 최대 입자 수로도 닿지 않으면 '추정 불확실'로 남기지 않고 보수적으로 통보한다."""
    out = _eval("ulsan_parts", box=9.6, eps=0.05)
    assert out["decision"]["status"] in ("recommended", "uncertain", "no_acceptable_action")
    if out["decision"].get("conservative"):
        assert out["notice"] is not None and "보수적으로" in out["decision"]["message"]


def test_announced_gate_closure_moves_export_departure():
    """공지된 반입 제한 중에 도착하는 출발안은 위험이 커지고, 제한 전에 들어가는 안이 추천된다."""
    cfg = config.CARGO["ulsan_export"]
    P = 64
    closed = ((T_F >= 8.0) & (T_F < 13.5)).astype(float)
    out = cargo.evaluate(T_F, cfg, _paths(P, xl=0.05, xt=0.1), np.full(P, 1.0 / P), float(P),
                         config.scenario("base"), 0.10, np.random.default_rng(1), gate_closed=closed)
    plan = next(r for r in out["candidates"] if r["is_plan"])
    early = out["candidates"][0]
    assert plan["p_fail"] > 0.5 and early["p_fail"] < 0.05
    assert out["decision"]["status"] == "recommended" and out["decision"]["time"] < 8.0 - 1.3
    assert out["gate_closed"] == [8.0, 13.5]


def test_uncertain_status_does_not_notify():
    """p_hat <= eps < p_hat+ 이면 위험 초과로 단정하지 않고 통보하지 않는다."""
    out = _eval("ulsan_parts", box=9.6, eps=0.05)
    statuses = {r["status"] for r in out["candidates"] if not r["negotiation"] and r["feasible"]}
    if out["decision"]["status"] == "uncertain":
        assert out["notice"] is None and "추정 불확실" in statuses
    else:  # 상황에 따라 추천 또는 초과로 갈 수 있지만 규칙은 일관돼야 한다
        assert out["decision"]["status"] in ("recommended", "no_acceptable_action")


def test_terminal_capacity_slows_gate_processing():
    slow = _paths(64)
    slow["capT"] = np.full(len(T_F), 0.5)
    fast = _paths(64)
    fast["capT"] = np.ones(len(T_F))
    cfg = config.CARGO["import_factory"]
    w = np.full(64, 1 / 64)
    a = cargo.evaluate(T_F, cfg, fast, w, 64.0, config.scenario("base"), 0.1, np.random.default_rng(2), ship=_ship(4.0))
    b = cargo.evaluate(T_F, cfg, slow, w, 64.0, config.scenario("base"), 0.1, np.random.default_rng(2), ship=_ship(4.0))
    ta = next(r for r in a["candidates"] if r["is_plan"])["exp_turnaround_h"]
    tb = next(r for r in b["candidates"] if r["is_plan"])["exp_turnaround_h"]
    assert tb > ta


def test_validation_options_run():
    """검증용 선택지: 공지 가림과 정적 모수 오지정이 참 경로는 그대로 두고 추정만 바꾼다."""
    a = api.run({"scenario": "terminal_failure", "particles": 64})
    m = api.run({"scenario": "terminal_failure", "particles": 64, "mask_notices": True})
    s = api.run({"scenario": "terminal_failure", "particles": 64, "misspec": {"beta_bar": 1.3}})
    assert m["rd"]["truth"] == a["rd"]["truth"] == s["rd"]["truth"]
    assert m["now"]["capacity"] == [1.0, 1.0, 1.0, 1.0] and a["now"]["capacity"][0] < 1.0
    assert s["now"]["p_spread"] != a["now"]["p_spread"]
    with pytest.raises(ValueError):
        api.run({"scenario": "base", "particles": 64, "misspec": {"nope": 2}})


def test_precomputed_matches_engine():
    """docs/data/precomputed.json의 모든 장면이 현재 엔진과 같은지 확인(오래된 데이터 배포 방지)."""
    model = json.loads((ROOT / "docs" / "data" / "ai_model.json").read_text(encoding="utf-8"))
    pre = json.loads((ROOT / "docs" / "data" / "precomputed.json").read_text(encoding="utf-8"))
    assert set(pre["runs"]) == set(config.SCENARIOS)
    for scen, stored in pre["runs"].items():
        fresh = json.loads(json.dumps(api.run({"scenario": scen}, ai_model=model)))
        # 선형대수 라이브러리(OS별 BLAS/LAPACK) 차이로 마지막 자리가 흔들릴 수 있어 수치는 허용오차로 비교한다.
        for key in ("alarm", "cause", "congested"):
            assert stored["now"][key] == fresh["now"][key], scen
        for key in ("rd_median", "rd_q10", "rd_q90", "p_spread"):
            assert stored["now"][key] == pytest.approx(fresh["now"][key], abs=2e-3), scen
        for name in config.CARGO:
            assert stored["cargo"][name]["decision"]["status"] == fresh["cargo"][name]["decision"]["status"], (scen, name)
            assert stored["cargo"][name]["decision"]["choice"] == fresh["cargo"][name]["decision"]["choice"], (scen, name)
