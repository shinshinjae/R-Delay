"""화물별 마감 실패확률과 기회제약 배차 결정.

수입: R = max(컨테이너 양하 + tau, 서류 D);  s = max(R, b);  int_s^G v = 1,
      v = cap_T * max(v_min, 1 - eta_L x_L - eta_Y x_T) / h0   (x_L: 게이트·도로, x_T: 터미널 야드 지연 비율,
                                                               cap_T: 공지된 터미널 처리능력 비율)
      A = G + road * (1 + eta_u x_L(G)) * zeta
      Sea&Air는 A에서 항공 반입 처리: int v_A = 1, v_A = max(v_min, 1 - eta_A x_A) / h_A
수출: 공장 출발 b,  G1 = b + road * (1 + eta_u x_L(b)) * zeta,  공지된 반입 제한 중이면 해제 시각까지 대기,
      반입 처리 int_{G1}^{G} v = 1
실패: 최종 시각 > 마감 d (원래 마감은 사후에 늦추지 않는다)
p_hat = sum_i w_i mean_s 1{실패},  p_hat+ = max(p_hat + z_0.95 sqrt(p_hat(1 - p_hat) / ESS), 1 - 0.05^(1/ESS))
a* = argmin_{a in A_feas, p_hat+(a) <= eps} c_a + c_w E[W] + c_f p_hat   (c_a = 변경 비용 + 거리 × km당 운행비)
배차안 a = (시각, 동력원[디젤/수소]). '우선 양하 요청'은 선사·터미널 승인이 필요한 협의안이라
자동 추천에서 빼고, 운송사 배차로 위험을 맞출 수 없을 때 근거로만 제시한다.
p_hat <= eps < p_hat+ 인 안만 있으면 위험 초과로 단정하지 않고 '추정 불확실'로 표시한다(통보하지 않음).
"""
from __future__ import annotations
import math
import numpy as np
from . import config, stats
from .queue import cumulative_hazard, invert_rows, interp_rows

V_MIN = 0.1  # 처리 속도 하한(완전 정지 대신 최소 처리, 1/h0 단위)
MAX_PARTICLES = 1024  # 화면에서 고를 수 있는 최대 입자 수
Z95 = 1.645


def _rate(xs, etas, h0, cap=None):
    v = 1.0
    for x, e in zip(xs, etas):
        v = v - e * x
    v = np.maximum(V_MIN, v) / h0
    return v if cap is None else v * cap


def _process(t, w_cum, start):
    """start (P, S)에서 누적 처리량 1을 채우는 시각. 예측 구간을 넘으면 inf."""
    w_start = interp_rows(t, w_cum, start, right=np.inf)
    done = invert_rows(t, w_cum, w_start + 1.0, start)
    done[~np.isfinite(start)] = np.inf
    return done


def _at(t, path, times):
    """path (P, T)를 times (P, S)에서 보간(구간 밖은 끝값)."""
    return interp_rows(t, path, np.minimum(times, t[-1]))


def _next_open(t, closed, times):
    """반입 제한(closed[t] = 1) 중에 도착하면 제한이 풀리는 첫 시각으로 미룬다."""
    if closed is None or not np.any(closed > 0.5):
        return times
    nxt = np.empty(len(t))
    upcoming = np.inf
    for k in range(len(t) - 1, -1, -1):
        if closed[k] < 0.5:
            upcoming = t[k]
        nxt[k] = upcoming
    idx = np.clip(np.searchsorted(t, times, side="right") - 1, 0, len(t) - 1)
    in_closed = (closed[idx] > 0.5) & np.isfinite(times) & (times <= t[-1])
    return np.where(in_closed, np.maximum(times, nxt[idx]), times)


def _closed_window(t, closed):
    """반입 제한 구간 [시작, 끝)을 판단 시각 기준 시간으로. 없으면 None."""
    if closed is None or not np.any(closed > 0.5):
        return None
    on = np.flatnonzero(closed > 0.5)
    end = t[on[-1] + 1] if on[-1] + 1 < len(t) else t[-1]
    return [round(float(t[on[0]]), 2), round(float(end), 2)]


