"""단계 6: 발표용 산출물 → presentation/(gitignore). 그림은 16:9(1920×1080), 한글 글꼴.

  s1_compare.png  평가 기간 PR-AUC(주요 모형 5개 + 정보 없음 기준선)
  s2_alarm.png    경보 비율 10%에서 정밀도와 '시작을 미리 잡은 에피소드'
  s3_case_a.png   사례 a(가장 몰린 에피소드) · s3_case_c.png 사례 c(경보가 시작을 놓침)
  s4_where.png    계절·공휴일별 PR-AUC
  s5_rd.png       실자료의 번짐 지수 R_d 분포(1을 넘은 적 없음)
  business.json   연간 12시간 초과 대기 척수·초과 시간 합계(실측, 공개 집계)
숫자는 realdata/results/*.json(scripts/build_backtest.py가 만든 backtest.json 포함)·라벨 캐시에서만 가져온다(지시서 규칙 7).
Clarksons 자료는 쓰지 않는다. 입력과 산출물은 모두 로컬 전용이다(공개 저장소에 올리지 않음).
실행: python -m realdata.slides   (evaluate final·cases·scripts/build_backtest.py 뒤)
"""
from __future__ import annotations

import json
import platform

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from . import features as F  # noqa: E402
from .io import load  # noqa: E402
from .paths import CACHE, REPO, RESULTS  # noqa: E402

OUT = REPO / "presentation"
INK, MUTED, GRID, GRAY, BLUE, GREEN, RED, GOLD = "#1f2328", "#6b7280", "#e5e7eb", "#9aa0a8", "#1d6ac8", "#0f8a5f", "#c0392b", "#b8901c"
W, H, DPI = 12.8, 7.2, 150  # 1920×1080


def _font():
    plt.rcParams["font.family"] = "Malgun Gothic" if platform.system() == "Windows" else "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 15


def _clean(ax):
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(colors=INK)
    ax.grid(alpha=0.35, color=GRID)


def _bt() -> dict:
    p = RESULTS / "backtest.json"  # 로컬 전용(gitignore)
    if not p.exists():
        raise SystemExit("[중단] realdata/results/backtest.json이 없습니다. python scripts/build_backtest.py를 먼저 돌리세요.")
    return json.loads(p.read_text(encoding="utf-8"))


def s1_compare(bt: dict):
    keys = ["S0", "S1", "S3", "R1", "R3b"]
    m = {x["key"]: x for x in bt["models"]}
    label = {"S0": "기후값(요일·시각 평균)", "S1": "지속성(지금 정박지 대기)", "S3": "스케줄(배별 기항 간격)", "R1": "R-Delay", "R3b": "R-Delay + 스케줄"}
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    for i, k in enumerate(keys[::-1]):
        v, (lo, hi) = m[k]["pr_auc"], m[k]["pr_ci"]
        c = BLUE if k.startswith("R") else GRAY
        ax.errorbar(v, i, xerr=[[v - lo], [hi - v]], fmt="o", color=c, ms=12, lw=3, capsize=6)
        ax.text(hi + 0.008, i, f"{v:.3f}", va="center", fontsize=17, color=INK, weight="bold" if k == "R1" else None)
    base = bt["test_pos_rate"]
    ax.axvline(base, color=RED, ls="--", lw=2)
    ax.text(base, len(keys) - 0.35, f"아무 정보 없이 찍을 때 {base:.2f}", color=RED, fontsize=14, ha="left", va="bottom")
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels([label[k] for k in keys[::-1]], fontsize=17)
    ax.set_ylim(-0.6, len(keys) - 0.1)
    ax.set_xlim(0.22, 0.56)
    ax.set_xlabel("PR-AUC(높을수록 잘 맞힘) · 막대 = 95% 구간(주 단위 블록 부트스트랩)", color=MUTED, fontsize=14)
    p0, p1 = bt["periods"]["test"]
    ax.set_title(f"24시간 안에 '12시간 넘게 기다릴 배'가 올지 — 평가 기간 {p0[:7]}~{p1[:7]}(한 번만 계산)", loc="left", fontsize=19, color=INK)
    _clean(ax)
    fig.tight_layout()
    fig.savefig(OUT / "s1_compare.png")
    plt.close(fig)


