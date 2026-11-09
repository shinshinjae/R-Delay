"""실자료 백테스트 특징의 정확성·누설 검사(PROTOCOL v1 9절).

- 가짜 관제 이벤트로 τ의 특징 값을 손으로 확인하고, τ 뒤 이벤트를 바꿔도 τ의 특징이 같은지 본다.
- 실자료 캐시가 있으면(이 컴퓨터) 무작위 τ에서 'τ 뒤 자료를 지운 결과 = 전체 자료 결과'인지 본다.
pandas가 없으면(웹앱용 CI) 건너뛴다.
"""
from __future__ import annotations

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
from realdata import features as F  # noqa: E402

SYN = [
    ("A", "신항 U-1 정박지", "입항", "2024-03-01 00:30"),
    ("A", "신항 U-1 정박지", "양묘", "2024-03-01 15:10"),
    ("A", "신항 3부두 1선석", "접안", "2024-03-01 16:00"),
    ("A", "신항 3부두 1선석", "출항", "2024-03-02 10:00"),
    ("B", "신항 2부두 2선석", "입항", "2024-03-01 03:00"),
    ("B", "신항 2부두 2선석", "출항", "2024-03-01 20:00"),
    ("C", "남외항 N-3박지", "입항", "2024-03-01 01:00"),
    ("C", "남외항 N-3박지", "양묘", "2024-03-02 01:00"),
]


def _cntl(rows):
    df = pd.DataFrame(rows, columns=["clsgn", "fcltyNm", "cntrlNm", "cntrlOpertDt_t"])
    df["etryptYear"], df["etryptCo"], df["vsslKndNm"], df["vsslGrtg"] = "2024", "001", "풀컨테이너선", "50000"
    df["cntrlOpertDt_t"] = pd.to_datetime(df["cntrlOpertDt_t"])
    return df


def _sea(rows, taus):
    grid = pd.DatetimeIndex(pd.to_datetime(taus))
    return pd.DataFrame(F.sea_features(F.container_events(_cntl(rows)), grid), index=grid)


def test_sea_features_by_hand():
    s = _sea(SYN, ["2024-03-01 02:00", "2024-03-01 04:00", "2024-03-01 14:00", "2024-03-01 16:00"])
    t = lambda h: pd.Timestamp(f"2024-03-01 {h}")  # noqa: E731
    assert s.loc[t("02:00"), "anchor_np"] == 1 and s.loc[t("02:00"), "anchor_all"] == 2
    assert s.loc[t("02:00"), "max_wait_np_h"] == pytest.approx(1.5)
    assert s.loc[t("02:00"), "berthed_np"] == 0 and s.loc[t("02:00"), "arr_np_24h"] == 1
    assert s.loc[t("04:00"), "berthed_np"] == 1 and s.loc[t("04:00"), "arr_np_24h"] == 2
    assert s.loc[t("14:00"), "long_wait_np"] == 1  # A가 13.5시간째 대기
    assert s.loc[t("16:00"), "anchor_np"] == 0 and s.loc[t("16:00"), "berthed_np"] == 2 and s.loc[t("16:00"), "anchor_all"] == 1


def test_future_events_do_not_change_features():
    taus = pd.date_range("2024-03-01 00:00", "2024-03-01 12:00", freq="2h")
    base = _sea(SYN, taus)
    changed = [r for r in SYN if not (r[0] == "B" and r[2] == "출항")]  # 12시 뒤 이벤트 지우기
    changed = [(c, f, n, "2024-03-01 20:00" if (c == "A" and n == "양묘") else t) for c, f, n, t in changed]  # 12시 뒤 시각 바꾸기
    changed.append(("D", "신항 U-2 정박지", "입항", "2024-03-01 13:00"))  # 12시 뒤 새 배
    pd.testing.assert_frame_equal(base, _sea(changed, taus))


