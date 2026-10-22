"""거점·모수·시나리오 정의.

값은 모두 합성 시연용 가정이다. 실측·추정값이 아니며, 계획서 일정대로 본선 전 공개자료 구간에서
IF2(반복 입자필터) 최대우도로 정적 모수를 다시 추정한다.
결합행렬 M은 비율(x) 단위로 둔다. 계획서의 개수 단위 M_plan과는 M = D_N^-1 M_plan D_N (D_N = diag N)
관계이며, 이 변환에서 동역학과 R_d가 같다.
"""
from __future__ import annotations
import copy

NODES = [
    {"id": "AT", "name": "가상 자동화 터미널 A", "short": "자동화 A", "domain": "sea", "N": 80},
    {"id": "PN", "name": "신항 타 터미널", "short": "타 터미널", "domain": "sea", "N": 120},
    {"id": "L", "name": "게이트·도로(양산·울산)", "short": "육상", "domain": "land", "N": 600},
    {"id": "A", "name": "항공 반입(현행 인천)", "short": "항공", "domain": "air", "N": 60},
]
IDX = {n["id"]: k for k, n in enumerate(NODES)}

# M[i][j]: j -> i 영향률(1/h), 비율 단위
COUPLING = {
    ("PN", "AT"): 0.20,  # 자동화 터미널 전배 선박이 타 터미널 혼잡으로
    ("L", "AT"): 0.30,   # 하역 지연 -> 반출 지연
    ("L", "PN"): 0.25,
    ("AT", "L"): 0.14,   # 되먹임: 반출 지연 -> 장치율 상승 -> 하역 생산성 저하
    ("PN", "L"): 0.10,
    ("A", "L"): 0.40,    # 도착 지연 -> 항공 반입 마감 실패
    ("L", "A"): 0.08,    # 되먹임: 마감 실패 화물의 재보관·재배차 -> 게이트 부하
}

DEFAULTS = {
    "beta_bar": [0.34, 0.30, 0.36, 0.33],   # 기준 전파율(1/h), beta = beta_bar * exp(z)
    "gamma": [0.55, 0.55, 0.65, 0.60],      # 해소율(1/h), 기록의 평균 해소시간으로 고정
    "eps": [0.010, 0.008, 0.012, 0.010],    # 배경 지연 유입(1/h): 거점 밖 원인
    "kappa": 1.0, "tau0": 1.0,              # C = I + kappa*tau0*M, kappa*tau0 고정
    "ou_rate": 0.25, "ou_sigma": 0.15,      # 로그-OU 평균회귀 속도(1/h), 변동 크기(1/sqrt(h))
    "nb_size": 10.0, "report_rate": 1.0,    # 음이항 산포 r, 보고율 q
    "snap_n": [40, 40, 60, 40], "snap_conc": 40.0,  # 현재 지연 표본(베타-이항) 크기·집중도
    # 필터의 로그-OU 변동 여유와 재표본추출 뒤 입자 재생 잡음. 합성 세계 1000~1023에서 네 조합
    # (1.6/0.15, 1.3/0.10, 1.1/0.08, 1.0/0.05)을 비교해 80% 구간 포함률과 경보 선행시간의 균형으로 골랐다
    # (당시 입자 256개). 재현: python3 scripts/validate.py --world0 1000 --worlds 24 --sigma-mult 1.3 --rejuv 0.1
    "filter_sigma_mult": 1.3,
    "rejuvenate_sd": 0.10,
    "theta": 0.0,                           # 운영 판정 기준 탄력성(식별 가능한 시간 변동분만)
    "congestion_threshold": [0.15, 0.15, 0.15, 0.20],  # 적체(수준) 경보 기준선
    "hold_band": [0.35, 0.65],              # 원인 판정 보류 구간: P(R_d^(xi=1) > 1)이 이 안이면 단정하지 않는다
    "particles": 512,
    "world": 20261108,                      # 합성 세계(물동량·참 경로·관측) 난수
    "seed": 20261108,                       # 추정(입자필터·예측·대기열·화물 표본) 난수
    "dt": 1.0 / 6.0, "history_h": 24.0, "forecast_h": 24.0, "obs_every_h": 2.0,
    "smooth_lag_h": 6.0, "window_h": 24.0,
}

QUEUE = {  # 선석 대기열(동질 서버, 지수 서비스)
    "AT": {"servers": 4, "arrival": 0.55, "mu0": 0.22, "eta_b": 0.8, "n0": 3},
    "PN": {"servers": 8, "arrival": 1.10, "mu0": 0.22, "eta_b": 0.8, "n0": 5},
    "cap": 60,
}