def s2_alarm(bt: dict):
    keys = ["S0", "S1", "S3", "R1", "R3b"]
    m = {x["key"]: x for x in bt["models"]}
    short = {"S0": "기후값", "S1": "지속성", "S3": "스케줄", "R1": "R-Delay", "R3b": "R-Delay\n+스케줄"}
    fig, axes = plt.subplots(1, 2, figsize=(W, H), dpi=DPI)
    x = np.arange(len(keys))
    cols = [BLUE if k.startswith("R") else GRAY for k in keys]
    prec = [m[k]["alarm"]["precision"] for k in keys]
    axes[0].bar(x, prec, color=cols, width=0.6)
    for i, v in enumerate(prec):
        axes[0].text(i, v + 0.01, f"{v:.2f}", ha="center", fontsize=16, color=INK)
    axes[0].axhline(bt["test_pos_rate"], color=RED, ls="--", lw=1.5)
    axes[0].set_title("경보가 맞을 확률(정밀도)", loc="left", fontsize=18)
    axes[0].set_ylim(0, 0.7)
    eps = m["R1"]["alarm"]["episodes"]
    c72 = [m[k]["alarm"]["caught_72h"] for k in keys]
    ccv = [m[k]["alarm"]["caught_cover"] for k in keys]
    on = [m[k]["alarm"]["onsets"] for k in keys]
    axes[1].bar(x - 0.18, c72, 0.34, color=cols, alpha=0.45, label="시작 전 72시간 안 경보")
    axes[1].bar(x + 0.18, ccv, 0.34, color=cols, label="경보 창이 시작을 덮음")
    for i in range(len(keys)):
        axes[1].text(x[i], max(c72[i], ccv[i]) + 1.5, f"경보 {on[i]}회", ha="center", fontsize=12, color=MUTED)
    axes[1].set_title(f"대기 에피소드 {eps}개 중 시작을 미리 잡은 수", loc="left", fontsize=18)
    axes[1].set_ylim(0, eps)
    axes[1].legend(fontsize=12, frameon=False, loc="upper right")
    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels([short[k] for k in keys], fontsize=15)
        _clean(ax)
    rates = [m[k]["alarm"]["rate"] for k in keys]
    fig.suptitle(f"경보 비율 10% 목표(실제 {min(rates):.0%}~{max(rates):.0%}) — R-Delay는 울리면 잘 맞지만, 대기가 시작되기 전에는 잘 울리지 않는다",
                 x=0.01, ha="left", fontsize=17, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "s2_alarm.png")
    plt.close(fig)