def test_targets_window():
    voy = pd.DataFrame({"arrival_t": pd.to_datetime(["2024-03-02 01:00", "2024-03-05 00:00", "2024-03-07 00:00"]),
                        "wait_h": [13.0, 5.0, 200.0]})
    grid = pd.DatetimeIndex(pd.to_datetime(["2024-03-01 00:00", "2024-03-01 02:00", "2024-03-04 00:00", "2024-03-06 02:00"]))
    y = F.targets(voy, grid)
    # (τ, τ+24h]: 01:00 도착(13h 대기)은 3월 1일 02:00에 잡히고 00:00에는 창 밖(25시간 뒤). 5h·200h(상한 초과)는 양성 아님
    assert y.tolist() == [0, 1, 0, 0]


def _real_data():
    from realdata.paths import CACHE
    if not (CACHE / "cntl").exists():
        pytest.skip("실자료 캐시 없음(python -m realdata.io 먼저)")
    from realdata.io import load
    return {n: load(n) for n in ("cntl", "asos", "buoy", "typhoon_track", "road")}


@pytest.mark.realdata
def test_real_data_truncation_leak():
    data = _real_data()
    road_fit = F.fit_road(data["road"])
    rng = np.random.default_rng(7)
    grid = F.tau_grid("2023-02-01", "2026-09-25")
    for tau in grid[rng.choice(len(grid), 6, replace=False)]:
        lo = tau - pd.Timedelta(days=15)
        win = dict(data)
        win["cntl"] = data["cntl"][data["cntl"]["cntrlOpertDt_t"] >= lo]
        full = F.build_features(win, pd.DatetimeIndex([tau]), road_fit)
        cut = {
            "cntl": win["cntl"][win["cntl"]["cntrlOpertDt_t"] <= tau],
            "asos": data["asos"][data["asos"]["t"] <= tau],
            "buoy": data["buoy"][data["buoy"]["t"] <= tau],
            "typhoon_track": data["typhoon_track"][data["typhoon_track"]["t"] <= tau],
            "road": data["road"][data["road"]["t"] + pd.Timedelta(hours=1) <= tau],
        }
        part = F.build_features(cut, pd.DatetimeIndex([tau]), road_fit)
        a, b = full[F.FEATURES].to_numpy(float), part[F.FEATURES].to_numpy(float)
        bad = [f for f, x, y in zip(F.FEATURES, a[0], b[0]) if not (np.isclose(x, y) or (np.isnan(x) and np.isnan(y)))]
        assert not bad, f"τ={tau}: τ 뒤 자료가 바뀌면 달라지는 특징 {bad}"


@pytest.mark.realdata
def test_typhoon_track_clock_is_kst():
    """태풍 경로의 분석시각(UTC)을 KST로 바꿨는지: 경로 첫 점 = 태풍목록 발생 시각(KST)."""
    from realdata.paths import CACHE
    if not (CACHE / "typhoon_track.parquet").exists():
        pytest.skip("실자료 캐시 없음")
    from realdata.io import load
    tr, tl = load("typhoon_track"), load("typhoon_list")
    first = tr.groupby(["year", "num"])["t"].min()
    j = first.to_frame("first").join(tl.set_index(["year", "num"])["start"], how="inner")
    assert len(j) > 50 and (j["first"] == j["start"]).mean() > 0.95


@pytest.mark.realdata
def test_schedule_features_truncation_leak():
    """스케줄 특징: τ 뒤 관제 이벤트를 지우고 τ까지 접안이 끝난 라벨만 남겨도 τ의 값이 같은지."""
    from realdata.paths import CACHE
    if not (CACHE / "cntl").exists():
        pytest.skip("실자료 캐시 없음")
    from realdata.io import load
    cntl, voy = load("cntl"), load("voyages")
    fit = F.fit_schedule(voy)
    ev = F.container_events(cntl)
    rng = np.random.default_rng(11)
    grid = F.tau_grid("2023-03-01", "2026-09-25")
    for tau in grid[rng.choice(len(grid), 4, replace=False)]:
        g = pd.DatetimeIndex([tau])
        full = F.schedule_features(ev, voy, g, fit)
        part = F.schedule_features(F.container_events(cntl[cntl["cntrlOpertDt_t"] <= tau]), voy[voy["berth_t"] <= tau], g, fit)
        bad = [k for k in F.SCHED_FEATURES if not np.isclose(full[k][0], part[k][0])]
        assert not bad, f"τ={tau}: {bad}"
