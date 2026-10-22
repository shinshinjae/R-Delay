"""다중 합성 세계 검증: 시나리오마다 세계 난수(world)를 바꿔 여러 번 돌리고 합성 참값과 대조한다.

세계 하나 = 같은 사건 설정에서 물동량·참 경로·관측이 다르게 나온 한 번의 '현실'.
필터는 참 세계의 구조와 정적 모수를 안다(합성 검증의 기본 조건). 이를 완화한 오지정 실험을 따로 돌린다.

측정(계획서 검증 설계 ⓑ 합성자료):
  - 경보: 판단 시각 확산 경보(P(R_d>1) >= 0.5) 대 참 R_d > 1의 적중·오경보·미탐
  - 선행시간: 과거 24시간에서 첫 경보(확산 또는 적체) 시각과 참 적체 시작 시각의 차이
      기준선 1) 공지 즉시 경보 규칙(운영자 처리능력 공지·장비 고장 신호가 처음 들어온 시각)
      기준선 2) 공지를 가린 관측 단독 추정(mask_notices)
  - 경보 오발견율: 첫 경보 뒤 12시간 안에 참 적체·참 확산이 없던 비율
  - 원인: 추정 판정 대 참 판정(혼동행렬, 보류 종류별 수, 라벨별 정밀도)
  - 확률 품질: P(R_d>1)의 Brier 점수와 시나리오별 기후값 대비 기술점수
  - 구간: 80% 구간 포함률(지난 24시간 평균, 마지막 6시간, 판단 시각), R_d RMSE
  - 화물: 현행 계획의 실패확률 예측 대 참 경로에서 한 번 실현한 결과
      기준선 a) 항상 0(현행 방식: 도착예정시각 + 고정 여유 2시간, 공지 반영 안 함)
      기준선 b) 공지 반영 도착예정시각 + 고정 여유 2시간
      기준선 c) 시나리오 기후값(다른 세계들의 실현 실패율)
      판별력: 세계별 예측과 실현의 상관
      R-Delay 선택안의 실현 실패율·실현 체류시간, 화주 통보의 정밀도·재현율
  - AI 켬/끔, 정적 모수 오지정(beta_bar ×1.3, ×0.7)
실행: python3 scripts/validate.py [--worlds 60]
결과: docs/data/validation.json (모두 합성 자료 결과이며 실증 성능이 아니다)
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "py"))
from rdelay import api, cargo, config, engine, queue, sis  # noqa: E402

SCENARIOS = ["base", "terminal_failure", "typhoon_diversion", "holiday_surge", "epidemic"]
NOTICE_SCENARIOS = ["terminal_failure", "epidemic"]
WORLD0 = 2000  # 보고용 세계. 필터 잡음 조정은 1000~1023에서 했으므로 겹치지 않게 한다.
BUFFER_H = 2.0
FA_WINDOW_H = 12.0


def _truth_paths(scen_name, p, model):
    """api.run과 같은 세계 난수로 참 경로를 다시 만든다(예측 구간 포함)."""
    scen = config.scenario(scen_name)
    rng_vol, rng_truth, _ = (np.random.default_rng(s) for s in np.random.SeedSequence(int(p["world"])).spawn(3))
    C = sis.coupling(p["kappa"], p["tau0"])
    inp = engine.build_inputs(scen, p, model, rng_vol)
    truth = engine.simulate_truth(inp, p, C, rng_truth)
    return scen, inp, truth


def _realize_cargo(scen, inp, truth, rng):
    """참 경로에서 화물마다 한 번 실현: 후보별 (실패 0/1, 체류시간)."""
    off = len(inp["t_hist"]) - 1
    tf = inp["t_fore"]
    term = config.IDX["AT"]
    x_t = truth["x"][off:]
    cap_t = inp["capacity"][off:, term]
    paths = {"L": x_t[None, :, config.IDX["L"]], "T": x_t[None, :, term], "A": x_t[None, :, config.IDX["A"]], "capT": cap_t}
    qcfg = config.QUEUE["AT"]
    icfg = config.CARGO["import_factory"]
    mu = queue.service_rates(paths["T"], qcfg["mu0"], qcfg["eta_b"], inp["berth_open"][off:, term],
                             inp["mu_mult"][off:, term] * inp["staffing"][off:, term])
    a = qcfg["arrival"] * inp["arrival_mult"][off:, term]
    ship = queue.tagged_ship(tf, mu, a, qcfg["servers"], scen["n0"]["AT"], config.QUEUE["cap"],
                             icfg["ship_arrival"], 1, rng, priority_frac=icfg["priority"]["frac"])
    out = {}
    for name, ccfg in config.CARGO.items():
        ev = cargo.evaluate(tf, ccfg, paths, np.ones(1), 1.0, scen, ccfg["epsilon"], rng,
                            ship=ship if ccfg["kind"] == "import" else None, samples=1,
                            gate_closed=inp["gate_closed"][off:, term] if ccfg["kind"] == "export" else None)
        out[name] = {r["label"]: (r["p_fail"], r["exp_turnaround_h"]) for r in ev["candidates"]}
    return out


def _notice_eta_pred(name, inp):
    """공지 반영 도착예정시각 + 고정 여유 2시간 규칙: 여유가 모자라면 실패(1), 아니면 0."""
    cfg = config.CARGO[name]
    off = len(inp["t_hist"]) - 1
    tf = inp["t_fore"]
    term = config.IDX["AT"]
    cap = inp["capacity"][off:, term]
    plan_b = cfg["candidates"][cfg["plan_index"]][0]
    if cfg["kind"] == "import":
        q = config.QUEUE["AT"]
        cap_ship = float(np.interp(cfg["ship_arrival"] + 2.0, tf, cap))
        ready = max(cfg["ship_arrival"] + 0.5 / (q["mu0"] * max(cap_ship, 0.1)) + cfg["transfer"], cfg["doc_ready"])
        start = max(plan_b, ready)
        eta = start + cfg["h0"] / max(float(np.interp(start, tf, cap)), 0.1) + cfg["road"] + cfg.get("air_h0", 0.0)
    else:
        gate_in = plan_b + cfg["road"]
        closed = inp["gate_closed"][off:, term]
        gate_in = float(cargo._next_open(tf, closed, np.array([gate_in]))[0])
        eta = gate_in + cfg["h0"] / max(float(np.interp(gate_in, tf, cap)), 0.1)
    return 1.0 if eta + BUFFER_H > cfg["deadline"] else 0.0


def _first_notice(inp):
    """과거 24시간에서 운영자 처리능력 공지나 장비 고장 신호가 처음 들어온 시각(없으면 None)."""
    th = inp["t_hist"]
    k = len(th)
    flag = np.any(inp["capacity"][:k] < 0.999, axis=1) | np.any(inp["features"][:k, :, 3] > 0.5, axis=1)
    return float(th[np.argmax(flag)]) if flag.any() else None


def one(task):
    scen_name, world, model, variant = task
    opts = {"scenario": scen_name, "world": world, **variant.get("extra", {})}
    kind = variant["kind"]
    use_model = None if kind == "ai_off" else model
    if kind == "masked":
        opts["mask_notices"] = True
    if kind == "misspec":
        opts["misspec"] = {"beta_bar": variant["factor"]}
    r = api.run(dict(opts), ai_model=use_model)
    p = api.params_from({"world": world})
    scen, inp, truth = _truth_paths(scen_name, p, model)
    thr = np.asarray(p["congestion_threshold"])
    th = np.asarray(r["t_hist"], dtype=float)
    p_gt1 = np.asarray(r["rd"]["p_gt1"], dtype=float)
    truth_rd_h = np.asarray(r["rd"]["truth"], dtype=float)
    x_f = np.asarray(r["x"]["filter_mean"], dtype=float)
    x_true_h = np.asarray(r["x"]["truth"], dtype=float)
    alarm_series = (p_gt1 >= 0.5) | np.any(x_f > thr, axis=1)
    cong_true = np.any(x_true_h > thr, axis=1)
    onset = float(th[np.argmax(cong_true)]) if (not cong_true[0] and cong_true.any()) else None
    first_alarm = float(th[np.argmax(alarm_series)]) if alarm_series.any() and not alarm_series[0] else None
    t_all = inp["t_all"]
    ev_full = np.any(truth["x"] > thr, axis=1) | (truth["rd"] > 1.0)
    false_alarm = None
    if first_alarm is not None:
        win = (t_all >= first_alarm) & (t_all <= first_alarm + FA_WINDOW_H)
        false_alarm = not bool(ev_full[win].any())
    k_hist = len(inp["t_hist"])
    notice_on = (np.any(inp["capacity"][:k_hist] < 0.999, axis=1) | np.any(inp["features"][:k_hist, :, 3] > 0.5, axis=1))[::3]
    rec = {"scenario": scen_name, "world": world, "variant": kind, "factor": variant.get("factor"),
           "rmse_filter": r["validation"]["rmse_filter"], "rmse_smooth": r["validation"]["rmse_smooth"],
           "coverage_80": r["validation"]["coverage_80"], "coverage_last6": r["validation"]["coverage_80_last6h"],
           "inside_now": r["validation"]["inside_now"],
           "p_spread": r["now"]["p_spread"], "truth_rd": r["truth_now"]["rd"], "truth_spread": r["truth_now"]["rd"] > 1.0,
           "est_spread": r["now"]["p_spread"] >= 0.5, "cause": r["now"]["cause"].split("(")[0],
           "truth_cause": r["truth_now"]["label"],
           "brier_hist": float(np.mean((p_gt1 - (truth_rd_h > 1.0)) ** 2)),
           "brier_notice_rule": float(np.mean((notice_on[: len(truth_rd_h)].astype(float) - (truth_rd_h > 1.0)) ** 2)),
           "no_alarm": not bool(alarm_series.any()),
           "truth_rate_hist": float(np.mean(truth_rd_h > 1.0)),
           "onset": onset, "first_alarm": first_alarm, "false_alarm": false_alarm,
           "first_notice": _first_notice(inp), "ess": r["now"]["ess"],
           "bias_now": r["now"]["rd_median"] - r["truth_now"]["rd"]}
    if kind != "main":
        return rec
    realized = _realize_cargo(scen, inp, truth, np.random.default_rng(world * 7 + 3))
    crec = {}
    for name, ev in r["cargo"].items():
        plan = next(c for c in ev["candidates"] if c["is_plan"])
        # 추천된 안만 R-Delay가 바꾼 것으로 센다('추정 불확실'·'추천 없음'은 현행 계획 유지).
        chosen_label = ev["decision"]["choice"] or plan["label"]
        y_plan, stay_plan = realized[name][plan["label"]]
        y_ch, stay_ch = realized[name][chosen_label]
        least = ev["decision"].get("least")
        crec[name] = {"p_plan": plan["p_fail"], "y_plan": y_plan, "y_chosen": y_ch,
                      "stay_plan": stay_plan, "stay_chosen": stay_ch,
                      "pred_zero": 0.0, "pred_notice_eta": _notice_eta_pred(name, inp),
                      "status": ev["decision"]["status"], "notice": ev["notice"] is not None,
                      "y_notice_target": realized[name][least][0] if least else y_plan,
                      "conservative": bool(ev["decision"].get("conservative")),
                      "changed": chosen_label != plan["label"]}
    rec["cargo"] = crec
    return rec


def _brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _skill(bs, ref):
    return None if ref <= 1e-12 else round(1 - bs / ref, 3)


def summarize(records):
    out = {}
    labels_est = ["비확산", "확산 경계", "물량 주도형", "처리능력형", "원인 보류"]
    for scen in SCENARIOS:
        rs = [r for r in records if r["scenario"] == scen and r["variant"] == "main"]
        if not rs:
            continue
        n = len(rs)
        tp = sum(r["est_spread"] and r["truth_spread"] for r in rs)
        fp = sum(r["est_spread"] and not r["truth_spread"] for r in rs)
        fn = sum((not r["est_spread"]) and r["truth_spread"] for r in rs)
        confusion = {t: {e: sum(r["truth_cause"] == t and r["cause"] == e for r in rs) for e in labels_est}
                     for t in ["비확산", "물량 주도형", "처리능력형"]}
        decided = [r for r in rs if r["cause"] not in ("확산 경계", "원인 보류")]
        precision, recall = {}, {}
        for lab in ["물량 주도형", "처리능력형"]:
            said = [r for r in rs if r["cause"] == lab]
            true = [r for r in rs if r["truth_cause"] == lab]
            precision[lab] = None if not said else [sum(r["truth_cause"] == lab for r in said), len(said)]
            recall[lab] = None if not true else [sum(r["cause"] == lab for r in true), len(true)]

        def lead_stats(rows, key="first_alarm"):
            leads = [r["onset"] - r[key] for r in rows if r["onset"] is not None and r[key] is not None]
            if not leads:
                return {"n": 0, "median": None, "q25": None, "q75": None}
            return {"n": len(leads), "median": round(float(np.median(leads)), 2),
                    "q25": round(float(np.quantile(leads, 0.25)), 2), "q75": round(float(np.quantile(leads, 0.75)), 2)}
        events = [r for r in rs if r["onset"] is not None]
        missed = [r for r in events if r["first_alarm"] is None or r["first_alarm"] > 0]
        alarms = [r for r in rs if r["false_alarm"] is not None]
        clim = float(np.mean([r["truth_rate_hist"] for r in rs]))
        brier = float(np.mean([r["brier_hist"] for r in rs]))
        brier_clim = float(np.mean([r["truth_rate_hist"] * (1 - clim) ** 2 + (1 - r["truth_rate_hist"]) * clim ** 2 for r in rs]))
        masked = [r for r in records if r["scenario"] == scen and r["variant"] == "masked"]
        ai_off = [r for r in records if r["scenario"] == scen and r["variant"] == "ai_off"]
        off_worlds = {r["world"] for r in ai_off}
        misspec = {}
        for f in (1.3, 0.7):
            ms = [r for r in records if r["scenario"] == scen and r["variant"] == "misspec" and r["factor"] == f]
            if ms:
                misspec[f"beta_bar_x{f}"] = {
                    "worlds": len(ms),
                    "alarm_tp_fp_fn": [sum(r["est_spread"] and r["truth_spread"] for r in ms),
                                       sum(r["est_spread"] and not r["truth_spread"] for r in ms),
                                       sum((not r["est_spread"]) and r["truth_spread"] for r in ms)],
                    "coverage_80": round(float(np.mean([r["coverage_80"] for r in ms])), 3),
                    "inside_now": round(float(np.mean([r["inside_now"] for r in ms])), 3),
                    "rmse_filter": round(float(np.mean([r["rmse_filter"] for r in ms])), 3),
                    "lead": lead_stats(ms)}
        cargo_sum = {}
        for name in config.CARGO:
            cs = [r["cargo"][name] for r in rs]
            y = np.array([c["y_plan"] for c in cs])
            pm = np.array([c["p_plan"] for c in cs])
            loo = np.array([(y.sum() - y[i]) / max(len(y) - 1, 1) for i in range(len(y))])
            bs_m, bs_zero = _brier(pm, y), _brier([c["pred_zero"] for c in cs], y)
            bs_eta, bs_clim = _brier([c["pred_notice_eta"] for c in cs], y), _brier(loo, y)
            corr = None
            if pm.std() > 1e-9 and y.std() > 1e-9:
                corr = round(float(np.corrcoef(pm, y)[0, 1]), 3)
            notices = [c for c in cs if c["notice"]]
            fails = [c for c in cs if c["y_plan"] > 0.5]
            stays = [(c["stay_chosen"], c["stay_plan"]) for c in cs if c["changed"] and c["stay_chosen"] is not None and c["stay_plan"] is not None]
            cargo_sum[name] = {
                "predicted_fail_plan": round(float(pm.mean()), 3),
                "realized_fail_plan": round(float(y.mean()), 3),
                "realized_fail_rdelay": round(float(np.mean([c["y_chosen"] for c in cs])), 3),
                "changed_share": round(float(np.mean([c["changed"] for c in cs])), 3),
                "realized_stay_change_h": None if not stays else round(float(np.mean([a - b for a, b in stays])), 2),
                "recommended_share": round(float(np.mean([c["status"] == "recommended" for c in cs])), 3),
                "uncertain_share": round(float(np.mean([c["status"] == "uncertain" for c in cs])), 3),
                "notice_share": round(len(notices) / len(cs), 3),
                "conservative_share": round(float(np.mean([c["conservative"] for c in cs])), 3),
                "notice_precision": None if not notices else round(float(np.mean([c["y_notice_target"] for c in notices])), 3),
                "notice_recall": None if not fails else round(sum(c["notice"] for c in fails) / len(fails), 3),
                "brier_model": round(bs_m, 4), "brier_always_zero": round(bs_zero, 4),
                "brier_notice_eta": round(bs_eta, 4), "brier_climatology": round(bs_clim, 4),
                "skill_vs_always_zero": _skill(bs_m, bs_zero), "skill_vs_notice_eta": _skill(bs_m, bs_eta),
                "skill_vs_climatology": _skill(bs_m, bs_clim), "corr_pred_realized": corr,
            }
        notice_lead = lead_stats(rs, "first_notice") if scen in NOTICE_SCENARIOS else None
        out[scen] = {
            "label": config.SCENARIOS[scen]["label"], "worlds": n,
            "spread_alarm": {"tp": tp, "fp": fp, "fn": fn, "tn": n - tp - fp - fn},
            "cause_confusion": confusion,
            "hold_spread": sum(r["cause"] == "확산 경계" for r in rs),
            "hold_cause": sum(r["cause"] == "원인 보류" for r in rs),
            "cause_accuracy_decided": None if not decided else round(sum(r["cause"] == r["truth_cause"] for r in decided) / len(decided), 3),
            "label_precision": precision, "label_recall": recall,
            "brier_notice_rule": None if scen not in NOTICE_SCENARIOS else round(float(np.mean([r["brier_notice_rule"] for r in rs])), 4),
            "brier_skill_notice_rule": None if (scen not in NOTICE_SCENARIOS or brier_clim < 1e-3) else
                round(1 - float(np.mean([r["brier_notice_rule"] for r in rs])) / brier_clim, 3),
            "brier_skill_masked": None if (not masked or brier_clim < 1e-3) else
                round(1 - float(np.mean([r["brier_hist"] for r in masked])) / brier_clim, 3),
            "masked_no_alarm": None if not masked else sum(r["no_alarm"] for r in masked),
            "lead_time_h": lead_stats(rs), "lead_time_notice_rule_h": notice_lead,
            "lead_time_masked_h": lead_stats(masked) if masked else None,
            "masked_alarm_tp_fn": None if not masked else [sum(r["est_spread"] and r["truth_spread"] for r in masked),
                                                           sum((not r["est_spread"]) and r["truth_spread"] for r in masked)],
            "masked_worlds": len(masked),
            "events": len(events), "missed": len(missed),
            "alarms": len(alarms), "false_alarms": sum(r["false_alarm"] for r in alarms),
            "brier_p_spread": round(brier, 4), "brier_climatology": round(brier_clim, 4),
            "brier_skill": None if brier_clim < 1e-4 else round(1 - brier / brier_clim, 3),
            "truth_spread_rate": round(clim, 4),
            "coverage_80_mean": round(float(np.mean([r["coverage_80"] for r in rs])), 3),
            "coverage_80_last6h": round(float(np.mean([r["coverage_last6"] for r in rs])), 3),
            "coverage_80_now": round(float(np.mean([r["inside_now"] for r in rs])), 3),
            "bias_now_mean": round(float(np.mean([r["bias_now"] for r in rs])), 3),
            "rmse_filter_mean": round(float(np.mean([r["rmse_filter"] for r in rs])), 3),
            "rmse_smooth_mean": round(float(np.mean([r["rmse_smooth"] for r in rs])), 3),
            "rmse_filter_ai_off_mean": None if not ai_off else round(float(np.mean([r["rmse_filter"] for r in ai_off])), 3),
            "rmse_filter_ai_on_same_worlds": None if not ai_off else round(float(np.mean(
                [r["rmse_filter"] for r in rs if r["world"] in off_worlds])), 3),
            "ai_off_worlds": len(ai_off), "misspec": misspec,
            "ess_median": round(float(np.median([r["ess"] for r in rs])), 1),
            "cargo": cargo_sum,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", type=int, default=60)
    ap.add_argument("--ai-off-worlds", type=int, default=20)
    ap.add_argument("--masked-worlds", type=int, default=30)
    ap.add_argument("--misspec-worlds", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "validation.json"))
    ap.add_argument("--world0", type=int, default=WORLD0, help="첫 세계 번호(필터 잡음 조정은 1000~1023에서 했다)")
    ap.add_argument("--sigma-mult", type=float, default=None, help="filter_sigma_mult 조정 실험")
    ap.add_argument("--rejuv", type=float, default=None, help="rejuvenate_sd 조정 실험")
    args = ap.parse_args()
    extra = {}
    if args.sigma_mult is not None:
        extra["filter_sigma_mult"] = args.sigma_mult
    if args.rejuv is not None:
        extra["rejuvenate_sd"] = args.rejuv
    w0 = args.world0
    model = json.loads((ROOT / "docs" / "data" / "ai_model.json").read_text(encoding="utf-8"))
    tasks = [(s, w0 + w, model, {"kind": "main", "extra": extra}) for s in SCENARIOS for w in range(args.worlds)]
    tasks += [(s, w0 + w, model, {"kind": "ai_off", "extra": extra}) for s in SCENARIOS for w in range(args.ai_off_worlds)]
    tasks += [(s, w0 + w, model, {"kind": "masked", "extra": extra}) for s in NOTICE_SCENARIOS for w in range(args.masked_worlds)]
    tasks += [(s, w0 + w, model, {"kind": "misspec", "factor": f, "extra": extra}) for s in SCENARIOS for f in (1.3, 0.7)
              for w in range(args.misspec_worlds)]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        records = list(ex.map(one, tasks, chunksize=4))
    summary = summarize(records)
    out = {"description": "합성 세계 다중 검증(시나리오별 world 난수만 바꿈). 필터는 참 세계의 구조·정적 모수를 안다(오지정 실험 별도). 실증 성능이 아니다.",
           "worlds": args.worlds, "world_seeds": [w0, w0 + args.worlds - 1], "estimation_seed": config.DEFAULTS["seed"],
           "baselines": {"always_zero": f"도착예정시각 + 고정 여유 {BUFFER_H:.0f}시간(공지 미반영, 여유가 있으면 실패 0)",
                         "notice_eta": f"공지 반영 도착예정시각 + 고정 여유 {BUFFER_H:.0f}시간",
                         "climatology": "같은 시나리오 다른 세계들의 실현 실패율",
                         "notice_rule": "운영자 처리능력 공지·장비 고장 신호가 들어오면 즉시 경보"},
           "false_alarm_window_h": FA_WINDOW_H,
           "params": {"filter_sigma_mult": extra.get("filter_sigma_mult", config.DEFAULTS["filter_sigma_mult"]),
                      "rejuvenate_sd": extra.get("rejuvenate_sd", config.DEFAULTS["rejuvenate_sd"]),
                      "particles": config.DEFAULTS["particles"]},
           "misspec_note": "기준 전파율 beta_bar만 모든 거점에 같은 배율로 바꿨다(구조·다른 모수 오지정은 시험하지 않음).",
           "elapsed_s": round(time.time() - t0, 1), "scenarios": summary}
    pathlib.Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for s, v in summary.items():
        a = v["spread_alarm"]
        print(f"{s:18s} alarm {a['tp']}/{a['fp']}/{a['fn']}/{a['tn']} | lead {v['lead_time_h']['median']} notice {None if not v['lead_time_notice_rule_h'] else v['lead_time_notice_rule_h']['median']} masked {None if not v['lead_time_masked_h'] else v['lead_time_masked_h']['median']} {v['masked_alarm_tp_fn']} | "
              f"FA {v['false_alarms']}/{v['alarms']} miss {v['missed']}/{v['events']} | cause acc {v['cause_accuracy_decided']} hold {v['hold_spread']}/{v['hold_cause']} prec {v['label_precision']} rec {v['label_recall']} | BS notice {v['brier_skill_notice_rule']} masked {v['brier_skill_masked']} noalarm {v['masked_no_alarm']} | "
              f"BSS {v['brier_skill']} cov {v['coverage_80_mean']}/{v['coverage_80_last6h']}/{v['coverage_80_now']} bias {v['bias_now_mean']} | ai {v['rmse_filter_ai_off_mean']}->{v['rmse_filter_ai_on_same_worlds']}")
        print("     misspec", {k: (m['alarm_tp_fp_fn'], m['coverage_80'], m['inside_now']) for k, m in v['misspec'].items()})
        for k, c in v["cargo"].items():
            print(f"     {k:14s} pred {c['predicted_fail_plan']} real {c['realized_fail_plan']} rdelay {c['realized_fail_rdelay']} chg {c['changed_share']} stayΔ {c['realized_stay_change_h']} | "
                  f"notice {c['notice_share']} prec {c['notice_precision']} rec {c['notice_recall']} unc {c['uncertain_share']} | skill zero {c['skill_vs_always_zero']} eta {c['skill_vs_notice_eta']} clim {c['skill_vs_climatology']} corr {c['corr_pred_realized']}")
    print(f"saved {args.out} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
