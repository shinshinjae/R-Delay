"""AI 모델 f_omega 학습(2단계 하이브리드).

(1) 60일 합성 과거 구간(기상 변동·장비 고장·주말 포함)을 만들고 음이항·베타-이항 관측을 생성한다.
(2) 입자필터 + 고정시차 평활로 로그 전파율의 사후평균 E[z|y]를 얻는다(학습 레이블).
(3) 앞 42일로 릿지 회귀를 적합하고 뒤 18일로 시간순 평가한다(미래 정보 차단).
(2)~(3)을 반복한다: 직전 모델의 예측을 필터의 OU 평균으로 넣어 레이블이 사전분포 쪽으로
수축하는 편향을 줄인다(EM과 비슷한 반복 학습, 3회).
결과: docs/data/ai_model.json (계수, 평가 지표). 모든 자료는 합성이다.

실행: python3 scripts/train_ai.py
"""
from __future__ import annotations
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "py"))
from rdelay import ai, config, engine, sis  # noqa: E402

DAYS, TRAIN_DAYS = 60, 42


def synthetic_history(p, rng):
    dt = p["dt"]
    t = np.round(np.arange(0.0, DAYS * 24 + 1e-9, dt), 6)
    n = len(config.NODES)
    # 기상: 평균회귀 무작위 과정 + 이따금 폭풍
    wind = np.empty(len(t))
    wind[0] = 6.0
    for s in range(1, len(t)):
        wind[s] = wind[s - 1] + 0.05 * (6.0 - wind[s - 1]) * dt * 6 + 0.6 * np.sqrt(dt) * rng.standard_normal()
    for start in rng.uniform(0, DAYS * 24 - 30, size=10):
        dur = rng.uniform(12, 30)
        mask = (t >= start) & (t < start + dur)
        wind[mask] += rng.uniform(5, 9)
    wind = np.clip(wind, 0, 25)
    wave = np.clip(0.3 + 0.12 * wind + 0.2 * rng.standard_normal(len(t)), 0, 6)
    weekend = (((t // 24) % 7) >= 5).astype(float)
    equip = np.zeros((len(t), n))
    for k in range(n):  # 항공 반입(화물터미널 검색·하역 장비)도 포함해야 그 계수를 배울 수 있다
        for start in rng.uniform(0, DAYS * 24 - 12, size=5):
            equip[(t >= start) & (t < start + rng.uniform(4, 10)), k] = 1.0
    feats = np.zeros((len(t), n, len(ai.FEATURES)))
    feats[:, :, 0] = ((wind - 8.0) / 5.0)[:, None]
    feats[:, :, 1] = (wave - 1.0)[:, None]
    feats[:, :, 2] = weekend[:, None]
    feats[:, :, 3] = equip
    xi = np.exp(0.06 * rng.standard_normal((len(t), n)))  # 물동량 보정(평시 근처)
    return {"t_hist": t, "t_all": t, "xi": xi, "features": feats,
            "zbar_true": ai.true_zbar(feats), "zbar_ai": np.zeros((len(t), n)),
            "gamma_mult": np.ones((len(t), n)), "gate_closed": np.zeros((len(t), n))}


def main():
    p = dict(config.DEFAULTS)
    # 학습 레이블도 운영과 같은 필터 설정(입자 수·변동 여유·재생 잡음)으로 만든다.
    p["history_h"] = DAYS * 24
    rng = np.random.default_rng(7)
    C = sis.coupling(p["kappa"], p["tau0"])
    inp = synthetic_history(p, rng)
    truth = engine.simulate_truth(inp, p, C, rng)
    obs_idx, Y, H = engine.observe(truth, inp, p, rng)
    t = inp["t_hist"]
    train = t < TRAIN_DAYS * 24
    test = ~train
    model = None
    for it in range(3):
        inp["zbar_ai"] = ai.predict(model, inp["features"])
        pf = engine.particle_filter(inp, obs_idx, Y, H, p, C, np.random.default_rng(100 + it))
        label = pf["smooth"]["z"]  # E[z | y] (고정시차 평활)
        model = ai.fit_ridge(inp["features"][train], label[train], lam=2.0)
        print(f"iteration {it + 1}: equip coef", [round(r[3], 3) for r in model["coef"]])
    pred = ai.predict(model, inp["features"])
    metrics = {}
    for k, node in enumerate(config.NODES):
        yt, yp = label[test, k], pred[test, k]
        ss_res = float(np.sum((yt - yp) ** 2))
        ss_tot = float(np.sum((yt - yt.mean()) ** 2))
        true_k = inp["zbar_true"][test, k]
        corr_true = float(np.corrcoef(pred[test, k], true_k)[0, 1])
        metrics[node["id"]] = {
            "r2_vs_smoothed_label": round(1 - ss_res / ss_tot, 3),          # 운영에서도 잴 수 있는 지표
            "rmse_vs_true_mean": round(float(np.sqrt(np.mean((pred[test, k] - true_k) ** 2))), 3),  # 합성에서만
            "rmse_zero_model": round(float(np.sqrt(np.mean(true_k ** 2))), 3),  # AI 없이 z_bar = 0일 때
            "corr_vs_true_mean": round(corr_true, 3),
            "equip_coef_ratio": round(float(model["coef"][k][3] / ai.TRUE_COEF[k][3]), 3),  # 수축 정도
        }
    model.update({
        "description": "f_omega: 기상·주말·장비 상태 -> 로그 전파율 평균(z_bar). 릿지 회귀, 레이블=입자평활 E[z|y], 3회 반복 학습. 평활 레이블이 사전분포 쪽으로 수축하므로 계수가 참값보다 작게 추정된다.",
        "data": "합성 60일(앞 42일 학습, 뒤 18일 평가, 시간순 분할)",
        "metrics_test": metrics,
        "true_coef_for_reference": ai.TRUE_COEF.tolist(),
    })
    out = ROOT / "docs" / "data" / "ai_model.json"
    out.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"coef": model["coef"], "metrics": metrics}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
