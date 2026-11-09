"""단계 4: 실제 사례 재현. 정해 둔 규칙으로 평가(시험) 기간 사례 3개를 고르고, 시간축을 공유하는 그림과 JSON을 만든다(로컬 전용).

고르는 규칙(평가 기간 결과를 본 뒤 정한 '보여 주기용' 규칙이다. 모형·기준은 바꾸지 않는다. PROTOCOL 변경 이력에 적음)
  (a) 평가 기간 에피소드 중 12시간 초과 대기 선박이 가장 많은 것(같으면 72시간 안 최대 척수가 많은 것, 그다음 이른 것)
  (b) 검증·평가 기간에 신항 500km 안에 들어온 태풍 중 가장 가까이 온 것
  (c) 평가 기간 에피소드 중 시작 전 72시간 안에 R-Delay 경보가 없었던 것 가운데 선박이 가장 많은 것(같으면 (a)와 같은 순서)
  경보 = R2(R-Delay 재보정 확률) ≥ 경보 비율 10% 기준(평가 때와 같은 반올림 전 값). R1과 같은 경보다.
  창 = [시작 − 4일, 끝 + 2일](태풍은 500km 진입·이탈 시각), 2시간 격자. 평가 기간 밖·자료 끝 밖은 자른다.

로컬 전용 산출이지만 익명 집계 수준으로 적는다(지시서 단계 4가 요청한 사례 그림의 '12시간 초과 선박 도착(점)'만 배 단위이고
나머지는 부산신항 전체 집계):
  선박 이름·호출부호·터미널 없음. 에피소드 시작·끝과 12시간 초과 선박 도착은 시 단위로 내리고, 대기 시간은 구간(12~24·24~48·48~168시간)만 낸다.
  선행시간 등도 시 단위로 내린 시작 시각으로 잰다(분 단위 도착 시각이 드러나지 않게).
로컬 산출(공개 저장소에 올리지 않음: 사용자 결정 2026-11-07): realdata/results/cases.json, realdata/reports/fig_case_*.png
실행: python -m realdata.cases   (evaluate final 뒤)
"""
from __future__ import annotations

import json
import platform

import numpy as np
import pandas as pd

from . import baselines as B
from . import features as F
from . import metrics as M
from .io import load
from .paths import CACHE, REPORTS, RESULTS

PAD_BEFORE = pd.Timedelta(days=4)
PAD_AFTER = pd.Timedelta(days=2)
RDELAY_RUN = CACHE / "rdelay_all_W24_P512.parquet"
OBS = CACHE / "rdelay_obs.parquet"


def _round(v, nd=3):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), nd)


def load_inputs() -> dict:
    r = json.loads((RESULTS / "rdelay.json").read_text(encoding="utf-8"))
    audit = json.loads((RESULTS / "audit.json").read_text(encoding="utf-8"))
    P = pd.read_parquet(CACHE / "pred_final.parquet")
    R = pd.read_parquet(RDELAY_RUN)
    O = pd.read_parquet(OBS)
    voy = load("voyages")
    asos, buoy = load("asos"), load("buoy")
    bus = asos[asos["stn"].astype(str) == "159"].set_index("t")["wind"].sort_index()
    geo = buoy[buoy["stn"].astype(str) == "22104"].set_index("t")["wave_sig"].sort_index()
    # 경보 기준: rdelay.json에는 4자리로 반올림해 적혀 있으므로 평가 때와 같이 다시 계산한다(figures._leads와 같은 방법)
    m = B.period_masks(P.index, pd.Timestamp(r["tau_end"]))
    thr = M.thresholds(P["R2"][m["val"]], P["y"][m["val"]], float(P["y"][m["train_fit"]].mean()))["rate_10"]
    assert abs(thr - r["models"]["R2"]["thresholds"]["rate_10"]) < 1e-4, thr
    return {"r": r, "audit": audit, "P": P, "R": R, "O": O, "voy": voy, "wind": bus, "wave": geo, "thr": thr}