def s3_case(bt: dict, cid: str):
    c = next(x for x in bt["cases"] if x["id"] == cid)
    s = c["series"]
    t = pd.date_range(s["t0"], periods=s["n"], freq=f"{s['step_h']}h")
    st, en = pd.Timestamp(c["start"]), pd.Timestamp(c["end"])
    fig, ax = plt.subplots(3, 1, figsize=(W, H), dpi=DPI, sharex=True, gridspec_kw={"height_ratios": [1, 1, 1.3]})
    for a in ax:
        for xx in (st, en):
            a.axvline(xx, color=GOLD, lw=1.6)
        _clean(a)
    ax[0].step(t, np.array(s["anchor"], dtype=float), where="post", color=INK, lw=2)
    ax[0].set_ylabel("정박지 대기(척)", fontsize=14)
    arr = pd.DataFrame(c["arrivals"])
    bands = bt["case_bands"]
    if len(arr):
        ax[1].scatter(pd.to_datetime(arr["t"]), arr["band"].map({b: i for i, b in enumerate(bands)}), s=60, color=RED, zorder=3)
    ax[1].set_yticks(range(len(bands)))
    ax[1].set_yticklabels(bands, fontsize=11)
    ax[1].set_ylim(-0.6, len(bands) - 0.4)
    ax[1].set_ylabel("12시간 넘게\n기다린 배", fontsize=14)
    prob = np.array([np.nan if v is None else v for v in s["prob"]], dtype=float)
    al = np.array(s["alarm"], dtype=bool)
    yy = np.array([0 if v is None else v for v in s["y"]], dtype=int) > 0
    ax[2].fill_between(t, 0, 0.05, where=yy, step="post", color=GRAY, alpha=0.8, lw=0, label="정답: 24시간 안에 초과 대기 배 도착")
    ax[2].fill_between(t, 0.05, 0.7, where=al, step="post", color=BLUE, alpha=0.12, lw=0, label="경보")
    ax[2].plot(t, prob, color=BLUE, lw=2.4, label="R-Delay 확률")
    ax[2].axhline(c["facts"]["alarm_threshold"], color=RED, ls="--", lw=1.5, label="경보 기준")
    ax[2].set_ylim(0, 0.7)
    ax[2].set_ylabel("확률", fontsize=14)
    ax[2].legend(fontsize=11.5, frameon=False, loc="upper left", ncol=2)
    ax[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    f = c["facts"]
    lead = f["first_alarm_lead_h"]
    after = f["first_alarm_after_start_h"]
    sub = (f"초과 대기 {f['ships_event']}척 · 시작 전 72시간 안 첫 경보 {'없음' if lead is None else f'{lead:.1f}시간 전'}"
           + ("" if after is None else f" · 시작 뒤 첫 경보 {after:.1f}시간 뒤"))
    fig.suptitle(f"사례: {c['title']} ({st:%Y-%m-%d}~{en:%m-%d})\n{sub}", x=0.01, ha="left", fontsize=17, color=INK)
    fig.tight_layout()
    fig.savefig(OUT / f"s3_case_{cid}.png")
    plt.close(fig)


def s4_where(bt: dict):
    keys = ["S1", "S3", "R1", "R3b"]
    name = {"S1": "지속성", "S3": "스케줄", "R1": "R-Delay", "R3b": "R-Delay+스케줄"}
    groups = [g for g in bt["subgroups"] if g.get("interpretable") and g["key"] in ("holiday", "autumn", "summer", "spring", "winter")]
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    x = np.arange(len(groups))
    wdt = 0.2
    colors = {"S1": "#b0b6bf", "S3": "#7d848e", "R1": BLUE, "R3b": GREEN}
    for j, k in enumerate(keys):
        vals = [g["pr_auc"][k] for g in groups]
        ax.bar(x + (j - 1.5) * wdt, vals, wdt, color=colors[k], label=name[k])
    for i, g in enumerate(groups):
        ax.plot([x[i] - 2 * wdt, x[i] + 2 * wdt], [g["pos_rate"]] * 2, color=RED, lw=2)
    ax.plot([], [], color=RED, lw=2, label="정보 없이 찍을 때(양성 비율)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{g['name']}\n(τ {g['n']:,})" for g in groups], fontsize=14)
    ax.set_ylabel("PR-AUC", fontsize=14)
    ax.legend(fontsize=13, frameon=False, ncol=5, loc="upper center", bbox_to_anchor=(0.5, 1.02))
    ax.set_title("어디서 잘하고 어디서 못하나(평가 기간)", loc="left", fontsize=19, pad=34)
    _clean(ax)
    fig.tight_layout()
    fig.savefig(OUT / "s4_where.png")
    plt.close(fig)


def s5_rd(bt: dict):
    A = pd.read_parquet(CACHE / "attribution_test_W24_P512.parquet")
    ref = bt["rd_reference"]
    fig, ax = plt.subplots(figsize=(W, H), dpi=DPI)
    ax.hist(A["rd_now"], bins=60, color=BLUE, alpha=0.8)
    ax.axvline(1.0, color=RED, lw=2.5, ls="--")
    ax.text(0.985, ax.get_ylim()[1] * 0.92, "R_d = 1\n(이보다 크면 '번짐')", color=RED, ha="right", fontsize=15)
    ax.set_xlim(0, 1.1)
    ax.set_xlabel("번짐 지수 R_d(판단 시각마다의 사후 평균)", fontsize=14)
    ax.set_ylabel("판단 시각 수", fontsize=14)
    ax.set_title(f"실자료에서 R_d는 중앙값 {ref['rd_median']:.2f}, 최대 {ref['rd_max']:.2f} — 앱의 확산 경보(P(R_d>1) ≥ 0.5)는 한 번도 울리지 않았다",
                 loc="left", fontsize=17)
    _clean(ax)
    fig.tight_layout()
    fig.savefig(OUT / "s5_rd.png")
    plt.close(fig)


