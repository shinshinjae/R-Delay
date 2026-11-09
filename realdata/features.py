"""판단 시각 τ 기준 특징과 목표(단계 2). PROTOCOL v1 5·9절.

원칙: τ의 특징에는 τ까지 일어난(관측된) 것만 쓴다.
- 관제 이벤트: 일어난 시각 ≤ τ인 것만. 구간(정박지 대기·선석 체류)은 끝 이벤트가 τ 뒤면 'τ에 진행 중'으로 본다.
- 기상(ASOS·부이): 관측 시각 ≤ τ. 도로(1시간 평균): 그 시간이 끝난 시각(시작 + 1시간) ≤ τ.
- 태풍: 분석 시각 ≤ τ인 경로점만.
- 기준값(도로 기준속도·유효 콘존)은 학습 기간 자료로만 만든다(fit_road).
목표 T1은 미래(τ, τ+24h]의 라벨로 만든다(특징이 아님).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NEWPORT_BERTH = r"^신항 ?(?:\d부두|다목적부두)"
NEWPORT_ANCHOR = r"^신항 .*정박지"  # 신항 U·W 정박지(점검: 이 배들의 68%가 나중에 신항에 댐)
ANCHOR = r"박지|묘지|정박지"
NEWPORT_LATLON = (35.08, 128.83)
ANCHOR_ON, ANCHOR_OFF = ("입항", "투묘"), ("양묘", "접안", "출항")
BERTH_ON, BERTH_OFF = ("입항", "접안"), ("이안", "출항")
KEY = ["clsgn", "etryptYear", "etryptCo"]
TRAIN_END = pd.Timestamp("2025-01-01")
WAIT_X, WAIT_CAP, HORIZON_H = 12.0, 168.0, 24

# 모형에 넣는 특징(PROTOCOL v1: 이 목록을 고정한다)
S1_FEATURES = ["anchor_np", "max_wait_np_h"]
FEATURES = [
    "anchor_np", "max_wait_np_h", "long_wait_np", "anchor_all", "anchor_np_6h_ago", "anchor_np_24h_ago",
    "arr_np_24h", "arr_np_grt_24h", "arr_np_6h", "berthings_np_24h", "departures_np_24h", "berthed_np",
    "long_waits_started_72h",
    "wind_busan", "wind_busan_max6", "wind_busan_max24", "rain_busan_6h", "wind_changwon_max6",
    "wave_geoje", "wave_geoje_max6", "wave_geoje_max24", "gust_geoje_max6",
    "ty_dist_km", "ty_within_500",
    "slot_sin", "slot_cos", "dow", "weekend", "holiday", "holiday_next3d", "month_sin", "month_cos",
    "road_cong", "road_cong_max24", "road_speed_ratio",
]
# S3 스케줄 기준선(PROTOCOL v1.4, 시험 전 등록): 배마다 지난 기항 간격·지난 대기로 '곧 올 배'를 어림한다(τ까지 알려진 것만)
SCHED_FEATURES = ["due_med_vrate_wide", "due_med_grtrate", "due_wk_vrate", "arr_npsmall_same_slot_4w", "arr_npanc_same_slot_4w",
                  "hz_vrate_x_small"]
S3_FEATURES = SCHED_FEATURES + ["slot_sin", "slot_cos", "dow_sin", "dow_cos", "weekend", "holiday", "holiday_next3d"]
GRT_BINS = [0, 10000, 20000, 40000, 80000, 1e7]


# ---------- 관제 이벤트 → 구간 ----------
def container_events(cntl: pd.DataFrame) -> pd.DataFrame:
    c = cntl[cntl["vsslKndNm"].str.contains("컨테이너", na=False)]
    c = c[KEY + ["fcltyNm", "cntrlNm", "cntrlOpertDt_t", "vsslGrtg"]].rename(columns={"cntrlOpertDt_t": "t"})
    c = c.drop_duplicates(subset=KEY + ["fcltyNm", "cntrlNm", "t"]).sort_values("t", kind="stable").reset_index(drop=True)
    c["grt"] = pd.to_numeric(c["vsslGrtg"], errors="coerce")
    c["anc"] = c["fcltyNm"].str.contains(ANCHOR, na=False)
    c["np_anchor"] = c["fcltyNm"].str.match(NEWPORT_ANCHOR, na=False)
    c["np_berth"] = c["fcltyNm"].str.match(NEWPORT_BERTH, na=False)
    return c


def _pair(ev: pd.DataFrame, on_ok: pd.Series, off_names: tuple, same_facility: bool) -> pd.DataFrame:
    """항차별로 시작 이벤트(on_ok) 다음 첫 끝 이벤트를 짝짓는다. 끝이 없으면 NaT(자료 끝까지 진행 중)."""
    e = ev.assign(_on=on_ok.to_numpy(), _off=ev["cntrlNm"].isin(off_names).to_numpy())
    e = e[e["_on"] | e["_off"]]
    groups = KEY + (["fcltyNm"] if same_facility else [])
    rows = []
    for k, g in e.groupby(groups, sort=False):
        start = fac = None
        for on, off, t, f, grt in zip(g["_on"].to_numpy(), g["_off"].to_numpy(), g["t"].to_numpy(), g["fcltyNm"].to_numpy(), g["grt"].to_numpy()):
            if on and start is None:
                start, fac, g0 = t, f, grt
            elif off and start is not None:
                rows.append((*k[:3], fac, start, t, g0))
                start = None
        if start is not None:
            rows.append((*k[:3], fac, start, np.datetime64("NaT", "ns"), g0))
    out = pd.DataFrame(rows, columns=KEY + ["facility", "start", "end", "grt"])
    out["end"] = pd.to_datetime(out["end"])
    return out


def anchorage_intervals(ev: pd.DataFrame) -> pd.DataFrame:
    """정박지 대기 구간: 정박지에서 입항·투묘 → 양묘·접안·출항."""
    iv = _pair(ev, ev["cntrlNm"].isin(ANCHOR_ON) & ev["anc"], ANCHOR_OFF, same_facility=False)
    iv["np_anchor"] = iv["facility"].str.match(NEWPORT_ANCHOR, na=False)
    return iv


def berth_intervals(ev: pd.DataFrame) -> pd.DataFrame:
    """신항 선석 체류 구간: 같은 시설에서 (입항|접안) → (이안|출항)."""
    e = ev[ev["np_berth"]]
    return _pair(e, e["cntrlNm"].isin(BERTH_ON), BERTH_OFF, same_facility=True)


def arrivals(ev: pd.DataFrame) -> pd.DataFrame:
    """항차의 첫 입항(시각·시설·총톤수). 신항 선석·신항 정박지 입항만 '신항 관련'으로 표시."""
    a = ev[ev["cntrlNm"] == "입항"].drop_duplicates(subset=KEY, keep="first")
    a = a[KEY + ["t", "fcltyNm", "grt"]].rename(columns={"fcltyNm": "facility"})
    a["np"] = a["facility"].str.match(NEWPORT_BERTH, na=False) | a["facility"].str.match(NEWPORT_ANCHOR, na=False)
    return a.reset_index(drop=True)


# ---------- 시각 격자 도우미 ----------
def tau_grid(start, end) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start).ceil("2h"), pd.Timestamp(end).floor("2h"), freq="2h")


def _np64(s) -> np.ndarray:
    return pd.to_datetime(pd.Series(s)).to_numpy(dtype="datetime64[ns]")


def _active_count(start, end, grid, min_age_h: float | None = None) -> np.ndarray:
    """τ에 진행 중(start ≤ τ < end, end가 없으면 계속)인 구간 수. min_age_h가 있으면 τ − start ≥ 그 시간인 것만."""
    s = _np64(start)
    e = _np64(pd.Series(end).fillna(pd.Timestamp("2200-01-01")))
    g = grid.to_numpy(dtype="datetime64[ns]")
    if min_age_h is None:
        return np.searchsorted(np.sort(s), g, side="right") - np.searchsorted(np.sort(e), g, side="right")
    lag = np.timedelta64(int(min_age_h * 3600), "s")
    # start ≤ τ − lag 이고 end > τ
    s_ok = np.searchsorted(np.sort(s), g - lag, side="right")
    # 그중 end ≤ τ인 것을 빼야 한다: start ≤ τ − lag 인 구간들 중 end ≤ τ 수 → 행렬로(구간 수가 작음)
    started = s[None, :] <= (g - lag)[:, None]
    ended = e[None, :] <= g[:, None]
    return s_ok - (started & ended).sum(axis=1)


def _max_age_h(start, end, grid) -> np.ndarray:
    """τ에 진행 중인 구간 중 가장 오래된 것의 경과 시간(시간). 없으면 0."""
    s = _np64(start)
    e = _np64(pd.Series(end).fillna(pd.Timestamp("2200-01-01")))
    g = grid.to_numpy(dtype="datetime64[ns]")
    out = np.zeros(len(g))
    for i0 in range(0, len(g), 2000):  # 메모리를 아끼려고 나눠 계산
        gg = g[i0:i0 + 2000]
        act = (s[None, :] <= gg[:, None]) & (e[None, :] > gg[:, None])
        age = np.where(act, (gg[:, None] - s[None, :]) / np.timedelta64(1, "h"), 0.0)
        out[i0:i0 + 2000] = age.max(axis=1) if age.shape[1] else 0.0
    return out


def _count_in(times, grid, hours: float, weights=None) -> np.ndarray:
    """(τ − hours, τ]에 든 시각 수(또는 가중 합)."""
    t = _np64(times)
    order = np.argsort(t)
    t = t[order]
    g = grid.to_numpy(dtype="datetime64[ns]")
    hi = np.searchsorted(t, g, side="right")
    lo = np.searchsorted(t, g - np.timedelta64(int(hours * 3600), "s"), side="right")
    if weights is None:
        return (hi - lo).astype(float)
    w = np.nan_to_num(np.asarray(weights, dtype=float)[order])
    cs = np.concatenate([[0.0], np.cumsum(w)])
    return cs[hi] - cs[lo]


def _rolling_at(series: pd.Series, grid, hours: int, how: str, avail_lag_h: int = 0) -> np.ndarray:
    """시간 자료를 τ에 맞춰 (τ − hours, τ] 구간 집계. avail_lag_h: 자료가 그만큼 늦게 확정됨(도로 1시간 평균은 1)."""
    s = series.sort_index()
    s = s[~s.index.duplicated()]
    at = grid - pd.Timedelta(hours=avail_lag_h)
    end = max(s.index.max(), at.max()).ceil("h")  # 요청 시각까지 늘려야 τ에서 자른 자료와 결과가 같다
    hourly = s.reindex(pd.date_range(s.index.min().floor("h"), end, freq="h"))
    if how == "last":
        r = hourly.ffill(limit=3)
    elif how == "max":
        r = hourly.rolling(hours, min_periods=1).max()
    elif how == "sum":
        r = hourly.rolling(hours, min_periods=1).sum()
    elif how == "mean":
        r = hourly.rolling(hours, min_periods=1).mean()
    else:
        raise ValueError(how)
    return r.reindex(at).to_numpy(dtype=float)


# ---------- 기상·태풍·달력 ----------
def weather_features(asos: pd.DataFrame, buoy: pd.DataFrame, grid) -> dict:
    a = asos.set_index("t")
    bus = a[a["stn"].astype(str) == "159"]
    chw = a[a["stn"].astype(str) == "155"]
    b = buoy.set_index("t")
    geo = b[b["stn"].astype(str) == "22104"]
    rain = bus["rain"].fillna(0.0)  # ASOS 강수 빈칸 = 비 없음
    return {
        "wind_busan": _rolling_at(bus["wind"], grid, 1, "last"),
        "wind_busan_max6": _rolling_at(bus["wind"], grid, 6, "max"),
        "wind_busan_max24": _rolling_at(bus["wind"], grid, 24, "max"),
        "rain_busan_6h": _rolling_at(rain, grid, 6, "sum"),
        "wind_changwon_max6": _rolling_at(chw["wind"], grid, 6, "max"),
        "wave_geoje": _rolling_at(geo["wave_sig"], grid, 1, "last"),
        "wave_geoje_max6": _rolling_at(geo["wave_sig"], grid, 6, "max"),
        "wave_geoje_max24": _rolling_at(geo["wave_sig"], grid, 24, "max"),
        "gust_geoje_max6": _rolling_at(geo["gust"], grid, 6, "max"),
    }


def haversine_km(lat, lon, lat0=NEWPORT_LATLON[0], lon0=NEWPORT_LATLON[1]):
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    a = np.sin((lat - np.radians(lat0)) / 2) ** 2 + np.cos(lat) * np.cos(np.radians(lat0)) * np.sin((lon - np.radians(lon0)) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


TY_RELEASE_LAG_H = 3.0  # 분석 뒤 통보가 나오기까지(보수적으로 3시간 뒤에 쓸 수 있다고 본다)


def typhoon_features(track: pd.DataFrame, grid, active_h: float = 12.0, far_km: float = 3000.0) -> dict:
    """τ까지 통보된 경로점(분석 시각 KST + 3시간 ≤ τ)만 쓴다. 마지막 통보가 τ − active_h 안인 태풍을 '활동 중'으로 보고 가장 가까운 거리."""
    tr = track.dropna(subset=["t", "lat", "lon"]).copy()
    tr["km"] = haversine_km(tr["lat"], tr["lon"])
    tr = tr.sort_values("t")
    g = grid.to_numpy(dtype="datetime64[ns]")
    dist = np.full(len(g), far_km)
    for _, s in tr.groupby(["year", "num"]):
        t = (s["t"] + pd.Timedelta(hours=TY_RELEASE_LAG_H)).to_numpy(dtype="datetime64[ns]")  # 쓸 수 있게 된 시각
        km = s["km"].to_numpy()
        idx = np.searchsorted(t, g, side="right") - 1  # τ까지 마지막으로 통보된 점
        ok = (idx >= 0)
        ok[ok] = (g[ok] - t[idx[ok]]) <= np.timedelta64(int(active_h * 3600), "s")
        d = np.where(ok, km[np.clip(idx, 0, len(km) - 1)], far_km)
        dist = np.minimum(dist, d)
    return {"ty_dist_km": dist, "ty_within_500": (dist <= 500).astype(float)}


def calendar_features(grid) -> dict:
    import holidays as hol
    years = range(grid.min().year - 1, grid.max().year + 2)
    kr = hol.country_holidays("KR", years=years)
    d = grid.normalize()
    is_hol = np.array([x.date() in kr for x in d])
    nxt = np.array([any((x + pd.Timedelta(days=k)).date() in kr for k in range(1, 4)) for x in d])
    slot = grid.hour // 2
    return {"slot": slot.to_numpy(), "slot_sin": np.sin(2 * np.pi * slot / 12), "slot_cos": np.cos(2 * np.pi * slot / 12),
            "dow": grid.day_of_week.to_numpy(), "dow_sin": np.sin(2 * np.pi * grid.day_of_week / 7), "dow_cos": np.cos(2 * np.pi * grid.day_of_week / 7),
            "weekend": (grid.day_of_week >= 5).astype(float),
            "holiday": is_hol.astype(float), "holiday_next3d": nxt.astype(float), "month": grid.month.to_numpy(),
            "month_sin": np.sin(2 * np.pi * (grid.month - 1) / 12), "month_cos": np.cos(2 * np.pi * (grid.month - 1) / 12)}


# ---------- 도로 ----------
def fit_road(road: pd.DataFrame, train_end=TRAIN_END, max_zero=0.2, q=0.85) -> dict:
    """학습 기간 자료로만: 유효 콘존(속도 0 비율 < max_zero), 콘존·요일·시각별 기준속도(85분위)."""
    tr = road[road["t"] < train_end]
    zero = tr.groupby("zone_id")["speed"].apply(lambda s: float((s <= 0).mean()))
    zones = sorted(zero[zero < max_zero].index)
    v = tr[(tr["speed"] > 0) & tr["zone_id"].isin(zones)]
    ref = v.groupby(["zone_id", v["t"].dt.day_of_week.rename("dow"), v["t"].dt.hour.rename("hour")])["speed"].quantile(q)
    return {"zones": zones, "ref": ref, "ratio": 0.6, "train_end": str(train_end)}


def road_index(road: pd.DataFrame, fit: dict) -> pd.DataFrame:
    """시간별 혼잡 지수(cong) = 혼잡 콘존 수 / 측정이 있는 유효 콘존 수, 속도비(ratio) = 유효 콘존의 속도/기준속도 평균.
    측정이 없는 시간은 행이 없다(→ NaN)."""
    r = road[road["zone_id"].isin(fit["zones"]) & (road["speed"] > 0)].copy()
    idx = pd.MultiIndex.from_arrays([r["zone_id"], r["t"].dt.day_of_week, r["t"].dt.hour])
    r["ref"] = fit["ref"].reindex(idx).to_numpy()
    r = r.dropna(subset=["ref"])
    r["cong"] = r["speed"] < fit["ratio"] * r["ref"]
    r["ratio"] = r["speed"] / r["ref"]
    return r.groupby("t").agg(cong=("cong", "mean"), ratio=("ratio", "mean"))


def road_features(road: pd.DataFrame, fit: dict, grid) -> dict:
    s = road_index(road, fit)
    # 1시간 평균(시작 시각 t)은 t+1시간에 확정 → (τ−2h, τ]에 끝난 두 시간 = 시작 τ−2h, τ−1h
    return {"road_cong": _rolling_at(s["cong"], grid, 2, "mean", avail_lag_h=1),
            "road_cong_max24": _rolling_at(s["cong"], grid, 24, "max", avail_lag_h=1),
            "road_speed_ratio": _rolling_at(s["ratio"], grid, 2, "mean", avail_lag_h=1)}


# ---------- 전체 ----------
def sea_features(ev: pd.DataFrame, grid) -> dict:
    anc = anchorage_intervals(ev)
    npa = anc[anc["np_anchor"]]
    brt = berth_intervals(ev)
    arr = arrivals(ev)
    arr_np = arr[arr["np"]]
    out = {
        "anchor_np": _active_count(npa["start"], npa["end"], grid).astype(float),
        "max_wait_np_h": _max_age_h(npa["start"], npa["end"], grid),
        "long_wait_np": _active_count(npa["start"], npa["end"], grid, min_age_h=WAIT_X).astype(float),
        "anchor_all": _active_count(anc["start"], anc["end"], grid).astype(float),
        "arr_np_24h": _count_in(arr_np["t"], grid, 24),
        "arr_np_grt_24h": _count_in(arr_np["t"], grid, 24, weights=arr_np["grt"]) / 1e5,
        "arr_np_6h": _count_in(arr_np["t"], grid, 6),
        "berthings_np_24h": _count_in(brt["start"], grid, 24),
        "departures_np_24h": _count_in(brt["end"].dropna(), grid, 24),
        "berthed_np": _active_count(brt["start"], brt["end"], grid).astype(float),
    }
    # 12시간 넘게 기다린 배가 지난 72시간 안에 정박을 시작한 수(τ까지 12시간이 지난 것만 셈)
    lw = npa[(npa["end"].isna()) | ((npa["end"] - npa["start"]) > pd.Timedelta(hours=WAIT_X))]
    reach = lw["start"] + pd.Timedelta(hours=WAIT_X)  # 12시간을 넘긴 시각(이때 알 수 있음)
    out["long_waits_started_72h"] = _count_in(reach, grid, 72)
    lag6, lag24 = grid - pd.Timedelta(hours=6), grid - pd.Timedelta(hours=24)
    out["anchor_np_6h_ago"] = _active_count(npa["start"], npa["end"], lag6).astype(float)
    out["anchor_np_24h_ago"] = _active_count(npa["start"], npa["end"], lag24).astype(float)
    return out


def fit_schedule(voy: pd.DataFrame, train_end=TRAIN_END) -> dict:
    """학습 기간 상수: 대기 > 12h(168h 이하) 기저율과 총톤수 구간별 비율(학습 기간 안에 접안까지 끝난 항차만)."""
    v = voy[(voy["arrival_t"] < train_end) & (voy["berth_t"] < train_end)]
    q = (v["wait_h"] > WAIT_X) & (v["wait_h"] <= WAIT_CAP)
    gr = q.groupby(pd.cut(v["vsslGrtg"], GRT_BINS), observed=False).mean()
    return {"prior": float(q.mean()), "grt_rate": [float(x) for x in gr.to_numpy()]}


def schedule_features(ev: pd.DataFrame, voy: pd.DataFrame, grid, fit: dict, m_: float = 2.0) -> dict:
    """τ까지 알려진 것만: 신항 기항(첫 입항) 기록, 배마다 지난 기항 간격, τ까지 접안이 끝난 지난 항차의 대기 여부.
    - due_*: 마지막 기항 ≤ τ이고 아직 다시 오지 않은 배 중, '마지막 기항 + 간격 중앙값(또는 주 단위)'이 창 안에 드는 배 수(가중)
    - arr_*_same_slot_4w: 7·14·21·28일 전 같은 24시간 창의 도착 수 평균
    - hz_vrate_x_small: 배마다 지난 간격으로 어림한 '24시간 안 도착 확률' × 그 배의 지난 대기 비율(2만 톤 미만만)"""
    from scipy.stats import norm
    g = grid.to_numpy(dtype="datetime64[ns]")
    prior = fit["prior"]
    arr = arrivals(ev)
    arr["small"] = arr["grt"] < 20000
    arr["np_anchor"] = arr["facility"].str.match(NEWPORT_ANCHOR, na=False)
    out = {}
    for nm, msk in {"npanc": arr["np_anchor"], "npsmall": arr["np"] & arr["small"]}.items():
        t = np.sort(_np64(arr.loc[msk, "t"]))
        lags = []
        for L in (7, 14, 21, 28):
            lo = g - np.timedelta64(L * 24, "h")
            lags.append(np.searchsorted(t, lo + np.timedelta64(24, "h"), side="right") - np.searchsorted(t, lo, side="right"))
        out[f"arr_{nm}_same_slot_4w"] = np.mean(lags, axis=0).astype(float)
    v = voy.assign(q=(voy["wait_h"] > WAIT_X) & (voy["wait_h"] <= WAIT_CAP))
    a2 = arr[arr["np"]].sort_values("t").reset_index(drop=True)
    n0 = len(a2)
    a2 = a2.merge(v[KEY + ["q", "berth_t"]], on=KEY, how="left")
    assert len(a2) == n0
    a2["next_t"] = a2.groupby("clsgn")["t"].shift(-1)  # τ와 비교만 한다(다음 기항이 τ 뒤인지는 τ에 알 수 있음)
    a2["gap_prev"] = a2.groupby("clsgn")["t"].diff().dt.total_seconds() / 3600
    a2["gap_med"] = a2.groupby("clsgn")["gap_prev"].transform(lambda s: s.expanding().median())
    a2["gap_wk"] = np.maximum(1, np.round(a2["gap_med"] / 168)) * 168
    a2["qk"] = np.where(a2["berth_t"].notna(), a2["q"].fillna(False).astype(float), np.nan)
    rates = np.full(len(a2), prior)
    for _, gi in a2.groupby("clsgn").groups.items():
        gi = np.asarray(gi)
        tt, bt, qq = _np64(a2.loc[gi, "t"]), _np64(a2.loc[gi, "berth_t"]), a2.loc[gi, "qk"].to_numpy()
        for j in range(len(gi)):
            ok = (np.arange(len(gi)) <= j) & ~np.isnat(bt) & (bt <= tt[j])  # 그 기항 때까지 접안이 끝난 지난 항차만
            rates[gi[j]] = (np.nansum(qq[ok]) + prior * m_) / (ok.sum() + m_)
    a2["vrate"] = rates
    gb = pd.cut(a2["grt"], GRT_BINS)
    a2["grt_rate"] = pd.Series(np.asarray(fit["grt_rate"])[gb.cat.codes.clip(lower=0)], index=a2.index).where(gb.notna(), prior).fillna(prior)
    at, nx = _np64(a2["t"]), _np64(a2["next_t"].fillna(pd.Timestamp("2200-01-01")))

    def due(pred_t, w, lo_h, hi_h):
        p, w = _np64(pred_t), np.nan_to_num(np.asarray(w, float))
        res = np.zeros(len(g))
        for i0 in range(0, len(g), 1500):
            gg = g[i0:i0 + 1500][:, None]
            mm = (at[None, :] <= gg) & (p[None, :] > gg + np.timedelta64(lo_h, "h")) & (p[None, :] <= gg + np.timedelta64(hi_h, "h")) & (nx[None, :] > gg)
            res[i0:i0 + 1500] = (mm * w[None, :]).sum(1)
        return res

    pm = a2["t"] + pd.to_timedelta(a2["gap_med"], unit="h")
    pw = a2["t"] + pd.to_timedelta(a2["gap_wk"], unit="h")
    out["due_med_vrate_wide"] = due(pm, a2["vrate"], -24, 48)
    out["due_med_grtrate"] = due(pm, a2["grt_rate"], 0, 24)
    out["due_wk_vrate"] = due(pw, a2["vrate"], 0, 24)
    SIG, HZ = 12.0, 24.0
    gh = (g - g[0]) / np.timedelta64(1, "h")
    hz = np.zeros(len(g))
    for _, gi in a2.groupby("clsgn").groups.items():
        gi = np.asarray(gi)
        t = (_np64(a2.loc[gi, "t"]) - g[0]) / np.timedelta64(1, "h")
        bt = _np64(a2.loc[gi, "berth_t"])
        bt = np.where(np.isnat(bt), np.inf, (bt - g[0]) / np.timedelta64(1, "h"))
        qq = a2.loc[gi, "q"].astype(float).to_numpy()
        small = (a2.loc[gi, "grt"].to_numpy(dtype=float) < 20000).astype(float)
        for k in range(2, len(gi) + 1):
            last = t[k - 1]
            gaps = np.diff(t[:k])
            gaps = gaps[gaps > 6]
            if len(gaps) == 0:
                continue
            nxt = t[k] if k < len(gi) else np.inf
            lo = np.searchsorted(gh, last, side="left")
            hi = np.searchsorted(gh, min(nxt, last + gaps.max() + 72), side="left")
            if hi <= lo:
                continue
            s = gh[lo:hi] - last
            Fs = norm.cdf((s[:, None] - gaps[None, :]) / SIG).mean(1)
            Fe = norm.cdf((s[:, None] + HZ - gaps[None, :]) / SIG).mean(1)
            h = np.clip((Fe - Fs) / np.maximum(1 - Fs, 0.05), 0, 1)
            known = bt[:k] <= last
            vr = (np.nansum(qq[:k][known]) + prior * m_) / (known.sum() + m_)
            hz[lo:hi] += h * vr * small[k - 1]
    out["hz_vrate_x_small"] = hz
    return out


def build_features(data: dict, grid, road_fit: dict | None = None, voy: pd.DataFrame | None = None,
                   sched_fit: dict | None = None) -> pd.DataFrame:
    """data: cntl·asos·buoy·typhoon_track·road(io.load 결과). road_fit이 없으면 학습 기간으로 만든다.
    voy(라벨)를 주면 스케줄 특징도 만든다(τ까지 접안이 끝난 항차의 대기만 쓴다)."""
    ev = container_events(data["cntl"])
    cols = {}
    cols.update(sea_features(ev, grid))
    if voy is not None:
        cols.update(schedule_features(ev, voy, grid, sched_fit or fit_schedule(voy)))
    cols.update(weather_features(data["asos"], data["buoy"], grid))
    cols.update(typhoon_features(data["typhoon_track"], grid))
    cols.update(calendar_features(grid))
    rf = road_fit or fit_road(data["road"])
    cols.update(road_features(data["road"], rf, grid))
    return pd.DataFrame(cols, index=grid)


# ---------- 목표 ----------
def targets(voy: pd.DataFrame, grid, thr: float = WAIT_X, cap: float = WAIT_CAP, horizon_h: int = HORIZON_H) -> pd.Series:
    """T1: (τ, τ+horizon]에 도착한 신항 컨테이너선 중 thr < 대기 ≤ cap이 1척 이상."""
    w = voy["wait_h"]
    pos = np.sort(voy.loc[(w > thr) & (w <= cap), "arrival_t"].to_numpy(dtype="datetime64[ns]"))
    g = grid.to_numpy(dtype="datetime64[ns]")
    lo = np.searchsorted(pos, g, side="right")
    hi = np.searchsorted(pos, g + np.timedelta64(horizon_h, "h"), side="right")
    return pd.Series(((hi - lo) > 0).astype(int), index=grid, name=f"y_{int(thr)}")


def data_tau_end(voy: pd.DataFrame) -> pd.Timestamp:
    """마지막 판단 시각 τ = 도착 자료 끝(마지막 도착일 다음 0시) − 7일, 2시간 격자(PROTOCOL 1절: 대기가 아직 안 끝난 배 때문에 생기는 오른쪽 잘림을 피함).
    2026-11-04까지의 자료에서는 2026-10-29 00:00(예전에 코드에 적어 둔 값과 같음). 자료가 늘면 평가 기간만 늘어난다."""
    return (pd.Timestamp(voy["arrival_t"].max()).ceil("D") - pd.Timedelta(days=7)).floor("2h")


def qualifying_arrivals(voy: pd.DataFrame, thr: float = WAIT_X, cap: float = WAIT_CAP) -> pd.Series:
    w = voy["wait_h"]
    return voy.loc[(w > thr) & (w <= cap), "arrival_t"].sort_values().reset_index(drop=True)
