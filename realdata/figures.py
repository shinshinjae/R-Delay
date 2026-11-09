"""단계 3 그림: realdata/results/rdelay.json(+ 선행시간 분포는 realdata/cache/pred_final.parquet) → realdata/reports/fig_*.png.

공개 자료 집계값만 그린다(2배 해상도). 한글 글꼴: Mac 'AppleGothic', Windows 'Malgun Gothic'(웹 글꼴 woff2는 matplotlib에서 못 씀).
실행: python -m realdata.figures   (evaluate final 뒤)
"""
from __future__ import annotations

import json
import platform

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .paths import CACHE, REPORTS, RESULTS  # noqa: E402

NAMES = {"S0": "S0 기후값", "S1": "S1 지속성", "S2": "S2 로지스틱", "S2b": "S2b 부스팅", "S3": "S3 스케줄", "R1": "R1 R-Delay 점수",
         "R2": "R2 R-Delay 재보정", "R3": "R3 하이브리드(S2+R)", "R3b": "R3b 하이브리드(S3+R)", "S2_oracle_weather": "참고: 기상 완벽 예보(상한)"}
GRAY, BLUE, GOLD, RED, INK, MUTED = "#8a8f98", "#1d6ac8", "#b8901c", "#c0392b", "#222222", "#666666"
# 신뢰도 곡선 4개 계열: 색약에서도 구별되는 Okabe-Ito 색 + 선 모양·표식으로 한 번 더 구별
REL_STYLE = {"S0": dict(color="#7a7a7a", ls="--", marker="s"), "S1": dict(color="#E69F00", ls="-.", marker="^"),
             "R2": dict(color="#0072B2", ls="-", marker="o"), "R3b": dict(color="#009E73", ls="-", marker="D")}
MIN_BIN_N = 30


def _font():
    plt.rcParams["font.family"] = "Malgun Gothic" if platform.system() == "Windows" else "AppleGothic"
    plt.rcParams["axes.unicode_minus"] = False


def _color(k):
    return GOLD if "oracle" in k else BLUE if k.startswith("R") else GRAY


def _style(ax):
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(colors=INK, labelsize=9)


def _leads(r, ks):
    """잡은 에피소드의 선행시간 목록: final과 같은 예측·같은 경보 기준으로 다시 세고, 요약값이 rdelay.json과 같은지 확인한다."""
    from . import baselines as B
    from . import features as F
    from . import metrics as M
    from .io import load
    P = pd.read_parquet(CACHE / "pred_final.parquet")
    tau_end = pd.Timestamp(r["tau_end"])
    m = B.period_masks(P.index, tau_end)
    y = P["y"]
    train_rate = float(y[m["train_fit"]].mean())
    arr = F.qualifying_arrivals(load("voyages"))
    t0, t1 = pd.Timestamp(B.PERIODS["test"][0]), tau_end + pd.Timedelta(hours=2)
    out = {}
    for k in ks:
        thr = M.thresholds(P[k][m["val"]], y[m["val"]], train_rate)["rate_10"]
        e = M.event_metrics(P[k], thr, arr, t0, t1, return_leads=True)
        ref = r["models"][k]["test_events_rate_10"]
        assert abs(thr - r["models"][k]["thresholds"]["rate_10"]) < 1e-4 and e["lead_h_median"] == ref["lead_h_median"] \
            and e["missed"] == ref["missed"], (k, e["lead_h_median"], ref["lead_h_median"])
        out[k] = e["leads_h"]
    return out


