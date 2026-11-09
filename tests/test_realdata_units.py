"""실자료 백테스트 코드의 단위 테스트. 작은 가짜 자료만 쓴다(실자료·realdata/cache 없이 어디서나 돈다).

- metrics: 점수·경보 기준·에피소드 묶기(48시간)·사건 지표(선행시간·놓침·오경보)를 손으로 센 값과 비교
- features: 태풍 경로점은 분석 시각 + 통보 지연 뒤에만 쓴다, 달력 특징(양력 고정 공휴일만)
- io: 시각 문자열 읽기, 인코딩 판별
- cases: 원인 판정 요약(앱 규칙: 보류 하한·주요인 기준은 앱 소스에서 읽어 비교), 결과에 적는 대기 구간(wait_band) 경계
pandas·scikit-learn이 없으면(웹앱용 CI) 건너뛴다.
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
from realdata import cases as C  # noqa: E402
from realdata import features as F  # noqa: E402
from realdata import io as IO  # noqa: E402
from realdata import metrics as M  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
T = pd.Timestamp


@pytest.fixture(autouse=True)
def _keep_off_real_data(tmp_path, monkeypatch):
    """혹시 io.load가 불려도 진짜 realdata/cache·원자료 폴더를 건드리지 않게 임시 폴더로 돌린다."""
    monkeypatch.setattr(IO, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(IO, "MANIFEST", tmp_path / "cache" / "_manifest.json")
    monkeypatch.setattr(IO, "public_dir", lambda: tmp_path / "public")


# ---------- metrics ----------
def test_scores_tiny_example():
    y = [0, 0, 1, 1]
    p = [0.2, 0.6, 0.4, 0.8]
    s = M.scores(p, y, p_ref=[0.5] * 4)
    assert s["n"] == 4 and s["pos_rate"] == 0.5
    # 점수 높은 순 0.8(1) 0.6(0) 0.4(1) 0.2(0): AP = 0.5×1 + 0.5×(2/3) = 0.8333
    assert s["pr_auc"] == pytest.approx(0.8333)
    # (양성, 음성) 4쌍 중 0.4 < 0.6 한 쌍만 순서가 틀림 → 0.75
    assert s["roc_auc"] == pytest.approx(0.75)
    # Brier = (0.04 + 0.36 + 0.36 + 0.04)/4 = 0.2, 기준 0.5의 Brier = 0.25 → 기술점수 1 − 0.2/0.25 = 0.2
    assert s["brier"] == pytest.approx(0.2)
    assert s["bss_vs_s0"] == pytest.approx(0.2)


def test_scores_edge_cases():
    s = M.scores([0.1, 0.3], [0, 0], p_ref=[0.0, 0.0])
    assert s["pr_auc"] is None and s["roc_auc"] is None  # 양성이 없으면 AUC를 내지 않는다
    assert s["brier"] == pytest.approx(0.05)  # (0.01 + 0.09)/2
    assert s["bss_vs_s0"] is None  # 기준 Brier가 0이면 기술점수 없음
    s = M.scores([0.2, 0.9], [1, 1])
    assert s["pr_auc"] == pytest.approx(1.0) and s["roc_auc"] is None  # 음성이 없으면 ROC-AUC 없음
    assert "bss_vs_s0" not in s


def test_thresholds_f1_rate_matched_rate_10():
    p = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]  # 높은 순으로 줘도 결과는 같아야 한다
    y = [1, 1, 1, 0, 1, 0, 0, 0, 0, 0]
    thr = M.thresholds(p, y, train_rate=0.3)
    # 기준 0.6: 경보 5개 중 정답 4(정밀도 0.8, 재현율 1) → F1 0.889로 최대(0.8 → 0.857, 0.5 → 0.8, 0.7 → 0.75)
    assert thr["f1_max"] == pytest.approx(0.6)
    # 경보 비율 = 학습 기저율 0.3 → 70분위: 정렬 위치 9×0.7 = 6.3 → 0.7 + 0.3×0.1 = 0.73
    assert thr["rate_matched"] == pytest.approx(0.73)
    # 경보 비율 10% → 90분위: 위치 9×0.9 = 8.1 → 0.9 + 0.1×0.1 = 0.91
    assert thr["rate_10"] == pytest.approx(0.91)
    # 그 기준에서: 경보 5/10, 정밀도 4/5, 재현율 4/4
    assert M.at_threshold(p, y, 0.6) == {"threshold": 0.6, "alarm_rate": 0.5, "precision": 0.8, "recall": 1.0}


def test_episodes_split_after_48h_gap():
    assert (M.LEAD_WINDOW_H, M.FA_WINDOW_H, M.EPISODE_GAP_H) == (72, 48, 48)  # PROTOCOL v1 7절
    arr = pd.Series(pd.to_datetime(["2025-07-10 00:00", "2025-07-01 00:00", "2025-07-04 00:00",
                                    "2025-07-06 01:00", "2025-07-02 00:00"]))  # 순서를 섞어 준다
    ep = M.episodes(arr)
    # 07-01 → 07-02(24h) → 07-04(48h: 같은 에피소드) | 07-06 01:00(49h 뒤: 새 에피소드) | 07-10
    assert list(ep.columns) == ["start", "end", "ships"]
    assert ep["start"].tolist() == [T("2025-07-01 00:00"), T("2025-07-06 01:00"), T("2025-07-10 00:00")]
    assert ep["end"].tolist() == [T("2025-07-04 00:00"), T("2025-07-06 01:00"), T("2025-07-10 00:00")]
    assert ep["ships"].tolist() == [3, 1, 1]
    # 간격 기준을 24시간으로 줄이면 07-02 → 07-04(48h)도 끊긴다
    assert M.episodes(arr, gap_h=24)["ships"].tolist() == [2, 1, 1, 1]
    assert M.episodes(pd.Series([], dtype="datetime64[ns]")).empty


def _event_case():
    """기간 [07-01, 07-25), 2시간 격자 점수(기간 밖 포함), 경보 기준 0.5, 12시간 초과 대기 선박 도착."""
    t0, t1 = T("2025-07-01 00:00"), T("2025-07-25 00:00")  # 오경보는 t1 − 48h = 07-23 00:00 전에 시작한 경보만 센다
    score = pd.Series(0.1, index=pd.date_range("2025-06-28 00:00", "2025-07-27 00:00", freq="2h"))
    for a, b, v in [
        ("2025-06-29 00:00", "2025-06-29 02:00", 0.9),  # K0: 기간 전에 시작(세지 않음)
        ("2025-07-01 00:00", "2025-07-01 00:00", 0.9),  # K1: A 시작 정확히 72시간 전(창에 듦)
        ("2025-07-09 00:00", "2025-07-09 00:00", 0.9),  # K2: B 시작 시각 그 자체(B를 미리 알린 것이 아님)
        ("2025-07-12 00:00", "2025-07-13 12:00", 0.9),  # K3: C 도착 정확히 48시간 전에 시작(뒤 48시간 창 끝에 도착 → 오경보 아님)
        ("2025-07-17 06:00", "2025-07-17 10:00", 0.5),  # K4: D 42시간 전(덮는 창 24시간 밖), 점수 = 기준(≥이면 경보)
        ("2025-07-21 00:00", "2025-07-21 02:00", 0.9),  # K5: D 도착 정확히 48시간 뒤 시작(직전 48시간 창 밖), 뒤에도 도착 없음 → 오경보
        ("2025-07-23 00:00", "2025-07-23 04:00", 0.9),  # K6: t1 − 48h에 시작(48시간 창이 기간 끝을 넘음 → 세지 않음)
    ]:
        score.loc[pd.date_range(a, b, freq="2h")] = v
    arrivals = pd.Series(pd.to_datetime([
        "2025-06-29 10:00",                      # 기간 전 에피소드
        "2025-07-04 00:00", "2025-07-05 12:00",  # A: 36시간 간격이라 한 에피소드(시작 07-04 00:00)
        "2025-07-09 00:00",                      # B
        "2025-07-14 00:00",                      # C
        "2025-07-19 00:00",                      # D
        "2025-07-26 00:00",                      # 기간 끝 뒤(세지 않음)
    ]))
    return score, arrivals, t0, t1


def test_event_metrics_by_hand():
    score, arrivals, t0, t1 = _event_case()
    r = M.event_metrics(score, 0.5, arrivals, t0, t1)
    # 선행시간 창 [시작 − 72h, 시작): A 72h(K1), B 없음(K2는 시작 시각이라 창 밖) → 놓침, C 48h(K3), D 42h(K4)
    # 덮는 경보 [시작 − 24h, 시작): A·B·D에 없음 → 3개
    # 경보 시작(07-01 ≤ τ < 07-23): K1·K2·K3·K4·K5 = 5개. 뒤 48시간 (τ, τ+48h]에 도착 없음 = K1·K2·K5 → 오경보 3
    # 직전 48시간 (τ−48h, τ]에 도착: K1(06-29 10:00)·K2(07-09 00:00) → 둘을 빼면 K3·K4·K5 중 오경보 K5 하나 = 1/3
    expected = {"episodes": 4, "missed": 1, "missed_rate": 0.25, "missed_covering": 3, "missed_covering_rate": 0.75,
                "lead_h_median": 48.0, "lead_h_q25": 45.0, "lead_h_q75": 60.0,  # [42, 48, 72]의 분위(선형 보간)
                "alarm_onsets": 5, "false_alarms": 3, "false_alarm_rate": 0.6,
                "false_alarm_rate_excl_after_episode": 0.333}
    assert r == expected
    r2 = M.event_metrics(score, 0.5, arrivals, t0, t1, return_leads=True)
    assert r2.pop("leads_h") == [72.0, 48.0, 42.0]  # 에피소드 순서(A, C, D)
    assert r2 == r


def test_event_metrics_without_alarms():
    score, arrivals, t0, t1 = _event_case()
    r = M.event_metrics(score, 2.0, arrivals, t0, t1)  # 기준이 점수보다 높아 경보가 없다
    assert (r["episodes"], r["missed"], r["missed_covering"], r["alarm_onsets"], r["false_alarms"]) == (4, 4, 4, 0, 0)
    assert r["lead_h_median"] is None and r["lead_h_q25"] is None and r["lead_h_q75"] is None
    assert r["false_alarm_rate"] == 0.0 and r["missed_rate"] == 1.0


# ---------- features ----------
def test_typhoon_point_usable_only_after_release_lag():
    lag = pd.Timedelta(hours=F.TY_RELEASE_LAG_H)
    assert lag > pd.Timedelta(0)
    lat0, lon0 = F.NEWPORT_LATLON
    t1, t2 = T("2025-08-01 00:00"), T("2025-08-01 06:00")  # 분석 시각(KST)
    track = pd.DataFrame({"year": [2025, 2025], "num": [9, 9], "t": [t1, t2],
                          "lat": [lat0 - 5.0, lat0 - 2.0], "lon": [lon0, lon0]})  # 신항 정남쪽 위도 5도·2도
    # 같은 경도면 거리 = 지구 반지름 6371km × 위도 차(라디안): 약 556.0km, 222.4km
    km5, km2 = 6371.0 * math.radians(5.0), 6371.0 * math.radians(2.0)
    m, h12 = pd.Timedelta(minutes=1), pd.Timedelta(hours=12)
    grid = pd.DatetimeIndex([t1, t1 + lag - m, t1 + lag, t2 + lag - m, t2 + lag, t2 + lag + h12, t2 + lag + h12 + m])
    out = F.typhoon_features(track, grid, active_h=12.0, far_km=3000.0)
    # 분석 시각에는 아직 못 쓴다 → 통보 지연 뒤부터 그 점 → 마지막 통보 12시간 뒤까지만 '활동 중'
    assert out["ty_dist_km"].tolist() == pytest.approx([3000.0, 3000.0, km5, km5, km2, km2, 3000.0], rel=1e-9)
    assert out["ty_within_500"].tolist() == [0, 0, 0, 0, 1, 1, 0]


def test_calendar_features_fixed_holidays():
    pytest.importorskip("holidays")
    grid = pd.DatetimeIndex(["2025-08-11 00:00", "2025-08-12 06:00", "2025-08-14 22:00", "2025-08-15 10:00",
                             "2025-08-16 12:00", "2025-12-21 00:00", "2025-12-22 00:00", "2025-12-25 08:00",
                             "2026-01-01 00:00"])
    c = F.calendar_features(grid)
    # 양력 고정 공휴일만 쓴다: 광복절 2025-08-15(금), 성탄절 2025-12-25(목), 신정 2026-01-01(목)
    assert c["holiday"].tolist() == [0, 0, 0, 1, 0, 0, 0, 1, 1]
    # 다음 1~3일 안 공휴일: 08-12(+3일)·08-14(+1일) → 광복절, 12-22(+3일) → 성탄절. 08-11은 +4일이라 아님
    assert c["holiday_next3d"].tolist() == [0, 1, 1, 0, 0, 0, 1, 0, 0]
    assert c["dow"].tolist() == [0, 1, 3, 4, 5, 6, 0, 3, 3]  # 월=0
    assert c["weekend"].tolist() == [0, 0, 0, 0, 1, 1, 0, 0, 0]
    assert c["slot"].tolist() == [0, 3, 11, 5, 6, 0, 0, 4, 0]  # 2시간 칸 = 시 // 2


# ---------- io ----------
def test_parse_kst_formats():
    s = pd.Series(["2023-01-01T20:00:00+09:00", "2024-12-31 00:00:00", "2023-01-01 00:00", "2016.07.03 09:00",
                   " 2025-07-01 06:00 ", "", None])
    out = IO.parse_kst(s)
    assert out.dt.tz is None  # 시간대 없는 KST
    assert out.iloc[:5].tolist() == [T("2023-01-01 20:00"), T("2024-12-31 00:00"), T("2023-01-01 00:00"),
                                     T("2016-07-03 09:00"), T("2025-07-01 06:00")]
    assert out.iloc[5:].isna().all()


def test_sniff_encoding_bytes():
    text = "지점,일시,풍속(m/s)\n159,2025-07-01 00:00,3.2\n"
    assert IO.sniff_encoding(text.encode("utf-8")) == "utf-8-sig"
    assert IO.sniff_encoding(("﻿" + text).encode("utf-8")) == "utf-8-sig"  # BOM
    assert IO.sniff_encoding(text.encode("cp949")) == "cp949"
    assert IO.sniff_encoding(b"stn,t\n159,2025\n") == "utf-8-sig"  # ASCII
    # 앞부분만 읽어 마지막 글자가 잘려도(UTF-8 3바이트 중 2바이트만) 마지막 줄바꿈까지만 보므로 UTF-8
    assert IO.sniff_encoding("지점,일시\n부산".encode("utf-8")[:-1]) == "utf-8-sig"
    assert IO.sniff_encoding(b"\xff\xfe\xff\n") == "cp949"  # 둘 다 아니면 cp949


def test_read_csv_auto_cp949(tmp_path):
    p = tmp_path / "asos.csv"
    p.write_bytes("지점,일시,풍속(m/s)\n159,2025-07-01 00:00,3.2\n".encode("cp949"))
    df, enc = IO.read_csv_auto(p, dtype=str)
    assert enc == "cp949"
    assert list(df.columns) == ["지점", "일시", "풍속(m/s)"] and df.iloc[0].tolist() == ["159", "2025-07-01 00:00", "3.2"]


# ---------- cases: 원인 판정 요약 ----------
GRID = pd.date_range("2025-07-31 00:00", "2025-08-04 00:00", freq="2h")
CASE = {"start": T("2025-08-02 00:00"), "end": T("2025-08-03 00:00")}  # 판정 구간 [시작 − 24h, 끝] = 08-01 00:00 ~ 08-03 00:00
REF = {"volume_median": 0.0003, "beta_shift_median": -0.0123}
X_Q90 = 0.5


def _attr(vol: float, zsh: float, p_peak: float, congested: bool) -> pd.DataFrame:
    """반사실 R_d 열: 구간 안에서 물동량 몫 = vol, 전파율 평균 이동 몫 = zsh, 불확실성 몫 = 0.02가 되게 만든다.
    구간 밖은 모든 값을 크게 둬서 구간 밖이 섞이면 결과가 달라지게 한다."""
    inside = (GRID >= T("2025-08-01 00:00")) & (GRID <= T("2025-08-03 00:00"))
    peak = GRID == T("2025-08-02 12:00")
    rd_now = np.where(inside, 0.2, 5.0)
    rd_now[peak] = 0.4  # 구간 안 25개: 0.2 × 24 + 0.4 → 평균 0.208, 최대 0.4
    rd_zmean = rd_now - 0.02
    return pd.DataFrame({
        "rd_now": rd_now,
        "rd_noxi": rd_now - np.where(inside, vol, 1.0),
        "rd_zmean": rd_zmean,
        "rd_noz": rd_zmean - np.where(inside, zsh, 1.0),
        "p_rd1": np.where(peak, p_peak, np.where(inside, 0.01, 0.99)),
        "x_S": np.where(peak & congested, X_Q90 + 0.1, np.where(inside, X_Q90 - 0.4, 10.0)),
    }, index=GRID)


def _app_factor_min() -> float:
    """앱 api.py가 주요인을 적는 기준: 'drop[main_factor] > 숫자'의 숫자(함수 안 상수라 소스에서 읽는다)."""
    tree = ast.parse((ROOT / "docs" / "py" / "rdelay" / "api.py").read_text(encoding="utf-8"))
    found = [n.comparators[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Gt)
             and ast.unparse(n.left) == "drop[main_factor]" and isinstance(n.comparators[0], ast.Constant)]
    assert len(found) == 1, f"api.py에서 'drop[main_factor] > 숫자'를 하나만 찾아야 하는데 {found}"
    return found[0]


def test_attribution_constants_match_app(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "docs" / "py"))
    from rdelay import config
    assert C.HOLD_LOW == config.DEFAULTS["hold_band"][0]  # 앱의 원인 판정 보류 구간 하한
    assert C.FACTOR_MIN == _app_factor_min()  # 앱 api.py: R_d를 이 값보다 크게 낮추는 요인만 주요인(같은 '>' 비교)


def test_wait_band_boundaries():
    """공개 사례의 대기 구간: 12 < w ≤ 24, 24 < w ≤ 48, 48 < w(라벨 상한 168까지)."""
    expect = [(12.01, "12~24시간"), (24.0, "12~24시간"), (24.01, "24~48시간"), (48.0, "24~48시간"),
              (48.01, "48~168시간"), (168.0, "48~168시간")]
    assert [C.wait_band(w) for w, _ in expect] == [b for _, b in expect]
    # 구간 이름의 숫자가 경계와 같고, 이어 붙이면 라벨 범위(12시간 초과 ~ 168시간 이하)가 된다
    edges = [tuple(float(x) for x in re.fullmatch(r"(\d+)~(\d+)시간", b).groups()) for b in C.WAIT_BANDS]
    assert edges[0][0] == F.WAIT_X and edges[-1][1] == F.WAIT_CAP
    assert all(a[1] == b[0] for a, b in zip(edges, edges[1:])), edges
    for (lo, hi), b in zip(edges, C.WAIT_BANDS):
        assert C.wait_band(lo + 1e-6) == b and C.wait_band(hi) == b, (lo, hi, b)
    # 실자료 집계(realdata/results/backtest.json, 로컬 전용이라 공개 저장소·CI에는 없음)가 있으면 그 구간 이름도 같다
    bt = ROOT / "realdata" / "results" / "backtest.json"
    if bt.exists():
        assert json.loads(bt.read_text(encoding="utf-8"))["case_bands"] == C.WAIT_BANDS


@pytest.mark.parametrize("vol, zsh, p_peak, congested, verdict, factor, words, tail", [
    # 적체가 없으면 0.02를 넘는 요인이 있어도 주요인을 적지 않는다. P(R_d>1) 0.34 < 0.35 → 비확산
    (0.05, -0.03, 0.34, False, "비확산", None, ["물동량은 R_d를 0.050 올렸", "전파율(평균 이동)은 R_d를 0.030 낮췄"],
     "적체 기준(정박지 지연 비율 학습 90분위)을 넘지 않아 주요인을 적지 않는다."),
    # 적체 + 물동량 몫 0.05 > 0.02 → 주요인 물동량. P(R_d>1) 0.35는 보류 하한과 같아 비확산이 아님
    (0.05, -0.03, 0.35, True, "판정 필요", "물동량", ["물동량은 R_d를 0.050 올렸", "전파율(평균 이동)은 R_d를 0.030 낮췄"],
     "주요인 = 물동량."),
    # 적체지만 가장 큰 몫 0.015 ≤ 0.02 → 주요인 없음. |0.0002| < 0.0005 → '거의 바꾸지 않았'
    (0.0002, 0.015, 0.01, True, "비확산", None, ["물동량은 R_d를 거의 바꾸지 않았", "전파율(평균 이동)은 R_d를 0.015 올렸"],
     "적체가 있었지만 0.02 넘게 바꾼 요인이 없어 주요인은 없다."),
    # 적체 + 전파율 몫 0.03이 가장 큼 → 주요인 전파율 상승. 음수 몫은 '낮췄'
    (-0.01, 0.03, 0.01, True, "비확산", "전파율 상승", ["물동량은 R_d를 0.010 낮췄", "전파율(평균 이동)은 R_d를 0.030 올렸"],
     "주요인 = 전파율 상승."),
])
def test_attribution_summary(vol, zsh, p_peak, congested, verdict, factor, words, tail):
    out = C.attribution_summary(_attr(vol, zsh, p_peak, congested), CASE, REF, X_Q90)
    assert out["tau"] == 25  # 08-01 00:00 ~ 08-03 00:00, 2시간 간격, 양 끝 포함
    assert out["rd_mean"] == pytest.approx(0.208) and out["rd_max"] == pytest.approx(0.4)
    assert out["p_rd1_max"] == pytest.approx(p_peak)
    assert out["verdict"] == verdict and out["congested"] is congested and out["main_factor"] == factor
    assert out["volume"] == pytest.approx(vol) and out["beta_shift"] == pytest.approx(zsh)
    assert out["beta_uncertainty"] == pytest.approx(0.02)
    assert out["capacity"] is None  # 처리능력 공지 자료가 없어 계산하지 않는다
    t = out["text"]
    assert f"앱 규칙상 '{verdict}'" in t and "처리능력은 공지 자료가 없어 판정하지 않았다" in t
    assert "R_d 평균 0.21(최대 0.40)" in t
    assert "평가 기간 전체 중앙값은 각각 0.000, -0.012" in t  # |0.0003| < 0.0005 → 0.000
    for w in words:
        assert w in t, (w, t)
    assert t.endswith(tail), t