def _clock(t_rel, base_hour=10):
    if t_rel is None or not math.isfinite(t_rel):
        return "–"
    total = base_hour + t_rel
    day = "내일 " if total >= 24 else ""
    minutes = int(round((total % 24) * 60)) % (24 * 60)
    return f"{day}{minutes // 60:02d}:{minutes % 60:02d}"


def h2_earliest(cfg, h2):
    """수소 트럭이 이 화물에 쓸 수 있는 가장 이른 시각과 사유. 불가능하면 (None, 사유)."""
    need = cfg["distance_km"]
    if need > h2["full_km"]:
        return None, "1회 충전 주행거리 초과(중간 충전 필요)"
    travel = h2["depot_to_terminal_h"] if cfg["kind"] == "import" else cfg.get("h2_reposition_h", 0.0)
    if need <= h2["residual_km"]:
        return h2["available_from"] + travel, "차량 복귀 전"
    start = max(h2["available_from"], h2["station_open"])
    if start + h2["charge_h"] > h2["station_close"]:
        return None, f"충전소 마감({_clock(h2['station_close'])}) 전 충전 불가"
    return start + h2["charge_h"] + h2["detour_h"] + travel, "충전·경유" + ("·이동" if travel else "")


def h2_check(b, cfg, h2):
    """수소 트럭 실행 가능 여부: (가능, 사유)."""
    earliest, why = h2_earliest(cfg, h2)
    if earliest is None:
        return False, why
    if b < h2["available_from"]:
        return False, f"차량 복귀 전({_clock(h2['available_from'])} 복귀)"
    if b < earliest - 1e-9:
        return False, f"{why} 뒤 {_clock(earliest)}부터 가능"
    return True, ""


def _specs(cfg, scen, costs, h2):
    per_km = costs["per_km"]
    trip = {k: v * cfg["distance_km"] for k, v in per_km.items()}
    rows = []
    for k, (b, c) in enumerate(cfg["candidates"]):
        rows.append({"time": float(b), "cost": float(c) + trip["디젤"], "priority": False, "vehicle": "디젤",
                     "is_plan": k == cfg["plan_index"]})
    if cfg["kind"] == "import" and cfg.get("priority"):
        pr = cfg["priority"]
        base_cost = dict(cfg["candidates"])
        for b in cfg.get("priority_times", []):
            rows.append({"time": float(b), "cost": float(base_cost.get(b, 0.0)) + trip["디젤"] + pr["approval"] * pr["cost"],
                         "priority": True, "vehicle": "디젤", "is_plan": False})
    if scen.get("h2"):
        for b, c in cfg["candidates"]:
            rows.append({"time": float(b), "cost": float(c) + trip["수소"], "priority": False,
                         "vehicle": "수소", "is_plan": False})
    return rows


def _label(cfg, r):
    head = ("도착 " if cfg["kind"] == "import" else "출발 ") + _clock(r["time"])
    if r["vehicle"] == "수소":
        head += " · 수소"
    if r["priority"]:
        head += " + 우선 양하 요청"
    if r["is_plan"]:
        head += " (현행 계획)"
    return head


def _final_times(t, cfg, spec, paths, ship, w_term, w_air, zeta, gate_closed, use_priority):
    """한 배차안의 최종 시각과 터미널 체류(또는 반입 처리) 시간 (P, S)."""
    b = spec["time"]
    P, S = zeta.shape
    if cfg["kind"] == "import":
        box = ship["box_priority"] if use_priority else ship["box"]
        ready = np.maximum(box + cfg["transfer"], cfg["doc_ready"])
        start = np.maximum(ready, b)
        done = _process(t, w_term, start)
        arrive = done + cfg["road"] * (1.0 + cfg["eta_u"] * _at(t, paths["L"], done)) * zeta
        final = _process(t, w_air, arrive) if w_air is not None else arrive
        return final, done - b
    start = np.full((P, S), b)
    gate_in = b + cfg["road"] * (1.0 + cfg["eta_u"] * _at(t, paths["L"], start)) * zeta
    gate_open = _next_open(t, gate_closed, gate_in)
    done = _process(t, w_term, gate_open)
    return done, done - gate_in