# 화물. 모든 시각은 판단 시각(10:00) 기준 시간. 비용·마감·거리는 합성 가정이다.
#  import: 선박 하역 -> 컨테이너 준비 -> 터미널 반출(야드·게이트) -> 도로 -> (항공 반입 처리) -> 마감
#  export: 공장 출발 -> 도로 -> 터미널 반입(게이트·야드) -> 선적 반입 마감(cut-off)
#  candidates: (시각, 변경 비용). import는 차량의 터미널 도착 시각, export는 공장 출발 시각.
_IMPORT_COMMON = {
    "kind": "import", "terminal": "AT", "ship_arrival": 2.0, "doc_ready": 4.0, "transfer": 0.5,
    "h0": 0.6, "eta_l": 0.6, "eta_y": 1.2, "eta_u": 0.6,
    "priority": {"approval": 0.5, "cost": 300_000, "frac": 0.15},
}
CARGO = {
    "import_factory": dict(_IMPORT_COMMON, **{
        "label": "수입 원자재 → 양산 공장 야간 라인", "road": 1.0, "distance_km": 90,
        "deadline": 14.0, "deadline_label": "내일 00:00 야간 라인 투입", "fail_cost": 1_500_000,
        "epsilon": 0.10, "plan_index": 2,
        "candidates": [(6.0, 15_000), (8.0, 10_000), (10.0, 0), (11.5, 10_000), (12.0, 15_000)],
        "priority_times": [8.0, 10.0],
    }),
    "ulsan_parts": dict(_IMPORT_COMMON, **{
        "label": "수입 부품 → 울산 완성차 라인", "road": 1.3, "distance_km": 170,
        "deadline": 14.5, "deadline_label": "내일 00:30 라인 투입", "fail_cost": 3_000_000,
        "epsilon": 0.05, "plan_index": 2,
        "candidates": [(6.0, 15_000), (8.0, 10_000), (10.0, 0), (11.5, 10_000), (12.25, 15_000)],
        "priority_times": [8.0, 10.0],
    }),
    "sea_air": dict(_IMPORT_COMMON, **{
        "label": "Sea&Air 환적 → 인천공항 항공 반입", "road": 5.0, "distance_km": 820,
        "air_h0": 0.75, "eta_a": 0.8,
        "deadline": 18.5, "deadline_label": "내일 04:30 항공 반입 마감", "fail_cost": 800_000,
        "epsilon": 0.10, "plan_index": 2,
        "candidates": [(6.0, 15_000), (7.5, 10_000), (9.0, 0), (11.0, 10_000), (12.25, 15_000)],
        "priority_times": [7.5, 9.0],
    }),
    "ulsan_export": {
        "kind": "export", "terminal": "AT",
        "label": "울산 자동차부품 수출 → 신항 선적 반입", "road": 1.3, "distance_km": 170, "h2_reposition_h": 1.0,
        "h0": 0.5, "eta_l": 0.6, "eta_y": 1.2, "eta_u": 0.6,
        "deadline": 14.0, "deadline_label": "내일 00:00 선적 반입 마감", "fail_cost": 2_500_000,
        "epsilon": 0.10, "plan_index": 2,
        "candidates": [(6.0, 30_000), (8.0, 15_000), (10.0, 0), (11.5, 0), (12.5, 0)],
    },
}
# 운행비 = 거리 × km당 단가(연료·기사, 수소는 연료 보조 반영)와 차량 대기 단가. 모두 합성 가정.
COSTS = {"wait_per_h": 30_000, "approval_lead_h": 1.0, "per_km": {"디젤": 700, "수소": 550}}
# 수소 트럭(합성 가정): 신항 인근 차고지 18:45 복귀(잔여 60km), 충전 45분 + 경유 15분, 충전소 06:00~21:00.
# 1회 충전 주행거리 761km는 울산 수소전기 트랙터 실증 보도[19]의 값이다. 나머지는 합성 가정이며 실측이 아니다.
# 차고지에서 출발지까지 이동: 수입은 신항 터미널(depot_to_terminal_h), 수출은 화물별 h2_reposition_h.
H2 = {"charge_h": 0.75, "detour_h": 0.25, "depot_to_terminal_h": 0.5, "station_open": -4.0,
      "station_close": 11.0, "available_from": 8.75, "residual_km": 60, "full_km": 761}

FEATURE_BASE = {"wind": 6.0, "wave": 0.8}


def _shock(start, end, value):
    return {"start": start, "end": end, "value": value}