def _episode_table(d: dict, t0, t1) -> pd.DataFrame:
    voy = d["voy"]
    q = voy[(voy["wait_h"] > F.WAIT_X) & (voy["wait_h"] <= F.WAIT_CAP)].sort_values("arrival_t")
    eps = M.episodes(q["arrival_t"])
    eps = eps[(eps["start"] >= t0) & (eps["start"] < t1)].reset_index(drop=True)
    at = d["P"].index[d["P"]["R2"].to_numpy() >= d["thr"]]
    rows = []
    for _, e in eps.iterrows():
        a = q.loc[(q["arrival_t"] >= e["start"]) & (q["arrival_t"] <= e["end"]), "arrival_t"].to_numpy()
        best72 = max(int(((a >= x) & (a < x + np.timedelta64(72, "h"))).sum()) for x in a)
        w = at[(at >= e["start"] - pd.Timedelta(hours=M.LEAD_WINDOW_H)) & (at < e["start"])]
        rows.append({"start": e["start"], "end": e["end"], "ships": int(e["ships"]), "best72": best72,
                     "lead_h": None if len(w) == 0 else (e["start"] - w[0]) / pd.Timedelta(hours=1)})
    return pd.DataFrame(rows)


def select_cases(d: dict) -> list[dict]:
    r = d["r"]
    t0 = pd.Timestamp(B.PERIODS["test"][0])
    t1 = pd.Timestamp(r["tau_end"]) + pd.Timedelta(hours=2)
    E = _episode_table(d, t0, t1)
    key = ["ships", "best72", "start"]
    asc = [False, False, True]
    a = E.sort_values(key, ascending=asc).iloc[0]
    c = E[E["lead_h"].isna()].sort_values(key, ascending=asc).iloc[0]
    v0 = pd.Timestamp(B.PERIODS["val"][0])
    ty = [t for t in d["audit"]["typhoon"]["within_500km"] if pd.Timestamp(t["end"]) >= v0 and pd.Timestamp(t["start"]) < t1]
    b = min(ty, key=lambda t: (t["min_km"], t["start"]))
    a, c = a.copy(), c.copy()
    for e in (a, c):  # 결과에 적는 시작·끝은 시 단위로 내림(첫·마지막 초과 선박의 분 단위 도착 시각을 내지 않음)
        e["start"], e["end"] = pd.Timestamp(e["start"]).floor("h"), pd.Timestamp(e["end"]).floor("h")
    return [
        {"id": "a", "kind": "most_ships", "title": "12시간 초과 대기가 가장 몰린 에피소드",
         "rule": "평가 기간 에피소드 중 12시간 초과 대기 선박이 가장 많은 것", "start": a["start"], "end": a["end"]},
        {"id": "b", "kind": "typhoon", "title": f"태풍 {b['name']}({b['year']}년 {b['num']}호)이 가장 가까이 온 때",
         "rule": "검증·평가 기간에 신항 500km 안에 들어온 태풍 중 가장 가까이 온 것", "start": pd.Timestamp(b["start"]),
         "end": pd.Timestamp(b["end"]), "typhoon": {"name": b["name"], "year": b["year"], "num": b["num"], "min_km": b["min_km"]}},
        {"id": "c", "kind": "missed", "title": "경보가 시작을 놓친 에피소드",
         "rule": "평가 기간 에피소드 중 시작 전 72시간 안에 경보가 없었던 것 가운데 선박이 가장 많은 것", "start": c["start"], "end": c["end"]},
    ]