def _weighted_mean_rows(values, w):
    tt = np.where(np.isfinite(values), values, np.nan)
    cnt = np.isfinite(tt).sum(axis=1)
    per = np.where(cnt > 0, np.nansum(tt, axis=1) / np.maximum(cnt, 1), np.nan)
    ok = np.isfinite(per)
    return float((w[ok] @ per[ok]) / w[ok].sum()) if ok.any() else float("inf")


def evaluate(t, cfg, paths, w, ess, scen, epsilon, rng, ship=None, samples=8, costs=None, h2=None,
             fail_cost=None, gate_closed=None):
    """paths: {"L","T","A"} (P, T) 예측 지연 비율과 "capT" (T,) 공지된 터미널 처리능력.
    ship: 수입 화물의 선박 결과(queue.tagged_ship). gate_closed: (T,) 공지된 수출 반입 제한(0/1)."""
    costs = costs or config.COSTS
    h2 = h2 or config.H2
    c_fail = float(fail_cost if fail_cost is not None else cfg["fail_cost"])
    P = paths["L"].shape[0]
    S = ship["box"].shape[1] if ship is not None else samples
    w = np.asarray(w, dtype=float)
    cap_t = paths.get("capT")
    term_rate = _rate([paths["L"], paths["T"]], [cfg["eta_l"], cfg["eta_y"]], cfg["h0"],
                      None if cap_t is None else np.asarray(cap_t)[None, :])
    w_term = cumulative_hazard(t, term_rate)
    w_air = None
    if cfg.get("air_h0"):
        w_air = cumulative_hazard(t, _rate([paths["A"]], [cfg["eta_a"]], cfg["air_h0"]))
    zeta = np.exp(0.15 * rng.standard_normal((P, S)) - 0.5 * 0.15 ** 2)  # 도로 교통 변동(후보 간 공통)
    approved = None
    if cfg["kind"] == "import" and cfg.get("priority"):
        approved = rng.random((P, S)) < cfg["priority"]["approval"]

    rows = []
    finals = {}
    for spec in _specs(cfg, scen, costs, h2):
        final, stay = _final_times(t, cfg, spec, paths, ship, w_term, w_air, zeta, gate_closed, False)
        p_hat = float(w @ (~(final <= cfg["deadline"])).mean(axis=1))
        exp_stay = _weighted_mean_rows(stay, w)
        p_approved = None
        if spec["priority"]:
            # 승인되면 우선 양하, 거절되면 일반 양하: 같은 난수로 두 조건부 값을 따로 계산해 승인 확률로 섞는다.
            q = cfg["priority"]["approval"]
            final_a, stay_a = _final_times(t, cfg, spec, paths, ship, w_term, w_air, zeta, gate_closed, True)
            p_approved = float(w @ (~(final_a <= cfg["deadline"])).mean(axis=1))
            p_hat = q * p_approved + (1 - q) * p_hat
            exp_stay = q * _weighted_mean_rows(stay_a, w) + (1 - q) * exp_stay
            final = np.where(approved, final_a, final)  # 도착 분위수도 승인·거절이 섞인 표본으로
        p_up = stats.wald_upper_ess(p_hat, ess, Z95)
        reasons = []
        if spec["time"] < costs["approval_lead_h"]:
            reasons.append("변경 승인·이동 시간 부족")
        if spec["vehicle"] == "수소":
            ok_h2, why = h2_check(spec["time"], cfg, h2)
            if not ok_h2:
                reasons.append(why)
        q50, q90 = stats.weighted_mean_quantiles(final, w, [0.5, 0.9])
        cost = spec["cost"] + costs["wait_per_h"] * exp_stay + c_fail * p_hat
        row = {
            "label": _label(cfg, spec), "time": spec["time"], "vehicle": spec["vehicle"], "priority": spec["priority"],
            "is_plan": spec["is_plan"], "change_cost": round(spec["cost"]), "p_fail": round(p_hat, 4),
            "p_if_approved": None if p_approved is None else round(p_approved, 4),
            "p_upper": round(p_up, 4), "exp_turnaround_h": round(exp_stay, 2) if math.isfinite(exp_stay) else None,
            "exp_cost": round(cost) if math.isfinite(cost) else None,
            "arrival_q50": None if q50 is None else round(q50, 2), "arrival_q90": None if q90 is None else round(q90, 2),
            "feasible": not reasons, "reasons": reasons, "negotiation": spec["priority"],
        }
        rows.append(row)
        finals[row["label"]] = final

    for r in rows:
        if not r["feasible"]:
            r["status"] = "실행 불가: " + ", ".join(r["reasons"])
        elif r["negotiation"]:
            r["status"] = "협의안(승인 {:.0%} 가정)".format(cfg["priority"]["approval"])
        elif r["p_upper"] <= epsilon:
            r["status"] = "기준 충족"
        elif r["p_fail"] <= epsilon:
            r["status"] = "추정 불확실"
        else:
            r["status"] = "위험 기준 초과"

    plan = next(r for r in rows if r["is_plan"])
    carrier = [r for r in rows if r["feasible"] and not r["negotiation"]]
    ok = [r for r in carrier if r["p_upper"] <= epsilon and r["exp_cost"] is not None]
    uncertain = [r for r in carrier if r["status"] == "추정 불확실"]
    nego = [r for r in rows if r["negotiation"] and r["feasible"]]
    nego_txt = ""
    if nego:
        nb = min(nego, key=lambda r: r["p_fail"])
        nego_txt = (f" 선사·터미널에 우선 양하를 요청하면 기대 실패확률 {nb['p_fail']:.1%}"
                    f"(승인 {cfg['priority']['approval']:.0%} 가정, 승인되면 {nb['p_if_approved']:.1%})입니다.")
    if ok:
        best = min(ok, key=lambda r: r["exp_cost"])
        best["status"] = "추천"
        d_p = (best["p_fail"] - plan["p_fail"]) * 100
        d_c = best["exp_cost"] - (plan["exp_cost"] or 0)
        decision = {
            "status": "recommended", "choice": best["label"], "time": best["time"],
            "message": (f"{best['label']} 추천: 실패확률 {best['p_fail']:.1%}(상한 {best['p_upper']:.1%} ≤ 허용 {epsilon:.0%}), "
                        f"기대비용 {best['exp_cost']:,}원" + ("" if best is plan else
                        f" — 현행 계획 대비 실패 {d_p:+.1f}%p, 비용 {d_c:+,}원")),
        }
    else:
        cand, need_ess, reachable = None, 0.0, 0.0
        if uncertain:
            cand = min(uncertain, key=lambda r: (r["p_upper"], r["exp_cost"] or float("inf")))
            # 상한을 허용 위험 아래로 내리는 데 필요한 ESS. 최대 입자 수로도 닿지 않으면 보수적으로 끝낸다.
            gap = max(epsilon - cand["p_fail"], 1e-9)
            need_ess = Z95 ** 2 * cand["p_fail"] * (1 - cand["p_fail"]) / gap ** 2
            reachable = ess * MAX_PARTICLES / max(P, 1)
        if cand is not None and need_ess <= reachable:
            decision = {
                "status": "uncertain", "choice": None, "time": None, "least": cand["label"],
                "message": (f"{cand['label']}의 실패확률 추정치 {cand['p_fail']:.1%}는 허용 위험 {epsilon:.0%} 이하이지만 "
                            f"추정 오차 상한 {cand['p_upper']:.1%}가 허용 위험을 넘습니다(ESS {ess:.0f}, 판정에 필요한 ESS 약 {need_ess:.0f}). "
                            f"입자 수 {MAX_PARTICLES}개로 다시 계산한 뒤 판단하세요. 화주 통보는 아직 하지 않습니다."),
            }
        else:
            pool = carrier or [r for r in rows if not r["negotiation"]]
            p_min = min(r["p_fail"] for r in pool)
            se = math.sqrt(max(p_min * (1 - p_min), 0.0) / max(ess, 1.0))
            band = [r for r in pool if r["p_fail"] <= p_min + max(0.002, se)]  # 추정 오차 안이면 같은 위험으로 본다
            if plan in band:
                least = plan  # 위험을 줄인다고 말할 수 없는 변경은 권하지 않는다(현행 유지)
            else:
                least = min(band, key=lambda r: (r["exp_cost"] if r["exp_cost"] is not None else float("inf")))
            if least["feasible"]:
                least["status"] = "위험 최소(기준 초과)"
            if cand is not None:
                lead = (f"실패확률 추정치는 허용 위험 {epsilon:.0%} 이하일 수 있지만, 판정에 필요한 ESS 약 {need_ess:.0f}을 "
                        f"입자 {MAX_PARTICLES}개로도 얻을 수 없어 보수적으로 기준 초과로 봅니다. ")
            else:
                lead = f"운송사 배차만으로는 허용 위험 {epsilon:.0%}을 맞출 수 없습니다. "
            decision = {
                "status": "no_acceptable_action", "choice": None, "time": None, "least": least["label"],
                "conservative": cand is not None,
                "message": (lead + f"위험이 가장 낮은 안은 {least['label']} — 실패확률 {least['p_fail']:.1%}, "
                            f"상한 {least['p_upper']:.1%}입니다." + nego_txt),
            }
    notice = None
    if decision["status"] == "no_acceptable_action":
        ref = next(r for r in rows if r["label"] == decision["least"])
        notice = (f"화주 사전 통보: 예상 도착 중앙 {_clock(ref['arrival_q50'])} · 90% {_clock(ref['arrival_q90'])}, "
                  f"마감 {cfg['deadline_label']} 실패확률 {ref['p_fail']:.0%}(상한 {ref['p_upper']:.0%}).")

    # 현행 계획의 필요 완충 b* = max(0, Q_{1-eps}(A_plan) - t_plan)
    qcfg = config.QUEUE[cfg["terminal"]]
    if cfg["kind"] == "import":
        ready_nom = max(cfg["ship_arrival"] + 0.5 / qcfg["mu0"] + cfg["transfer"], cfg["doc_ready"])
        t_plan = max(plan["time"], ready_nom) + cfg["h0"] + cfg["road"] + cfg.get("air_h0", 0.0)
    else:
        gate_in = float(_next_open(t, gate_closed, np.array([plan["time"] + cfg["road"]]))[0])
        t_plan = gate_in + cfg["h0"]  # 공지된 반입 제한을 반영한 명목 반입
    q_hi = stats.weighted_mean_quantiles(finals[plan["label"]], w, [1 - epsilon])[0]
    buffer = None if q_hi is None else max(0.0, q_hi - t_plan)
    ready = None
    if cfg["kind"] == "import":
        rq = stats.weighted_mean_quantiles(np.maximum(ship["box"] + cfg["transfer"], cfg["doc_ready"]), w, [0.5, 0.9])
        ready = {"q50": None if rq[0] is None else round(rq[0], 2), "q90": None if rq[1] is None else round(rq[1], 2)}
    h2_note = None
    if scen.get("h2"):
        earliest, why = h2_earliest(cfg, h2)
        latest_return = h2["station_close"] - h2["charge_h"]
        if earliest is None:
            h2_note = f"수소 트럭으로는 이 화물을 맡을 수 없습니다: {why}."
        else:
            h2_note = (f"수소 트럭은 {_clock(earliest)}부터 {'터미널 도착' if cfg['kind'] == 'import' else '출발'} 가능합니다"
                       f"({_clock(h2['available_from'])} 복귀·잔여 {h2['residual_km']}km, 이 운행 {cfg['distance_km']}km). "
                       f"차고지 복귀가 {_clock(latest_return)}보다 늦으면 충전소 마감({_clock(h2['station_close'])}) 때문에 오늘 밤 수소 운행이 불가능합니다.")
    return {
        "label": cfg["label"], "kind": cfg["kind"], "deadline": cfg["deadline"], "deadline_label": cfg["deadline_label"],
        "epsilon": epsilon, "fail_cost": c_fail, "ess": round(float(ess), 1), "candidates": rows, "decision": decision,
        "notice": notice, "h2_note": h2_note, "ready": ready,
        "gate_closed": _closed_window(t, gate_closed),
        "plan": {"label": plan["label"], "t_plan": round(t_plan, 2), "slack_h": round(cfg["deadline"] - t_plan, 2),
                 "buffer_needed_h": None if buffer is None else round(buffer, 2)},
    }