SCENARIOS = {
    "base": {
        "label": "평시", "summary": "특별한 충격이 없는 평일. 배경 지연만 있다.",
        "n0": {"AT": 3, "PN": 5}, "features": {}, "volume": {}, "beta_jump": {}, "gamma_mult": {},
        "berth_open": {}, "mu_mult": {}, "arrival_mult": {}, "h2": False,
    },
    "terminal_failure": {
        "label": "자동화 터미널 장비 고장(가상)",
        "summary": "판단 9시간 전 가상 자동화 터미널 A의 시스템 오류(2026년 보도 사례 유형을 본뜬 합성 장면이며 특정 터미널의 자료나 판정이 아니다). 판단 6시간 뒤까지 선석 처리능력이 절반으로 떨어지고(운영자 공지) 일부 선박이 타 터미널로 전배된다. 터미널은 18:00~23:30 수출 반입 제한을 공지했다.",
        "n0": {"AT": 6, "PN": 6}, "features": {"equip_AT": _shock(-9, 6, 1.0)},
        "volume": {"PN": _shock(-8, 24, 1.25)}, "beta_jump": {"AT": _shock(-9, 6, 0.35)},
        "gamma_mult": {"AT": _shock(6, 24, 0.8)}, "berth_open": {"AT": _shock(-9, 6, 0.5)},
        "mu_mult": {}, "arrival_mult": {}, "h2": False,
        "gate_closed": {"AT": _shock(8.0, 13.5, 1.0)},  # 공지된 수출 반입 제한(판단 시각 기준 시간)
    },
    "typhoon_diversion": {
        "label": "북중국 태풍 우회 물량",
        "summary": "중국 항만 폐쇄로 우회 선박이 몰려 신항 물동량이 1.6배가 된다. 부산 해상은 태풍 외곽 영향으로 바람이 조금 강해진다.",
        "n0": {"AT": 6, "PN": 9},
        "features": {"wind": _shock(-14, 24, 9.0), "wave": _shock(-14, 24, 1.4)},
        "volume": {"AT": _shock(-18, 24, 1.6), "PN": _shock(-18, 24, 1.6)}, "beta_jump": {},
        "gamma_mult": {}, "berth_open": {}, "mu_mult": {},
        "arrival_mult": {"AT": _shock(-14, 24, 1.4), "PN": _shock(-14, 24, 1.4)}, "h2": False,
    },
    "holiday_surge": {
        "label": "명절 전 물량 급증",
        "summary": "명절 연휴 전 반출입이 몰려 게이트·도로 물동량이 1.8배, 신항 물동량이 1.25배가 된다.",
        "n0": {"AT": 4, "PN": 6}, "features": {},
        "volume": {"L": _shock(-20, 24, 1.8), "AT": _shock(-20, 24, 1.25), "PN": _shock(-20, 24, 1.25)},
        "beta_jump": {}, "gamma_mult": {}, "berth_open": {}, "mu_mult": {}, "arrival_mult": {}, "h2": False,
    },
    "epidemic": {
        "label": "감염병 검역·인력 결근",
        "summary": "부산 항만·물류 근로자 집단감염으로 출근율이 75%로 떨어져(운영자 공지) 신항과 게이트·도로의 지연 해소와 선석 처리가 느려진다. 공지되지 않은 생산성 저하(10%)도 있다.",
        "n0": {"AT": 5, "PN": 7}, "features": {}, "volume": {}, "beta_jump": {},
        "gamma_mult": {k: _shock(-16, 24, 0.9) for k in ("AT", "PN", "L")},
        "staffing": {k: _shock(-16, 24, 0.75) for k in ("AT", "PN", "L")},
        "berth_open": {}, "mu_mult": {},
        "arrival_mult": {}, "h2": False,
    },
    "h2_trucks": {
        "label": "수소 트럭 배차(충전 제약)",
        "summary": "평시 조건에서 디젤과 수소 트럭 배차안을 함께 비교한다. 수소 트럭은 18:45 차고지 복귀(잔여 60km), 충전 45분+경유 15분, 차고지→신항 30분, 충전소 06:00~21:00(울산 수소 트랙터 실증을 본뜬 합성 가정).",
        "n0": {"AT": 3, "PN": 5}, "features": {}, "volume": {}, "beta_jump": {}, "gamma_mult": {},
        "berth_open": {}, "mu_mult": {}, "arrival_mult": {}, "h2": True,
    },
}


def scenario(name: str) -> dict:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    return copy.deepcopy(SCENARIOS[name])


def shock_value(spec: dict | None, t, default=1.0):
    """시각 배열 t에서 [start, end) 구간은 value, 나머지는 default."""
    import numpy as np
    t = np.asarray(t, dtype=float)
    out = np.full(t.shape, float(default))
    if spec:
        out[(t >= spec["start"]) & (t < spec["end"])] = float(spec["value"])
    return out
