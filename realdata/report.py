"""비교표 realdata/results/compare.md: rdelay.json(시험 1회)·rdelay_select.json(검증)·baselines.json에서만 숫자를 가져온다(지시서 규칙 7).

실행: python -m realdata.report
"""
from __future__ import annotations

import json

from .paths import RESULTS

from . import features as F  # noqa: E402

NAMES = {"S0": "S0 기후값(요일×시각)", "S1": "S1 지속성(지금 정박지 대기)", "S2": f"S2 로지스틱(공개 특징 {len(F.FEATURES)}개)",
         "S2b": "S2b 부스팅(같은 특징)", "S3": "S3 스케줄(배별 기항 간격·지난 대기)", "R1": "R1 R-Delay 점수", "R2": "R2 R-Delay 재보정",
         "R3": "R3 하이브리드(S2 특징 + R-Delay 상태)", "R3b": "R3b 하이브리드(S3 특징 + R-Delay 상태)",
         "S2_oracle_weather": "참고: S2 + 기상 완벽 예보(상한)"}


def _ci(ci, key):
    v = ci.get(key)
    return "" if not v else f" [{v[0]:.3f}, {v[1]:.3f}]"


MIN_POS = 10
POSTHOC_PAIRS = [("R1", "S1"), ("R1", "S3"), ("R1", "S0"), ("S1", "S3"), ("R3b", "S3"), ("R3b", "R1")]