def main():
    _font()
    r = json.loads((RESULTS / "rdelay.json").read_text(encoding="utf-8"))
    REPORTS.mkdir(parents=True, exist_ok=True)
    ci = r["bootstrap_95"]
    order = [k for k in ["S0", "S1", "S2", "S2b", "S3", "R1", "R2", "R3", "R3b", "S2_oracle_weather"] if k in r["models"]]
    base = r["test_pos_rate"]
    period = f"평가 기간 {r['test'][0][:10]}~{r['test'][1][:10]}"

    # 1) 시험 기간 PR-AUC와 95% 구간
    fig, ax = plt.subplots(figsize=(9, 5), dpi=200)
    his = []
    for i, k in enumerate(order[::-1]):
        v = r["models"][k]["test"]["pr_auc"]
        lo, hi = ci.get(f"{k}.pr_auc", [v, v])
        his.append(hi)
        ax.errorbar(v, i, xerr=[[v - lo], [hi - v]], fmt="o", color=_color(k), capsize=3, lw=1.6, ms=6,
                    mfc="white" if "oracle" in k else _color(k))
        ax.text(hi + 0.006, i, f"{v:.3f}", va="center", fontsize=9, color=INK)
    ax.axvline(base, color=RED, ls="--", lw=1, label=f"아무 정보 없이 찍을 때(양성 비율 {base:.2f})")
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([NAMES.get(k, k) for k in order[::-1]])
    ax.set_xlim(min(base, min(ci.get(f"{k}.pr_auc", [1, 1])[0] for k in order)) - 0.02, max(his) + 0.05)
    ax.set_xlabel("PR-AUC (높을수록 좋음) · 막대 = 주 단위 블록 부트스트랩 95% 구간", fontsize=9, color=MUTED)
    ax.set_title(f"앞으로 24시간 안에 '12시간 넘게 기다릴 배'가 올지 맞히기\n{period}", fontsize=11, color=INK, loc="left")
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.grid(axis="x", alpha=0.3)
    _style(ax)
    fig.tight_layout()
    fig.savefig(REPORTS / "fig_compare_test.png")
    plt.close(fig)

    # 2) 신뢰도 곡선(표본 30개 미만 구간은 뺌)
    fig, ax = plt.subplots(figsize=(5.4, 5.2), dpi=200)
    ax.plot([0, 1], [0, 1], color="#999", lw=1, ls=":")
    ax.axhline(base, color=RED, lw=0.8, ls="--", alpha=0.6)
    ax.text(0.79, base + 0.01, f"평가 기간 양성 비율 {base:.2f}", color=RED, fontsize=7.5, ha="right", va="bottom")
    for k, st in REL_STYLE.items():
        if k not in r["models"]:
            continue
        rel = [b for b in r["models"][k]["test_reliability"] if b["n"] >= MIN_BIN_N]
        ax.plot([b["mean_p"] for b in rel], [b["obs"] for b in rel], lw=1.6, ms=6, label=NAMES[k], **st)
    ax.set_xlim(0, 0.8)
    ax.set_ylim(0, 0.8)
    ax.set_xlabel("예측 확률(구간 평균)", fontsize=9)
    ax.set_ylabel("실제로 일어난 비율", fontsize=9)
    ax.set_title(f"신뢰도 곡선 — {period}", fontsize=10.5, loc="left")
    ax.text(0.01, 0.01, f"표본 {MIN_BIN_N}개 미만 구간은 뺌 · 점선 대각선 = 완벽한 보정", transform=ax.transAxes, fontsize=7.5, color=MUTED)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    ax.grid(alpha=0.3)
    _style(ax)
    fig.tight_layout()
    fig.savefig(REPORTS / "fig_reliability_test.png")
    plt.close(fig)

    # 3) 사건 단위(경보 비율 10% 목표): 시작을 잡은 비율 두 기준 · 오경보율과 경보 횟수 · 선행시간 분포
    ks = [k for k in order if "oracle" not in k and k != "R2"]  # R2는 R1의 단조 변환이라 경보가 같다
    ev = {k: r["models"][k]["test_events_rate_10"] for k in ks}
    rates = [r["models"][k]["test_at_rate_10"]["alarm_rate"] for k in ks]
    leads = _leads(r, ks)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), dpi=200, gridspec_kw={"width_ratios": [1.25, 1, 1.15]})
    x = np.arange(len(ks))
    ax = axes[0]
    c72 = [1 - ev[k]["missed_rate"] for k in ks]
    ccov = [1 - ev[k]["missed_covering_rate"] for k in ks]
    ax.bar(x - 0.2, c72, 0.38, color=[_color(k) for k in ks], alpha=0.45, label="시작 전 72시간 안에 경보")
    ax.bar(x + 0.2, ccov, 0.38, color=[_color(k) for k in ks], label="경보 창(24시간)이 시작을 덮음")
    for i in range(len(ks)):
        ax.text(x[i] - 0.2, c72[i] + 0.01, f"{c72[i]:.0%}", ha="center", fontsize=7, color=MUTED)
        ax.text(x[i] + 0.2, ccov[i] + 0.01, f"{ccov[i]:.0%}", ha="center", fontsize=7, color=INK)
    ax.set_title("에피소드 시작을 미리 잡은 비율", fontsize=10, loc="left")
    ax.legend(fontsize=7.5, frameon=False, loc="upper right")
    ax.set_ylim(0, 1)
    ax = axes[1]
    fa = [ev[k]["false_alarm_rate"] for k in ks]
    ax.bar(x, fa, 0.6, color=[_color(k) for k in ks])
    for i, k in enumerate(ks):
        ax.text(x[i], fa[i] + 0.01, f"{fa[i]:.0%}\n{ev[k]['alarm_onsets']}회", ha="center", fontsize=7, color=INK)
    ax.set_title("오경보율(경보 뒤 48시간 안에 없음)과 경보 횟수", fontsize=10, loc="left")
    ax.set_ylim(0, 0.7)
    ax = axes[2]
    data = [leads[k] for k in ks]
    bp = ax.boxplot(data, positions=x, widths=0.55, patch_artist=True, showfliers=False, medianprops=dict(color=INK))
    for patch, k in zip(bp["boxes"], ks):
        patch.set_facecolor(_color(k))
        patch.set_alpha(0.55)
    for i, d in enumerate(data):
        ax.scatter(np.full(len(d), x[i]) + np.linspace(-0.15, 0.15, max(len(d), 1))[:len(d)], d, s=5, color=INK, alpha=0.5, zorder=3)
        ax.text(x[i], 74, f"{len(d)}개", ha="center", fontsize=7, color=MUTED)
    ax.set_ylim(0, 80)
    ax.set_title("선행시간 분포(잡은 에피소드, 72시간 창)", fontsize=10, loc="left")
    ax.set_ylabel("시간", fontsize=9)
    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(ks, fontsize=8.5)
        ax.grid(axis="y", alpha=0.3)
        _style(ax)
    fig.suptitle(f"경보 비율 10% 목표(평가 기간 실제 {min(rates):.0%}~{max(rates):.0%}) · {period} · 에피소드 {ev[ks[0]]['episodes']}개 "
                 f"(R2는 R1과 경보가 같아 뺌)", fontsize=10.5, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(REPORTS / "fig_events_test.png")
    plt.close(fig)
    print("→", REPORTS / "fig_compare_test.png", REPORTS / "fig_reliability_test.png", REPORTS / "fig_events_test.png")


if __name__ == "__main__":
    main()