def case_series(d: dict, case: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    r, P, R, O = d["r"], d["P"], d["R"], d["O"]
    lo = max(case["start"] - PAD_BEFORE, pd.Timestamp(B.PERIODS["test"][0]))
    hi = min(case["end"] + PAD_AFTER, pd.Timestamp(r["tau_end"]))
    grid = F.tau_grid(lo.ceil("2h"), hi.floor("2h"))
    s = pd.DataFrame(index=grid)
    s["anchor"] = O["anchor_np"].reindex(grid)
    s["prob"] = P["R2"].reindex(grid)
    s["p_rd1"] = R["p_rd1"].reindex(grid)
    s["y"] = P["y"].reindex(grid)
    s["alarm"] = s["prob"] >= d["thr"]
    s["wind"] = F._rolling_at(d["wind"], grid, 2, "max")
    s["wave"] = F._rolling_at(d["wave"], grid, 2, "max")
    nl = O["n_L"].reindex(grid)
    s["road"] = (O["H_L"].reindex(grid) / nl).where(nl > 0)
    voy = d["voy"]
    q = voy[(voy["wait_h"] > F.WAIT_X) & (voy["wait_h"] <= F.WAIT_CAP)
            & (voy["arrival_t"] > grid[0]) & (voy["arrival_t"] <= grid[-1] + pd.Timedelta(hours=2))]
    arr = pd.DataFrame({"t": q["arrival_t"].dt.floor("h"), "wait_h": q["wait_h"], "band": q["wait_h"].map(wait_band)})
    return s, arr.sort_values("t").reset_index(drop=True)


WAIT_BANDS = ["12~24시간", "24~48시간", "48~168시간"]


def wait_band(w: float) -> str:
    """결과에 적는 대기 시간 구간(배 하나의 정확한 대기 시간은 내지 않는다)."""
    return WAIT_BANDS[0] if w <= 24 else (WAIT_BANDS[1] if w <= 48 else WAIT_BANDS[2])


def case_facts(d: dict, case: dict, s: pd.DataFrame, arr: pd.DataFrame) -> dict:
    """사례 글에 쓰는 숫자(모두 이 함수에서만 만든다)."""
    thr = d["thr"]
    st = case["start"]  # 시 단위로 내린 시작(결과에 적는 값과 같게)
    at = s.index[s["alarm"].to_numpy()]
    w72 = at[(at >= st - pd.Timedelta(hours=72)) & (at < st)]
    w24 = at[(at >= st - pd.Timedelta(hours=24)) & (at < st)]
    after = at[at >= st]
    inside = (arr["t"] >= st) & (arr["t"] <= case["end"])
    y = s["y"]
    hits = int((s["alarm"] & (y == 1)).sum())
    ev_wind, ev_wave = d["wind"][case["start"]:case["end"]], d["wave"][case["start"]:case["end"]]  # 사건 구간 안의 시간 관측
    bands = arr.loc[inside, "band"].value_counts()
    return {
        "ships_event": int(inside.sum()), "ships_window": int(len(arr)),
        "wait_bands_event": {b: int(bands.get(b, 0)) for b in WAIT_BANDS},
        "wait_h_median": _round(arr.loc[inside, "wait_h"].median(), 0) if inside.any() else None,
        "wait_h_max": _round(arr.loc[inside, "wait_h"].max(), 0) if inside.any() else None,
        "wait_h_max_window": _round(arr["wait_h"].max(), 0) if len(arr) else None,
        "alarm_threshold": _round(thr, 4), "alarm_tau": int(s["alarm"].sum()), "tau": int(len(s)),
        "alarm_tau_correct": hits, "y_tau": int((y == 1).sum()),
        "first_alarm_lead_h": _round((st - w72[0]) / pd.Timedelta(hours=1), 1) if len(w72) else None,
        "first_alarm_after_start_h": _round((after[0] - st) / pd.Timedelta(hours=1), 1) if len(after) else None,
        "alarm_in_24h_before": bool(len(w24)), "p_rd1_max": _round(s["p_rd1"].max(), 4),
        "prob_max": _round(s["prob"].max(), 3), "anchor_max": int(s["anchor"].max()),
        "wind_max": _round(s["wind"].max(), 1), "wave_max": _round(s["wave"].max(), 1),
        "wind_max_event": _round(ev_wind.max(), 1) if len(ev_wind) else None, "wave_max_event": _round(ev_wave.max(), 1) if len(ev_wave) else None,
        "road_mean": _round(s["road"].mean(), 3), "road_share_missing": _round(float(s["road"].isna().mean()), 3),
    }


# ---------- 그림 ----------
def _font(plt):
    plt.rcParams["font.family"] = "Malgun Gothic" if platform.system() == "Windows" else "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False


def fig_case(case: dict, s: pd.DataFrame, arr: pd.DataFrame, facts: dict, attribution: dict | None, path, ref: dict | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    _font(plt)
    INK, MUTED, BLUE, RED, GRAY = "#222222", "#666666", "#1d6ac8", "#c0392b", "#8a8f98"
    fig, ax = plt.subplots(6, 1, figsize=(10, 10.5), dpi=200, sharex=True, gridspec_kw={"height_ratios": [1.1, 1.0, 1.3, 0.75, 0.75, 0.75]})
    t = s.index
    GOLD = "#b8901c"
    for a in ax:
        for x in (case["start"], case["end"]):
            a.axvline(x, color=GOLD, lw=1.0, alpha=0.9)
        for sp in ("top", "right"):
            a.spines[sp].set_visible(False)
        a.tick_params(labelsize=8, colors=INK)
        a.grid(alpha=0.25)
    ax[0].step(t, s["anchor"], where="post", color=INK, lw=1.2)
    ax[0].set_ylabel("정박지 대기\n(척)", fontsize=8.5)
    lab = ("500km 진입", "이탈") if case["kind"] == "typhoon" else ("시작", "끝")
    for x, tx in zip((case["start"], case["end"]), lab):
        ax[0].annotate(tx, (x, 1.0), xycoords=("data", "axes fraction"), xytext=(3, -2), textcoords="offset points",
                       fontsize=7.5, color=GOLD, va="top")
    if len(arr):  # 배 하나의 정확한 대기 시간은 내지 않고 구간 줄에 점으로만(도착은 시 단위로 내림)
        ax[1].scatter(arr["t"], arr["band"].map({b: i for i, b in enumerate(WAIT_BANDS)}), s=22, color=RED, zorder=3)
    ax[1].set_yticks(range(len(WAIT_BANDS)))
    ax[1].set_yticklabels(WAIT_BANDS, fontsize=7.5)
    ax[1].set_ylim(-0.6, len(WAIT_BANDS) - 0.4)
    ax[1].set_ylabel("12시간 넘게\n기다린 배", fontsize=8.5)
    yy = s["y"].fillna(0).to_numpy() > 0
    ax[2].fill_between(t, 0, 0.06, where=yy, step="post", color=GRAY, alpha=0.6, lw=0, label="정답: 24시간 안에 초과 대기 선박 도착")
    ax[2].fill_between(t, 0.06, 1, where=s["alarm"].to_numpy(), step="post", color=BLUE, alpha=0.10, lw=0, label="경보(경보 비율 10% 기준)")
    ax[2].plot(t, s["prob"], color=BLUE, lw=1.4, label="R-Delay 확률(재보정)")
    ax[2].axhline(facts["alarm_threshold"], color=RED, lw=0.9, ls="--")
    pmax = (ref or {}).get("p_rd1_max")
    ax[2].plot(t, s["p_rd1"], color=MUTED, lw=1.0, ls=":",
               label="P(R_d>1) — 0.5를 넘은 적 없음" + (f"(평가 기간 최대 {pmax:.4f})" if pmax is not None else ""))
    ax[2].set_ylim(0, max(0.7, float(s["prob"].max()) + 0.1))
    ax[2].set_ylabel("확률", fontsize=8.5)
    ax[2].legend(fontsize=7, frameon=False, loc="upper left", ncol=2)
    ax[3].plot(t, s["wind"], color=INK, lw=1.0)
    ax[3].set_ylabel("풍속 부산\n(m/s)", fontsize=8.5)
    ax[4].plot(t, s["wave"], color=INK, lw=1.0)
    ax[4].set_ylabel("유의파고\n거제도(m)", fontsize=8.5)
    ax[5].plot(t, s["road"] * 100, color=INK, lw=1.0)
    ax[5].set_ylabel("도로 혼잡\n콘존(%)", fontsize=8.5)
    ax[5].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax[5].xaxis.set_major_locator(mdates.DayLocator(interval=max(1, int(len(t) / 12 / 8))))
    ttl = f"사례 {case['id']}: {case['title']} — {case['start']:%Y-%m-%d %H시} ~ {case['end']:%m-%d %H시}"
    fig.suptitle(ttl, fontsize=11, x=0.01, ha="left", color=INK)
    lead = facts["first_alarm_lead_h"]
    note = (f"금색 세로선 = {'태풍 500km 진입·이탈' if case['kind'] == 'typhoon' else '에피소드 시작·끝(시 단위로 내림)'} · 초과 대기 {facts['ships_event']}척(그림 기간 {facts['ships_window']}척) · "
            f"시작 전 72시간 안 첫 경보 {'없음' if lead is None else f'{lead:.0f}시간 전'} · 경보 τ {facts['alarm_tau']}/{facts['tau']}"
            + (f" · 태풍 500km 안 부산 풍속 최대 {facts['wind_max_event']}m/s, 거제도 파고 최대 {facts['wave_max_event']}m" if case["kind"] == "typhoon" else ""))
    if attribution:
        import textwrap
        note += "\n" + "\n".join(textwrap.wrap(f"모형상 배분(원인 판정): {attribution['text']}", width=104))
    fig.text(0.01, 0.005, note, fontsize=7.8, color=MUTED, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.012 * (note.count("\n") + 1) + 0.01, 1, 0.97))
    fig.savefig(path)
    plt.close(fig)


# ---------- 원인 판정(모형상 배분, 시험을 본 뒤 계산한 설명용) ----------
ATTR_CACHE = CACHE / "attribution_test_W24_P512.parquet"
HOLD_LOW = 0.35      # 앱의 원인 판정 보류 구간 하한(config.hold_band): P(R_d>1)이 이보다 낮으면 '비확산'
FACTOR_MIN = 0.02    # 앱 규칙(api.py congestion_factor): R_d를 이만큼 넘게 낮추는 요인만 '주요인'으로 적는다


def attribution_all(d: dict, jobs: int = 8) -> pd.DataFrame:
    """시험 기간 모든 τ에서 요인별 반사실 R_d(rdelay_real.attribution_tau). 같은 τ 묶음이면 캐시를 다시 쓴다."""
    from . import rdelay_real as RR
    R = d["R"]
    taus = [t for t in R.index[R["ok"].to_numpy(bool)]
            if pd.Timestamp(B.PERIODS["test"][0]) <= t <= pd.Timestamp(d["r"]["tau_end"])]
    if ATTR_CACHE.exists():
        A = pd.read_parquet(ATTR_CACHE)
        # τ 묶음만이 아니라 본 실행의 rd_mean과도 맞아야 다시 쓴다(R-Delay를 다시 돌렸으면 새로 계산)
        if list(A.index) == list(taus) and "rd_zmean" in A and np.allclose(A["rd_now"].to_numpy(), R.loc[A.index, "rd_mean"].to_numpy()):
            return A
    params = json.loads(RR.PARAMS_PATH.read_text(encoding="utf-8"))
    A = RR.run_many(taus, 24, 512, jobs, d["O"], params, fn=RR.attribution_tau)
    A = A[A["ok"]].drop(columns=["ok"])
    assert np.allclose(A["rd_now"].to_numpy(), R.loc[A.index, "rd_mean"].to_numpy()), "사후 입자가 본 실행과 다름"
    A.to_parquet(ATTR_CACHE)
    return A


def attribution_reference(A: pd.DataFrame) -> dict:
    """시험 기간 전체의 R_d 분포와 요인별 차이(평시 기준). 문서·웹에 인용하는 R_d 숫자는 여기서만 나온다."""
    dv, dzm, dzu = A["rd_now"] - A["rd_noxi"], A["rd_zmean"] - A["rd_noz"], A["rd_now"] - A["rd_zmean"]
    return {"tau": int(len(A)), "rd_median": _round(A["rd_now"].median(), 3), "rd_q05": _round(A["rd_now"].quantile(0.05), 3),
            "rd_q95": _round(A["rd_now"].quantile(0.95), 3), "rd_max": _round(A["rd_now"].max(), 3), "p_rd1_max": _round(A["p_rd1"].max(), 4),
            "corr_rd_xi": _round(np.corrcoef(A["rd_now"], A["xi_S"])[0, 1], 3),
            "volume_median": _round(dv.median(), 4), "volume_share_negative": _round(float((dv < 0).mean()), 3),
            "beta_shift_median": _round(dzm.median(), 4), "beta_uncertainty_median": _round(dzu.median(), 4)}


def attribution_summary(A: pd.DataFrame, case: dict, ref: dict, x_q90: float) -> dict:
    """사례 구간 [시작 − 24h, 끝]에서 앱과 같은 규칙으로 원인 판정을 요약한다.

    요인별 값 = 그 요인을 평시로 되돌렸을 때 R_d가 내려가는 양(+는 그 요인이 R_d를 올리고 있었다는 뜻, −는 낮추고 있었다는 뜻).
    전파율은 z 평균 이동 몫만 요인으로 본다(불확실성 몫은 따로 적음). 앱처럼 적체(사후 x_S > 학습 90분위)가 있을 때만 주요인을 적는다."""
    lo, hi = case["start"] - pd.Timedelta(hours=24), case["end"]
    a = A[(A.index >= lo) & (A.index <= hi)]
    vol = float((a["rd_now"] - a["rd_noxi"]).mean())
    zsh = float((a["rd_zmean"] - a["rd_noz"]).mean())
    zun = float((a["rd_now"] - a["rd_zmean"]).mean())
    p_max = float(a["p_rd1"].max())
    verdict = "비확산" if p_max < HOLD_LOW else "판정 필요"
    congested = bool((a["x_S"] > x_q90).any())
    drops = {"물동량": vol, "전파율 상승": zsh}
    main = max(drops, key=drops.get)
    factor = main if (congested and drops[main] > FACTOR_MIN) else None
    out = {"tau": int(len(a)), "rd_mean": _round(a["rd_now"].mean(), 3), "rd_max": _round(a["rd_now"].max(), 3), "p_rd1_max": _round(p_max, 4),
           "verdict": verdict, "volume": _round(vol, 4), "beta_shift": _round(zsh, 4), "beta_uncertainty": _round(zun, 4),
           "capacity": None, "congested": congested, "main_factor": factor}
    def eff(name: str, v: float) -> str:
        return (f"{name}은 R_d를 {v:.3f} 올렸" if v >= 0.0005 else
                f"{name}은 R_d를 {-v:.3f} 낮췄" if v <= -0.0005 else f"{name}은 R_d를 거의 바꾸지 않았")

    def f3(v: float) -> str:
        return "0.000" if abs(v) < 0.0005 else f"{v:+.3f}"
    out["text"] = (f"번짐 지수 R_d 평균 {out['rd_mean']:.2f}(최대 {out['rd_max']:.2f}), P(R_d>1) 최대 {out['p_rd1_max']:.4f} → 앱 규칙상 '{verdict}'. "
                   f"한 요인씩 평시 값으로 되돌려 보면 {eff('물동량', vol)}고 {eff('전파율(평균 이동)', zsh)}다"
                   f"(평가 기간 전체 중앙값은 각각 {f3(ref['volume_median'])}, {f3(ref['beta_shift_median'])}). 처리능력은 공지 자료가 없어 판정하지 않았다. "
                   + (f"주요인 = {factor}." if factor else ("적체가 있었지만 0.02 넘게 바꾼 요인이 없어 주요인은 없다." if congested
                                                         else "적체 기준(정박지 지연 비율 학습 90분위)을 넘지 않아 주요인을 적지 않는다.")))
    return out


# ---------- 내보내기 ----------
def _arr(v: pd.Series, nd: int | None) -> list:
    out = []
    for x in v.to_numpy(dtype=float):
        out.append(None if not np.isfinite(x) else (int(round(x)) if nd is None else round(float(x), nd)))
    return out


def export_case(case: dict, s: pd.DataFrame, arr: pd.DataFrame, facts: dict, att: dict) -> dict:
    c = {k: v for k, v in case.items() if k not in ("start", "end")}
    c.update({"start": f"{case['start']:%Y-%m-%d %H:00}", "end": f"{case['end']:%Y-%m-%d %H:00}", "facts": facts, "attribution": att})
    c["series"] = {"t0": f"{s.index[0]:%Y-%m-%d %H:%M}", "step_h": 2, "n": int(len(s)),
                   "anchor": _arr(s["anchor"], None), "prob": _arr(s["prob"], 3), "p_rd1": _arr(s["p_rd1"], 4),
                   "y": _arr(s["y"], None), "alarm": [int(bool(a)) for a in s["alarm"]],
                   "wind": _arr(s["wind"], 1), "wave": _arr(s["wave"], 1), "road": _arr(s["road"], 3)}
    c["arrivals"] = [{"t": f"{t:%Y-%m-%d %H:00}", "band": b} for t, b in zip(arr["t"], arr["band"])]
    return c


def main():
    import time
    t0 = time.time()
    d = load_inputs()
    A = attribution_all(d)
    ref = attribution_reference(A)
    x_q90 = json.loads((RESULTS / "rdelay_params.json").read_text(encoding="utf-8"))["x_S_q90"]
    out = {"generated_kst": time.strftime("%Y-%m-%d %H:%M"), "protocol": d["r"]["protocol"],
           "note": "평가 기간 결과를 본 뒤 정한 '보여 주기용' 사례다. 모형·경보 기준은 그대로이고, 원인 판정은 설명용(사후) 계산이다. "
                   "선박 이름·호출부호·터미널은 없다. 그림용 2시간 간격 집계는 사례 3개 구간에만 담았다. "
                   "12시간 넘게 기다린 배는 도착 시각을 시 단위로 내리고 대기 시간은 구간(12~24·24~48·48~168시간)으로만 적었다. "
                   "에피소드 시작·끝도 시 단위로 내렸고, 선행시간은 그 시각으로 쟀다.",
           "wait_bands": WAIT_BANDS,
           "alarm": {"model": "R2", "threshold": round(d["thr"], 4),
                     "rule": "경보 비율 10% 기준(학습+검증으로 다시 맞춘 R2의 검증 기간 점수 90분위). R1과 같은 경보"},
           "rd_reference": ref, "cases": []}
    for case in select_cases(d):
        s, arr = case_series(d, case)
        facts = case_facts(d, case, s, arr)
        att = attribution_summary(A, case, ref, x_q90)
        fig_case(case, s, arr, facts, att, REPORTS / f"fig_case_{case['id']}.png", ref)
        out["cases"].append(export_case(case, s, arr, facts, att))
        print(case["id"], case["title"], json.dumps(facts, ensure_ascii=False))
        print("  ", att["text"])
    txt = json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False)
    (RESULTS / "cases.json").write_text(txt, encoding="utf-8")
    print(f"→ {RESULTS / 'cases.json'} ({len(txt.encode()) / 1024:.0f} KB) · R_d 기준값 {ref} · {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