def posthoc(r) -> dict:
    """시험 기간 예측끼리의 순위상관(설명용). realdata/cache/pred_final.parquet → realdata/results/posthoc_test.json."""
    import pandas as pd
    from scipy.stats import spearmanr

    from .paths import CACHE
    P = pd.read_parquet(CACHE / "pred_final.parquet")
    te = (P.index >= pd.Timestamp(r["test"][0])) & (P.index <= pd.Timestamp(r["test"][1]))
    assert int(te.sum()) == r["test"][2], (int(te.sum()), r["test"][2])
    out = {"note": "평가 기간 결과를 본 뒤 계산한 설명용 값. 모형 선택·설정에 쓰지 않음", "test": r["test"][:2], "n": int(te.sum()),
           "spearman": {f"{a}~{b}": round(float(spearmanr(P.loc[te, a], P.loc[te, b]).statistic), 3) for a, b in POSTHOC_PAIRS}}
    (RESULTS / "posthoc_test.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main():
    r = json.loads((RESULTS / "rdelay.json").read_text(encoding="utf-8"))
    sel = json.loads((RESULTS / "rdelay_select.json").read_text(encoding="utf-8"))
    ci = r["bootstrap_95"]
    L = ["# 실자료 후향 검증 비교표 (단계 3)", "",
         f"만든 시각 {r['generated']} · 프로토콜 {r['protocol']} · 출처: `realdata/results/rdelay.json`(평가 기간 1회 계산), `rdelay_select.json`(검증), `baselines.json`.",
         "공개 실자료 후향 검증이며 실시간 운영 검증이 아니다. 해상(신항 정박지)·육상(배후 고속도로) 두 층만 쓰고 항공은 자료가 없어 뺐다.", "",
         f"- 목표 T1: (τ, τ+24h]에 도착하는 신항 컨테이너선 중 12시간 초과(168시간 이하) 대기가 1척 이상. τ = 2시간 격자.",
         f"- 평가(시험) 기간 {r['test'][0][:16]} ~ {r['test'][1][:16]} · τ {r['test'][2]:,}개 · 양성 비율 {r['test_pos_rate']}",
         f"- 고른 설정(검증 기간): R1 점수 = `{sel['R1_score']}`, R2 = {sel['R2_method']}, R3 로지스틱 C = {sel['R3_C']}, "
         f"R3b C = {sel['R3b_C']}, S3 C = {r['chosen']['baselines']['S3']['C']}, R-Delay 창 24시간",
         f"- {r['threshold_note']}", "",
         "## 평가 기간 점수(괄호: 주 단위 블록 부트스트랩 95% 구간)", ""]
    L += ["| 모형 | PR-AUC | ROC-AUC | Brier | S0 대비 기술점수 |", "|---|---|---|---|---|"]
    for k, v in r["models"].items():
        t = v["test"]
        L.append(f"| {NAMES.get(k, k)} | {t['pr_auc']}{_ci(ci, k + '.pr_auc')} | {t['roc_auc']}{_ci(ci, k + '.roc_auc')} | "
                 f"{t['brier']}{_ci(ci, k + '.brier')} | {t.get('bss_vs_s0')}{_ci(ci, k + '.bss_vs_s0')} |")
    L += ["", "짝 차이(같은 부트스트랩 표본):", ""]
    for pair in ("R1-S1", "R1-S0", "R2-S1", "R3-S2", "R3b-S3", "S3-S0", "S3-S2", "R1-S3"):
        if f"{pair}.pr_auc" in ci:
            L.append(f"- {pair}: PR-AUC 차이 95% 구간 {ci[pair + '.pr_auc']}, ROC-AUC 차이 {ci[pair + '.roc_auc']}")
    L += ["", "## 경보 비율 10%일 때(평가 기간)", "",
          "| 모형 | 경보 비율 | 정밀도 | 재현율 | 에피소드 | 놓침(72h 창) | 놓침(덮는 경보) | 선행시간 중앙값(시간) | 경보 시작 | 오경보율 | 오경보율(직후 제외) |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in r["models"].items():
        a, e = v["test_at_rate_10"], v["test_events_rate_10"]
        L.append(f"| {NAMES.get(k, k)} | {a['alarm_rate']} | {a['precision']} | {a['recall']} | {e['episodes']} | {e['missed']} | {e['missed_covering']} | "
                 f"{e['lead_h_median']} | {e['alarm_onsets']} | {e['false_alarm_rate']} | {e['false_alarm_rate_excl_after_episode']} |")
    L += ["", "## 경보 비율 = 학습 기저율일 때(평가 기간)", "",
          "| 모형 | 경보 비율 | 정밀도 | 재현율 | 놓침/에피소드 | 선행시간 중앙값 | 오경보율 |", "|---|---|---|---|---|---|---|"]
    for k, v in r["models"].items():
        a, e = v["test_at_rate_matched"], v["test_events_rate_matched"]
        L.append(f"| {NAMES.get(k, k)} | {a['alarm_rate']} | {a['precision']} | {a['recall']} | {e['missed']}/{e['episodes']} | {e['lead_h_median']} | {e['false_alarm_rate']} |")
    L += ["", "## 매일 10:00 판단만(앱의 판단 시각, 평가 기간)", "", "| 모형 | n | PR-AUC | ROC-AUC |", "|---|---|---|---|"]
    for k, v in r["models"].items():
        t = v["test_10am"]
        L.append(f"| {NAMES.get(k, k)} | {t['n']} | {t['pr_auc']} | {t['roc_auc']} |")
    L += ["", "## 소분석(평가 기간 PR-AUC)", "", f"양성 τ가 {MIN_POS}개 미만인 묶음은 PR-AUC가 몇 개 점에 좌우되므로 해석하지 않는다(값은 기록용).", "",
          "| 묶음 | n | 양성 τ | 양성 비율 | S0 | S1 | S2 | S3 | R1 | R3 | R3b |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in r["subgroups_test"].items():
        if "note" in v:
            L.append(f"| {k} | {v['n']} | | {v['note']} | | | | | | | |")
        else:
            npos = round(v["n"] * v["pos_rate"])
            tag = " (해석 안 함)" if npos < MIN_POS else ""
            L.append(f"| {k}{tag} | {v['n']} | {npos} | {v['pos_rate']} | {v['S0']} | {v['S1']} | {v['S2']} | {v['S3']} | {v['R1']} | {v['R3']} | {v['R3b']} |")
    L += ["", "## 선박 단위(T2, 평가 기간)", "",
          "| 판단 | n(평가) | 양성 비율 | 기준선 PR-AUC / ROC | R-Delay 대기열 PR-AUC / ROC | 하이브리드 PR-AUC / ROC |", "|---|---|---|---|---|---|"]
    for k, v in r["vessel_level"].items():
        if "note" in v:
            L.append(f"| {k} | {v['n_test']} | {v['note']} | | | |")
            continue
        q = v.get("rdelay_queue", {})
        h = v.get("hybrid_lr", {})
        L.append(f"| {k} | {v['n_test']} | {v['pos_rate_test']} | {v['baseline_lr']['pr_auc']} / {v['baseline_lr']['roc_auc']} | "
                 f"{q.get('pr_auc')} / {q.get('roc_auc')} | {h.get('pr_auc')} / {h.get('roc_auc')} |")
    ph = posthoc(r)
    L += ["", "## 사후 설명용(평가 기간 결과를 본 뒤 계산, 모형 선택·설정에 쓰지 않음)", "",
          f"평가 기간 τ {ph['n']:,}개에서 두 모형 점수의 순위상관(Spearman). 출처 `realdata/results/posthoc_test.json`.", ""]
    L += [f"- {a} ~ {b}: {v}" for (a, b), v in ((tuple(k.split('~')), v) for k, v in ph["spearman"].items())]
    L += ["", "## 검증 기간에서 본 것(선택 근거)", "", f"- R1 후보(검증 PR-AUC·ROC·0이 아닌 비율): `{json.dumps(sel['r1_candidates_val'], ensure_ascii=False)}`",
          f"- R2 재보정 검증 Brier: {sel['R2_calibration_val_brier']}", f"- R3 로지스틱 강도별 검증 PR-AUC: {sel['R3_candidates_val']}"]
    (RESULTS / "compare.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("→", RESULTS / "compare.md")


if __name__ == "__main__":
    main()
