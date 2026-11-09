"""실자료 백테스트 결과 → realdata/results/backtest.json (발표·문서 숫자용 집계, 로컬 전용).

공개 저장소에는 올리지 않는다(사용자 결정 2026-11-07: GitHub에는 데이터를 올리지 않는다. .gitignore로 막는다).
README·REPORT·MODEL·method.html의 실자료 숫자는 이 파일과 손으로 맞춘다.
입력은 아래 결과 파일뿐이다(원자료·캐시 없이 다시 만들 수 있다). 결과 파일도 로컬에만 있다.
  realdata/results/rdelay.json        평가(시험) 기간 1회 계산
  realdata/results/rdelay_select.json 검증 기간 선택
  realdata/results/baselines.json     기간·기준선 설정
  realdata/results/cases.json         사례 3개(시계열·원인 판정)와 R_d 기준값
  realdata/results/posthoc_test.json  사후 설명용 순위상관
  realdata/results/audit.json         자료 기간·라벨 수
  realdata/results/rdelay_params.json 실자료 모형 보정값
  realdata/results/ai_model_real.json f_ω 실자료 재학습 기록(꺼 둠)
문장 속 숫자는 모두 위 파일 값에서 만든다(지시서 규칙 7). 표준 라이브러리만 쓴다.

실행: python scripts/build_backtest.py [--check]   (--check: 다시 만든 내용이 지금 파일과 같은지만 본다. 생성 시각·커밋은 비교하지 않음)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "realdata" / "results"
OUT = RES / "backtest.json"  # gitignore(로컬 전용)
OUT_REL = "realdata/results/backtest.json"
MAX_BYTES = 500_000
MIN_POS_SUBGROUP = 10     # 양성 τ가 이보다 적은 묶음은 해석하지 않는다(compare.md와 같음)
RELIABILITY_MIN_N = 30    # 신뢰도 곡선에서 표본이 이보다 적은 구간은 뺀다(fig_reliability_test.png와 같음)

MODELS = [  # (키, 짧은 이름, 설명, 묶음)
    ("S0", "기후값", "요일·시각별 평균(학습+검증 기간으로 다시 맞춤)", "base"),
    ("S1", "지속성", "지금 신항 정박지에서 기다리는 배 수·가장 오래 기다린 시간", "base"),
    ("S2", "로지스틱", "공개 특징 {n_features}개(정박지·기상·태풍·달력·도로)", "base"),
    ("S2b", "부스팅", "S2와 같은 특징, gradient boosting", "base"),
    ("S3", "스케줄", "배마다 지난 기항 간격·지난 대기로 '곧 올 배'를 어림 + 달력", "base"),
    ("R1", "R-Delay 점수", "정박지 지연 비율의 24시간 예측 최댓값(검증 기간에서 고름)", "rdelay"),
    ("R2", "R-Delay 확률", "R1을 확률로 재보정(Platt)", "rdelay"),
    ("R3", "R-Delay+S2", "S2 특징 + R-Delay 상태", "hybrid"),
    ("R3b", "R-Delay+S3", "S3 특징 + R-Delay 상태", "hybrid"),
    ("S2_oracle_weather", "참고: 기상 완벽 예보", "S2에 앞으로 24시간의 실제 최대 풍속·파고를 더한 참고 모형(그 정보는 실제로는 알 수 없음). S2의 상한이지 전체의 상한은 아니다", "oracle"),
]
PAIRS = [("R1", "S0"), ("R1", "S1"), ("R1", "S3"), ("R3b", "S3"), ("S3", "S0"), ("R3", "S2"), ("S3", "S2")]
SOURCES = [
    {"org": "해양수산부", "name": "PORT-MIS 관제정보(입항·투묘·접안·이안·출항 이벤트)", "id": "15006354",
     "url": "https://www.data.go.kr/data/15006354/openapi.do", "via": "공공데이터포털", "use": "정답(선박 대기) 라벨, 해상층 관측"},
    {"org": "해양수산부", "name": "PORT-MIS 선박운항정보(입출항 신고)", "id": "15006353",
     "url": "https://www.data.go.kr/data/15006353/openapi.do", "via": "공공데이터포털", "use": "자료 점검(신고 시각이 없어 예측에는 안 씀)"},
    {"org": "부산항만공사", "name": "선박 입출항 집계·외내항 집계·컨테이너 수송통계", "id": "15150637 등",
     "url": "https://www.data.go.kr/data/15150637/fileData.do", "via": "공공데이터포털", "use": "자료 점검(라벨 교차 확인)"},
    {"org": "해양수산부", "name": "수출입 물류 플랫폼(적시선박·선석 일정 등)", "id": "15151750 등",
     "url": "https://www.data.go.kr/data/15151750/fileData.do", "via": "공공데이터포털", "use": "자료 점검(표본 자료라 백테스트에는 안 씀)"},
    {"org": "기상청", "name": "종관기상관측(ASOS) 시간자료·해양기상부이", "id": "",
     "url": "https://data.kma.go.kr", "via": "기상자료개방포털(공공누리 출처표시)", "use": "기상 특징(풍속·파고), 사례 그림"},
    {"org": "기상청", "name": "태풍경로·태풍목록", "id": "15043568 · 15043569",
     "url": "https://www.data.go.kr/data/15043568/fileData.do", "via": "공공데이터포털(공공누리 출처표시)", "use": "태풍 근접 특징, 태풍 사례"},
    {"org": "한국도로공사", "name": "노선별 방향별 1시간 구간통행속도(신항 배후 고속도로)", "id": "15043737",
     "url": "http://data.ex.co.kr", "via": "고속도로 공공데이터 포털", "use": "육상층 관측(혼잡 콘존)"},
]


def load(name: str) -> dict:
    return json.loads((RES / name).read_text(encoding="utf-8"))


def _ci_verdict(ci) -> str:
    if not ci:
        return "na"
    return "a" if ci[0] > 0 else ("b" if ci[1] < 0 else "same")


# 결과 문장의 정도 표현: PR-AUC 차이 95% 구간에서 0에 가까운 끝이 이만큼 떨어져 있을 때만 '확실히', 더 가까우면 '조금'이라고 쓴다
# (2026-11-07 문구 정정: R3b−S3는 +0.003~+0.066이라 '조금'). 판정(_ci_verdict) 자체는 그대로다.
CLEAR_MIN = 0.02


def _degree(ci) -> str:
    if not ci:
        return ""
    return "확실히" if min(abs(ci[0]), abs(ci[1])) >= CLEAR_MIN else "조금"


SOURCE_FILES = ["realdata/results/rdelay.json", "realdata/results/rdelay_select.json", "realdata/results/baselines.json",
                "realdata/results/cases.json", "realdata/results/posthoc_test.json", "realdata/results/audit.json",
                "realdata/results/rdelay_params.json", "realdata/results/ai_model_real.json"]


def source_digest() -> str:
    """입력 결과 파일 내용의 해시(앞 12자리). 같은 입력이면 어디서 만들어도 같다. 결과 파일은 저장소에 없으므로(로컬 전용)
    '어떤 결과 파일로 만들었나'를 이것으로 밝힌다."""
    h = hashlib.sha256()
    for f in SOURCE_FILES:
        h.update(f.encode())
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:12]


def git_commit() -> str | None:
    """만든 때의 코드 커밋. 이 스크립트가 커밋과 다르면 '+수정'을 붙인다. 커밋이 없으면 None.
    입력 결과 파일은 커밋에 들어가지 않으므로(로컬 전용) source_digest로 밝힌다."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        head = r.stdout.strip()
        if r.returncode != 0 or not head:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "scripts/build_backtest.py"], cwd=ROOT,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return head + ("+수정" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def build() -> dict:
    r, sel, base = load("rdelay.json"), load("rdelay_select.json"), load("baselines.json")
    cases, post, audit, params = load("cases.json"), load("posthoc_test.json"), load("audit.json"), load("rdelay_params.json")
    n_features = len(base["features"]) if isinstance(base["features"], list) else len(base["features"].get("S2", []))
    wp = re.search(r"_W(\d+)_P(\d+)", r["rdelay"])
    window_h, particles = (int(wp.group(1)), int(wp.group(2))) if wp else (None, None)
    ai = load("ai_model_real.json")
    ci = r["bootstrap_95"]
    names = {k: n for k, n, _, _ in MODELS}

    models = []
    for k, short, desc, group in MODELS:
        v = r["models"][k]
        t = v["test"]
        e, a = v["test_events_rate_10"], v["test_at_rate_10"]
        models.append({
            "key": k, "name": short, "desc": desc.format(n_features=n_features), "group": group,
            "pr_auc": t["pr_auc"], "pr_ci": ci.get(f"{k}.pr_auc"), "roc_auc": t["roc_auc"], "roc_ci": ci.get(f"{k}.roc_auc"),
            "brier": t["brier"], "bss": t.get("bss_vs_s0"), "bss_ci": ci.get(f"{k}.bss_vs_s0"),
            "pr_auc_10am": v["test_10am"]["pr_auc"], "roc_auc_10am": v["test_10am"]["roc_auc"],
            "alarm": {"rate": a["alarm_rate"], "precision": a["precision"], "recall": a["recall"], "threshold": a["threshold"],
                      "episodes": e["episodes"], "caught_72h": e["episodes"] - e["missed"], "caught_cover": e["episodes"] - e["missed_covering"],
                      "onsets": e["alarm_onsets"], "false_alarm_rate": e["false_alarm_rate"],
                      "false_alarm_rate_excl_after": e["false_alarm_rate_excl_after_episode"],
                      "lead_h_median": e["lead_h_median"], "lead_h_q25": e["lead_h_q25"], "lead_h_q75": e["lead_h_q75"]},
            "reliability": [b for b in v["test_reliability"] if b["n"] >= RELIABILITY_MIN_N],
        })
    pairs = []
    for a_, b_ in PAIRS:
        pc, rc = ci.get(f"{a_}-{b_}.pr_auc"), ci.get(f"{a_}-{b_}.roc_auc")
        pairs.append({"a": a_, "b": b_, "a_name": names[a_], "b_name": names[b_], "pr_ci": pc, "roc_ci": rc,
                      "pr_verdict": _ci_verdict(pc), "roc_verdict": _ci_verdict(rc)})
    subgroups = []
    labels = {"weekend": "주말", "holiday": "공휴일", "typhoon_500km": "태풍 500km 안", "winter": "겨울(12~2월)", "spring": "봄(3~5월)",
              "summer": "여름(6~8월)", "autumn": "가을(9~11월)"}
    for k, v in r["subgroups_test"].items():
        if "note" in v:
            subgroups.append({"key": k, "name": labels.get(k, k), "n": v["n"], "interpretable": False, "note": v["note"]})
            continue
        npos = round(v["n"] * v["pos_rate"])
        subgroups.append({"key": k, "name": labels.get(k, k), "n": v["n"], "n_pos": npos, "pos_rate": v["pos_rate"],
                          "interpretable": npos >= MIN_POS_SUBGROUP,
                          "pr_auc": {m: v[m] for m in ("S0", "S1", "S2", "S3", "R1", "R3", "R3b") if m in v}})
    vessel = []
    for k, v in r["vessel_level"].items():
        row = {"name": k, "n_test": v["n_test"]}
        if "note" in v:
            row["note"] = v["note"]
        else:
            row.update({"pos_rate": v["pos_rate_test"], "baseline": v["baseline_lr"], "rdelay_queue": v.get("rdelay_queue"),
                        "hybrid": v.get("hybrid_lr")})
        vessel.append(row)

    test0, test1, n_test = r["test"]
    m = {x["key"]: x for x in models}
    p = {(x["a"], x["b"]): x for x in pairs}
    ds = audit["datasets"]
    period = {"train": base["periods"]["train"][:2], "val": base["periods"]["val"][:2], "test": [test0, test1],
              "n_tau": {"train": base["periods"]["train"][2], "val": base["periods"]["val"][2], "test": n_test}}
    ref = cases["rd_reference"]
    rc = m["R1"]["alarm"]
    ci_txt = lambda key: f"{p[key]['pr_ci'][0]:+.3f}~{p[key]['pr_ci'][1]:+.3f}"  # noqa: E731
    v_s0, v_s1, v_b = p[("R1", "S0")]["pr_verdict"], p[("R1", "S1")]["pr_verdict"], p[("R3b", "S3")]["pr_verdict"]
    d_s0, d_s1, d_b = (_degree(p[k]["pr_ci"]) for k in (("R1", "S0"), ("R1", "S1"), ("R3b", "S3")))
    s0 = {"a": f"기후값보다 {d_s0} 낫", "same": "기후값과 차이가 없", "b": f"기후값보다 {d_s0} 못하"}[v_s0]
    s1 = {"a": f"지금 정박지 대기만 보는 지속성보다도 {d_s1} 낫다", "same": "지금 정박지 대기만 보는 지속성과는 차이가 없다",
          "b": f"지금 정박지 대기만 보는 지속성보다 {d_s1} 못하다"}[v_s1]
    conj = "지만" if (v_s0 == "a") != (v_s1 == "a") else "고"
    lines = [
        f"평가 기간({test0[:10]}~{test1[:10]}, 판단 시각 {n_test:,}개)에서 앞으로 24시간 안에 12시간 넘게 기다릴 배가 올지 맞힌 정도(PR-AUC)는 "
        f"R-Delay {m['R1']['pr_auc']:.3f}, 지속성 {m['S1']['pr_auc']:.3f}, 기후값 {m['S0']['pr_auc']:.3f}였다(아무 정보 없이 찍으면 {r['test_pos_rate']:.3f}).",
        f"R-Delay는 {s0}{conj}(차이 95% 구간 {ci_txt(('R1', 'S0'))}), {s1}({ci_txt(('R1', 'S1'))}).",
    ]
    best_prec = max(x["alarm"]["precision"] for x in models if x["group"] != "oracle")
    lines.append(f"경보가 울리면 맞을 확률(경보 비율 10%에서 정밀도)은 {rc['precision']:.2f}"
                 + ("로 비교한 모형 중 가장 높지만, " if rc["precision"] >= best_prec else "이고, ")
                 + f"대기 에피소드 {rc['episodes']}개 중 {rc['episodes'] - rc['caught_72h']}개는 시작 전 72시간 안에 경보가 없었다.")
    lines.append(f"스케줄 기준선에 R-Delay 상태를 더하면 "
                 + {"a": f"{d_b} 나아진다", "same": "차이가 없다", "b": f"오히려 {d_b} 나빠진다"}[v_b] + f"(PR-AUC 차이 95% 구간 {ci_txt(('R3b', 'S3'))}).")
    typh = next((c for c in cases["cases"] if c["kind"] == "typhoon"), None)
    n168 = audit["labels"].get("n_gt_168")
    limits = [
        "공개 실자료 후향 검증(과거 자료로 되돌려 보기)이며 실시간 운영 검증이 아니다.",
        "해상(신항 정박지)과 육상(배후 고속도로) 두 층만 썼다. 항공 자료는 없어 뺐다.",
        f"정답은 항계 안 정박지 대기만 센다(항계 밖 대기는 빠짐). 168시간을 넘는 대기({n168}건)는 정비·계류 같은 비운영 체류로 보고 "
        "양성에서만 뺐다(항차는 남김).",
        "판단 시각에 앞으로 올 배의 도착 예정 정보를 알 수 없어 쓰지 않았다(선박운항정보에 신고 시각이 없음).",
    ]
    if ref["p_rd1_max"] < 0.5:
        limits.append(f"앱의 확산 경보 P(R_d>1) ≥ 0.5는 실자료에서 한 번도 울리지 않았다(평가 기간 R_d 중앙값 {ref['rd_median']:.2f}, 최대 {ref['rd_max']:.2f}, "
                      f"P(R_d>1) 최대 {ref['p_rd1_max']:.4f}). 그래서 경보는 검증 기간에서 고른 '정박지 지연 비율 예측' 점수로 냈다.")
    limits.append("선석·장비 처리능력 공지 자료가 없어 원인 판정의 처리능력 요인은 계산하지 않았다."
                  + (f" AI 보정(f_ω)은 실자료로 다시 학습한 검증 R²가 {ai['r2_val']:.3f}로 0 이하라 껐다." if ai["r2_val"] <= 0 else ""))
    if r["test_pos_rate"] < base["train_pos_rate"]:
        limits.append(f"평가 기간의 양성 비율({r['test_pos_rate']:.2f})이 학습 기간({base['train_pos_rate']:.2f})보다 낮아 확률이 조금 높게 나온다.")
    limits.append("S3 스케줄 특징은 검증 기간을 보며 찾은 것이다(평가 기간 계산 전에 등록). 평가 기간 결과가 첫 공정한 판단이다.")
    if typh and typh["typhoon"]["min_km"] >= 300:
        f_ = typh["facts"]
        limits.append(f"평가 기간에 신항에 가장 가까이 온 태풍도 {typh['typhoon']['min_km']}km 밖이었고, 500km 안에 있던 동안 부산 풍속은 최대 "
                      f"{f_['wind_max_event']}m/s, 거제도 유의파고는 최대 {f_['wave_max_event']}m였다. 태풍 상황은 사실상 검증하지 못했다.")
    limits.append(f"도로 자료는 {ds['road']['end'][:10]}까지라 그 뒤 판단 시각에는 육상층 관측이 없다.")

    return {
        "schema": 1,
        "generated_kst": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
        "built_from_commit": git_commit(),
        "source_digest": source_digest(),
        "protocol": r["protocol"],
        "source_files": SOURCE_FILES,
        "question": "R-Delay 경보가 실제 '선박 대기 12시간 초과'를 미리, 단순 기준선보다 잘 알려 주는가?",
        "answer": lines,
        "target": "판단 시각(2시간 간격)마다 앞으로 24시간 안에 신항에 도착할 컨테이너선 중 정박지에서 12시간 넘게(168시간 이하) 기다릴 배가 1척 이상 있는지",
        "periods": period,
        "test_pos_rate": r["test_pos_rate"], "train_pos_rate": base["train_pos_rate"],
        "labels": {"voyages": audit["labels"]["n"], "gt12": audit["labels"]["gt12"], "n_gt_168": n168,
                   "gt12_capped": next(x["ships"] for x in audit["sensitivity"]["rows"] if x["thr"] == 12 and x["cap"] == 168),
                   "label_period": [ds["voyages"]["start"][:10], ds["voyages"]["end"][:10]]},
        "pretest_patterns": audit.get("pretest_patterns"),
        "anchorage_choice_train": {k: audit["asof_waiting"][k]["train"] for k in ("newport_anchorages", "all_anchorages")
                                   if "train" in audit["asof_waiting"].get(k, {})},
        "data_ranges": {k: [ds[k]["start"][:10], ds[k]["end"][:10]] for k in ("cntl", "asos", "buoy", "road", "typhoon_track") if k in ds},
        "setup": {"window_h": window_h, "particles": particles, "n_features_s2": n_features, "r1_score": sel["R1_score"], "r2_method": sel["R2_method"],
                  "nodes": ["해상 S: 신항 정박지(2시간 넘게 기다린 배 비율)", "육상 L: 배후 고속도로 혼잡 콘존 비율"],
                  "servers": params["queue"]["servers"], "ai_r2_val": ai["r2_val"], "ai": "끔(실자료로 다시 학습한 f_ω의 검증 R²가 0 이하)"},
        "alarm_rule": cases["alarm"],
        "models": models, "pairs": pairs, "subgroups": subgroups, "vessel": vessel,
        "posthoc": post, "rd_reference": ref, "cases": cases["cases"], "case_note": cases["note"], "case_bands": cases.get("wait_bands"),
        "limits": limits, "sources": SOURCES,
        "constants": {"min_pos_subgroup": MIN_POS_SUBGROUP, "reliability_min_n": RELIABILITY_MIN_N},
    }


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("generated_kst", "built_from_commit")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    missing = [f for f in SOURCE_FILES if not (ROOT / f).exists()]
    if missing:
        sys.exit(f"[중단] 입력 결과 파일이 없습니다: {', '.join(missing)}. 실자료 결과는 공개 저장소에 없고 로컬에서만 만듭니다.")
    out = build()
    txt = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(txt.encode("utf-8")) > MAX_BYTES:
        sys.exit(f"[중단] backtest.json이 {len(txt.encode()) / 1024:.0f}KB로 너무 큽니다(한도 {MAX_BYTES / 1000:.0f}KB).")
    if a.check:
        if not OUT.exists():
            sys.exit(f"[없음] {OUT_REL}이 없습니다. python scripts/build_backtest.py로 먼저 만드세요.")
        cur = json.loads(OUT.read_text(encoding="utf-8"))
        if _strip(cur) != _strip(out):
            sys.exit(f"[다름] {OUT_REL}이 realdata/results의 결과 파일과 맞지 않습니다. python scripts/build_backtest.py로 다시 만드세요.")
        print(f"{OUT_REL} 일치")
        return
    OUT.write_text(txt + "\n", encoding="utf-8")
    print(f"→ {OUT_REL} ({len(txt.encode()) / 1024:.0f}KB, 로컬 전용: 공개 저장소에 올리지 않음)")


if __name__ == "__main__":
    main()