def business() -> dict:
    """연간 12시간 초과 대기 척수·초과 시간(실측, 공개 라벨 집계). 상한(168시간)을 넘는 기록은 오류일 수 있어 뺀다."""
    voy = load("voyages")
    q = voy[(voy["wait_h"] > F.WAIT_X) & (voy["wait_h"] <= F.WAIT_CAP)].copy()
    q["year"] = q["arrival_t"].dt.year
    out = {"note": "부산신항 컨테이너선, PORT-MIS 관제정보 라벨. 12시간 초과·168시간 이하. 2026년은 11-04까지(일부 연도)",
           "label_end": str(voy["arrival_t"].max())[:10], "years": {}}
    for y, g in q.groupby("year"):
        out["years"][int(y)] = {"ships": int(len(g)), "wait_h_sum": round(float(g["wait_h"].sum()), 1),
                                "over12_h_sum": round(float((g["wait_h"] - 12).sum()), 1), "wait_h_median": round(float(g["wait_h"].median()), 1)}
    full = [y for y in out["years"] if y < 2026]
    out["full_year_mean"] = {k: round(float(np.mean([out["years"][y][k] for y in full])), 1) for k in ("ships", "wait_h_sum", "over12_h_sum")}
    out["full_years"] = full
    # 트럭 대기 단가(가정의 근거): 2026년 적용 화물자동차 안전운임 고시의 컨테이너 대기료 30분당 2만 원(항만 부두 안은 1시간 넘으면)
    out["truck_wait_fee"] = {"won_per_30min": 20000, "won_per_hour": 40000,
                             "source": "물류신문 2026-02-03 '3년 전과 확연히 달라진 안전운임제 무엇이 바뀌었나'(2026년 적용 안전운임 고시: 대기료 30분당 2만 원, "
                                       "항만 부두 안 대기료 발생 기준 1시간) https://www.klnews.co.kr/news/articleView.html?idxno=319886",
                             "note": "선박 대기 1척이 트럭 몇 대의 대기로 이어지는지는 자료가 없어 가정이다(아래 시나리오)."}
    ships = out["full_year_mean"]["ships"]
    out["scenarios"] = [{"trucks_per_ship": n, "hours_saved_per_truck": h,
                         "won_per_year": int(round(ships * n * h * out["truck_wait_fee"]["won_per_hour"]))}
                        for n in (5, 10, 20) for h in (1, 2)]
    (OUT / "business.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _ci(v) -> str:
    return f"{v[0]:+.3f}~{v[1]:+.3f}"


def _pattern_sentence(bt: dict) -> str:
    pp = bt.get("pretest_patterns") or {}
    g = (pp.get("grt_bands") or {}).get("5천~1만")
    if not g:
        return ""
    return (f"평가 기간 전({pp['period'][0][:7]}~{pp['period'][1][:7]}) 12시간 넘는 대기의 하루 건수는 분산/평균 {pp['daily_var_over_mean']}로 "
            f"무작위 도착과 비슷했고, 총톤수 5천~1만 톤 배가 도착의 {g['arrivals_pct']}%인데 12시간 초과 대기의 {g['gt12_pct']}%였다"
            "(`realdata/results/audit.json`). 혼잡이 번지기보다 일정(선석 예약보다 일찍 온 배)과 관련된 것으로 보이지만, 공개 자료로는 가를 수 없다.")


def slides_md(bt: dict, biz: dict) -> str:
    m = {x["key"]: x for x in bt["models"]}
    pr = {(x["a"], x["b"]): x for x in bt["pairs"]}
    p = bt["periods"]
    r1, ref = m["R1"]["alarm"], bt["rd_reference"]
    ca = next(c for c in bt["cases"] if c["id"] == "a")
    cc = next(c for c in bt["cases"] if c["id"] == "c")
    fy = biz["full_year_mean"]
    L = ["# 발표용: 실자료 검증 슬라이드(10분 발표 중 실자료 1~2장 + 사례 1장)", "",
         f"숫자 출처: `realdata/results/backtest.json`(로컬 전용, 만든 시각 {bt['generated_kst']}, 커밋 {bt['built_from_commit'] or '없음'}), "
         "`realdata/results/rdelay.json`, "
         "`presentation/business.json`. 그림은 `presentation/s*.png`(1920×1080).", "",
         "## 슬라이드 1. 실제 공개 자료로 되돌려 보았다", "",
         f"- 핵심 문장: 기후값보다는 낫고, 지금 정박지 대기만 보는 지속성과는 비슷하다.",
         f"- 그림: `s1_compare.png`",
         f"- 숫자: 평가 기간 {p['test'][0][:10]}~{p['test'][1][:10]}(판단 시각 {p['n_tau']['test']:,}개, 한 번만 계산) PR-AUC "
         f"R-Delay {m['R1']['pr_auc']:.3f} · 지속성 {m['S1']['pr_auc']:.3f} · 스케줄 {m['S3']['pr_auc']:.3f} · 기후값 {m['S0']['pr_auc']:.3f} · "
         f"정보 없음 {bt['test_pos_rate']:.3f}",
         f"- 차이 95% 구간: R-Delay−기후값 {_ci(pr[('R1', 'S0')]['pr_ci'])}(낫다), R-Delay−지속성 {_ci(pr[('R1', 'S1')]['pr_ci'])}(차이 없음)",
         f"- 말할 것: 학습 {p['train'][0][:7]}~{p['train'][1][:7]}, 검증 {p['val'][0][:7]}~{p['val'][1][:7]}에서 설정을 정해 고정하고 평가 기간은 한 번만 계산했다. "
         "공개 실자료 후향 검증이며 실시간 운영 검증은 아니다.", "",
         "## 슬라이드 2. 울리면 잘 맞지만, 대기가 시작되기 전에는 잘 울리지 않는다", "",
         "- 핵심 문장: R-Delay 경보는 이미 쌓인 대기를 확률로 정리하는 데 강하고, 시작을 미리 알리는 힘은 약하다. 스케줄 정보와 합치면 조금 낫다.",
         "- 그림: `s2_alarm.png`",
         f"- 숫자(경보 비율 10% 목표): R-Delay 정밀도 {r1['precision']:.2f}(가장 높음), 에피소드 {r1['episodes']}개 중 시작 전 72시간 안 경보 "
         f"{r1['caught_72h']}개, 경보 창이 시작을 덮은 것 {r1['caught_cover']}개, 경보 {r1['onsets']}회",
         f"- R-Delay+스케줄 − 스케줄 PR-AUC 차이 95% 구간 {_ci(pr[('R3b', 'S3')]['pr_ci'])}(조금 낫다)",
         f"- 왜: R-Delay 점수와 지속성의 순위상관 {bt['posthoc']['spearman']['R1~S1']}, 스케줄과는 {bt['posthoc']['spearman']['R1~S3']}"
         "(서로 다른 정보). " + _pattern_sentence(bt), "",
         f"## 슬라이드 3. 사례: {ca['title']}", "",
         f"- 그림: `s3_case_a.png`(예비: `s3_case_c.png` {cc['title']})",
         f"- 숫자: {ca['start'][:10]}~{ca['end'][5:10]} 초과 대기 {ca['facts']['ships_event']}척, 대기 중앙값 약 {ca['facts']['wait_h_median']:.0f}시간, "
         f"정박지 대기 최대 {ca['facts']['anchor_max']}척. 시작 전 72시간 안 첫 경보 "
         + (f"{ca['facts']['first_alarm_lead_h']:.1f}시간 전" if ca['facts']['first_alarm_lead_h'] is not None else "없음")
         + f", 시작 24시간 안 경보 {'있음' if ca['facts']['alarm_in_24h_before'] else '없음'}, 시작 뒤 첫 경보 {ca['facts']['first_alarm_after_start_h']}시간 뒤",
         f"- 원인 판정(모형상 배분): {ca['attribution']['text']}", "",
         "## 예비 슬라이드", "",
         "- `s4_where.png` 어디서 잘하고 못하나: " + "; ".join(
             f"{g['name']} R-Delay {g['pr_auc']['R1']:.2f}·지속성 {g['pr_auc']['S1']:.2f}(정보 없음 {g['pos_rate']:.2f})"
             for g in bt["subgroups"] if g.get("interpretable") and g["key"] in ("holiday", "autumn", "summer", "winter", "spring")),
         f"- `s5_rd.png` 실자료 R_d 중앙값 {ref['rd_median']:.2f}, 최대 {ref['rd_max']:.2f}, P(R_d>1) 최대 {ref['p_rd1_max']:.4f} → 앱의 확산 경보는 실자료에서 울리지 않았다", "",
         "## 사업성 숫자(실측 + 가정)", "",
         f"- 실측: 부산신항 컨테이너선 12시간 초과 대기 연 {fy['ships']:.0f}척, 대기 합계 연 {fy['wait_h_sum']:,.0f}시간, 12시간을 넘긴 시간 합계 연 "
         f"{fy['over12_h_sum']:,.0f}시간({biz['full_years'][0]}~{biz['full_years'][-1]}년 평균, `presentation/business.json`)",
         f"- 트럭 대기 단가 근거: 컨테이너 대기료 30분당 {biz['truck_wait_fee']['won_per_30min']:,}원(시간당 {biz['truck_wait_fee']['won_per_hour']:,}원), "
         f"{biz['truck_wait_fee']['source']}",
         "- 시나리오(가정: 대기 선박 1척당 트럭 n대가 h시간 덜 기다림): " + "; ".join(
             f"{x['trucks_per_ship']}대×{x['hours_saved_per_truck']}시간 → 연 {x['won_per_year'] / 1e6:,.1f}백만 원" for x in biz["scenarios"]),
         "- 주의: 트럭 대수·절감 시간은 실측이 아니라 가정이다. 선박 대기비용(용선료 기반)은 `local_only/`에만 둔다(외부 공개 금지 자료)."]
    return "\n".join(L) + "\n"


def qa_md(bt: dict, biz: dict) -> str:
    m = {x["key"]: x for x in bt["models"]}
    pr = {(x["a"], x["b"]): x for x in bt["pairs"]}
    ref, r1, p = bt["rd_reference"], m["R1"]["alarm"], bt["periods"]
    vt = next((v for v in bt["vessel"] if v["name"].startswith("T2-24")), None)
    ty = next((c for c in bt["cases"] if c["kind"] == "typhoon"), None)
    qa = [
        ("실제 자료로 검증했나?",
         f"공개 실자료(PORT-MIS 관제정보 등)로 과거를 되돌려 본 후향 검증이다. 평가 기간 {p['test'][0][:10]}~{p['test'][1][:10]}, 판단 시각 "
         f"{p['n_tau']['test']:,}개, 한 번만 계산(`realdata/results/test_runs.json`). 실시간 운영 검증은 아니다."),
        ("기준선보다 얼마나 낫나?",
         f"PR-AUC R-Delay {m['R1']['pr_auc']:.3f}, 기후값 {m['S0']['pr_auc']:.3f}, 지속성 {m['S1']['pr_auc']:.3f}. 기후값보다는 낫고"
         f"({_ci(pr[('R1', 'S0')]['pr_ci'])}), 지속성과는 차이가 없다({_ci(pr[('R1', 'S1')]['pr_ci'])})."),
        ("그럼 R-Delay가 왜 필요한가?",
         f"스케줄 기준선에 R-Delay 상태를 더하면 PR-AUC가 조금 오른다(차이 95% 구간 {_ci(pr[('R3b', 'S3')]['pr_ci'])}). "
         "R-Delay 상태는 스케줄 정보와 겹치지 않는다"
         f"(순위상관 {bt['posthoc']['spearman']['R1~S3']}). 경보가 울리면 정밀도 {r1['precision']:.2f}로 가장 높다."),
        ("경보가 대기 시작을 미리 알려 주나?",
         f"아니다. 에피소드 {r1['episodes']}개 중 {r1['episodes'] - r1['caught_72h']}개는 시작 전 72시간 안에 경보가 없었다. 이미 쌓인 대기를 따라가는 성격이 강하다."),
        ("왜 감염병 모형인가? 실자료에서도 지연이 번지나?",
         f"연쇄(번짐)를 재기 위해 감염병 모형의 번짐 지수 R_d를 썼다. 실자료에서는 R_d 중앙값 {ref['rd_median']:.2f}, 최대 {ref['rd_max']:.2f}로 1을 넘지 않았다. "
         "이 자료 해상도에서는 번짐이 보이지 않았고, 경보는 정박지 지연 예측 점수로 냈다."),
        ("항계 밖 대기는?",
         "정답 라벨은 항계 안 정박지 대기만 센다(입항 신고 전 항계 밖 대기는 빠진다). 한계로 적었다."),
        ("실시간 운영은 어떻게 하나?",
         "판단 시각마다 그때까지의 관측(관제 이벤트·도로 속도)만으로 24시간 창 필터 → 예측을 돌린다. 백테스트도 같은 방식이다. "
         "실시간 자료 연동(지연·누락 처리)은 아직 검증하지 않았다."),
        ("태풍 때는 잘 맞나?",
         (f"평가 기간에 가장 가까이 온 태풍도 {ty['typhoon']['min_km']}km 밖이었고, 500km 안에 있던 동안 부산 풍속은 최대 {ty['facts']['wind_max_event']}m/s였다. "
          "태풍 상황은 검증하지 못했다." if ty else "평가 기간에 태풍 사례가 없다.")
         + f" 기상을 완벽히 안다고 해도 PR-AUC는 {m['S2']['pr_auc']:.3f} → {m['S2_oracle_weather']['pr_auc']:.3f}로 조금만 오른다."),
        ("배 한 척 단위로도 맞히나?",
         (f"도착 24시간 전 판단(도착 시각을 안다는 낙관적 가정)에서 R-Delay 대기열 PR-AUC {vt['rdelay_queue']['pr_auc']:.3f}로, 배 크기를 쓰는 로지스틱 "
          f"{vt['baseline']['pr_auc']:.3f}보다 못하다." if vt and vt.get("rdelay_queue") else "표본 부족으로 계산하지 않았다.")),
        ("시험 기간을 보고 고친 것 아닌가?",
         "프로토콜을 먼저 고정하고(`realdata/PROTOCOL.md`) 평가 기간은 한 번만 계산했다. 계산 뒤 바꾼 것은 그림·표 모양과 설명용 분석뿐이며 변경 이력에 적었다. "
         "S3 스케줄 특징은 검증 기간 탐색에서 나왔다는 점도 밝혔다."),
        ("사업성은?",
         f"부산신항에서 12시간 넘게 기다린 컨테이너선은 연 {biz['full_year_mean']['ships']:.0f}척, 12시간을 넘긴 시간 합계 연 "
         f"{biz['full_year_mean']['over12_h_sum']:,.0f}시간이다({biz['full_years'][0]}~{biz['full_years'][-1]}년 평균, 실측). "
         f"트럭 대기료는 2026년 안전운임 고시 기준 시간당 {biz['truck_wait_fee']['won_per_hour']:,}원이다. 대기 선박 1척당 트럭 10대가 1시간 덜 기다리면 "
         f"연 {next(x['won_per_year'] for x in biz['scenarios'] if x['trucks_per_ship'] == 10 and x['hours_saved_per_truck'] == 1) / 1e6:,.1f}백만 원(가정)."),
    ]
    L = ["# 발표 예상 질문과 답(실자료 검증)", "",
         f"숫자 출처: `realdata/results/backtest.json`(로컬 전용, {bt['generated_kst']}), `presentation/business.json`.", ""]
    for i, (q, a_) in enumerate(qa, 1):
        L += [f"## {i}. {q}", "", a_, ""]
    return "\n".join(L)


def main():
    _font()
    OUT.mkdir(exist_ok=True)
    bt = _bt()
    s1_compare(bt)
    s2_alarm(bt)
    s3_case(bt, "a")
    s3_case(bt, "c")
    s4_where(bt)
    s5_rd(bt)
    b = business()
    (OUT / "slides_backtest.md").write_text(slides_md(bt, b), encoding="utf-8")
    (OUT / "qa_backtest.md").write_text(qa_md(bt, b), encoding="utf-8")
    print("→", sorted(p.name for p in OUT.glob("*.*")), "· business:", b["full_year_mean"])


if __name__ == "__main__":
    main()
