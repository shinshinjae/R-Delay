"""자료 점검(단계 0): realdata/reports/data_audit.md, realdata/results/audit.json, local_only/data_audit_detail.md.

산출은 모두 로컬 전용이다(공개 저장소에 올리지 않음: 사용자 결정 2026-11-07). 그래도 보고서(data_audit.md)는
익명 집계 수준으로 적는다: 부산신항 전체 수준으로만 적고, 선박 이름·호출부호는 넣지 않고, 터미널(부두)은
'T1~T8'로 익명화한다(대응표는 local_only/terminal_key.json). Clarksons는 local_only/clarksons_audit.md에만 적는다.
실행: python -m realdata.audit [--datakit <라벨을 만든 data-kit 폴더>]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .io import SPECS, clarksons_files, load, read_platform_zip, sniff_encoding, source_files
from .paths import LOCAL_ONLY, REPO, REPORTS, RESULTS, public_dir

NEWPORT = (35.08, 128.83)  # 부산신항(위도, 경도)
THRESHOLDS = (6, 12, 24)
CAPS = (None, 336, 168, 72)
PERIODS = {"학습": ("2023-01-01", "2025-01-01"), "검증": ("2025-01-01", "2025-07-01"), "시험": ("2025-07-01", "2100-01-01")}
NEWPORT_BERTH = r"^신항 ?(?:\d부두|다목적부두)"  # 라벨을 만든 data-kit(label_config.json)과 같은 패턴


def pct(x, d=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(100 * float(x), d)


def md_table(rows: list[list], head: list[str]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return out


# ---------- 터미널 익명화 ----------
def terminal_of(fac: str) -> str | None:
    m = re.match(r"^신항 ?(\d부두|다목적부두)", str(fac))
    return None if not m else m.group(1)


def terminal_key(names) -> dict[str, str]:
    """'1부두'… → 'T1'… 순서는 이름의 해시로 섞는다(부두 번호가 그대로 드러나지 않게)."""
    groups = sorted({g for g in (terminal_of(n) for n in names) if g}, key=lambda g: hashlib.sha1(("rdelay:" + g).encode()).hexdigest())
    return {g: f"T{i + 1}" for i, g in enumerate(groups)}


def gt_band(g) -> str:
    """총톤수를 구간으로(보고서에서 배를 알아볼 수 없게)."""
    if g is None or pd.isna(g):
        return "모름"
    for hi, lab in ((5000, "5천 미만"), (10000, "5천~1만"), (30000, "1만~3만"), (100000, "3만~10만")):
        if g < hi:
            return lab
    return "10만 이상"


def arrival_area(fac) -> str:
    f = str(fac)
    return "신항 정박지" if re.match(r"^신항 .*정박지", f) else ("남외항 박지" if f.startswith("남외항") else ("바로 선석" if f.startswith("신항") else "기타"))


def anon_facility(fac: str, key: dict[str, str]) -> str:
    g = terminal_of(fac)
    if not g:
        return "신항 외 시설"
    m = re.search(r"(\d+)선석", str(fac))
    return f"신항 {key[g]}" + (f" 선석{m.group(1)}" if m else "")


# ---------- 1. 자료별 요약 ----------
def time_col(name):
    return {"cntl": "cntrlOpertDt_t", "vssl": "etryptDt_t", "voyages": "arrival_t", "waiting_2h": "t", "asos": "t",
            "buoy": "t", "typhoon_track": "t", "typhoon_list": "start", "road": "t", "bpa_calls": "입항일시_t"}.get(name)


def resolution(t: pd.Series) -> str:
    t = t.dropna()
    if t.empty:
        return "-"
    if (t.dt.second != 0).any():
        return "초"
    mins = t.dt.minute
    if (mins == 0).all():
        hrs = t.dt.hour
        if (hrs == 0).all():
            return "일"
        return "2시간(짝수 시각)" if (hrs % 2 == 0).all() else "1시간"
    if (mins % 10 == 0).all():
        return "10분"
    return "1분"


def dataset_summary(name: str, df: pd.DataFrame) -> dict:
    files = source_files(name)
    with open(files[0], "rb") as f:
        enc = sniff_encoding(f.read(400_000))
    tc = time_col(name)
    out = {"name": name, "note": SPECS[name].note, "files": len(files), "mb": round(sum(p.stat().st_size for p in files) / 1e6, 1),
           "encoding": enc, "rows": int(len(df)), "cols": int(df.shape[1])}
    raw_cols = [c for c in df.columns if not c.startswith("_") and not c.endswith("_t")]
    out["dup_rows"] = int(df[raw_cols].duplicated().sum())
    if tc and tc in df:
        t = df[tc]
        out.update({"start": str(t.min()), "end": str(t.max()), "time_missing": int(t.isna().sum()), "resolution": resolution(t)})
    miss = {}
    for c in raw_cols:
        s = df[c]
        m = s.isna() | (s.astype("string").str.strip() == "")
        if m.mean() > 0:
            miss[c] = round(float(m.mean()) * 100, 1)
    out["missing_pct"] = dict(sorted(miss.items(), key=lambda kv: -kv[1])[:8])
    return out


def grid_check(t: pd.Series, freq_h: int) -> dict:
    t = pd.Series(pd.to_datetime(t)).dropna().sort_values()
    aligned = bool(((t.dt.minute == 0) & (t.dt.second == 0) & (t.dt.hour % freq_h == 0)).all())
    full = pd.date_range(t.min(), t.max(), freq=f"{freq_h}h")
    present = pd.Index(t.unique())
    missing = full.difference(present)
    return {"aligned": aligned, "expected": int(len(full)), "present": int(len(present)), "missing": int(len(missing)),
            "dups": int(t.duplicated().sum())}


# ---------- 2. 라벨 ----------
def import_datakit(folder: Path):
    """data-kit의 build_wait_labels.build를 불러온다(common.py가 같은 폴더에 있어야 함)."""
    os.environ.setdefault("RDELAY_DATA_DIR", str(public_dir()))
    sys.path.insert(0, str(folder))
    try:
        for m in ("common", "build_wait_labels"):
            sys.modules.pop(m, None)
        spec = importlib.util.spec_from_file_location(f"bwl_{abs(hash(str(folder)))}", folder / "build_wait_labels.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = json.loads((folder / "label_config.json").read_text(encoding="utf-8"))
        return mod, cfg
    finally:
        sys.path.remove(str(folder))


def cntl_for_datakit(cntl: pd.DataFrame) -> pd.DataFrame:
    """data-kit load()와 같은 모양: 원본 열(문자열)만, 't' = 관제일시(분), 정확히 같은 행은 하나로."""
    cols = [c for c in cntl.columns if not c.startswith("_") and not c.endswith("_t")] + ["_vsslRow"]
    df = cntl[[c for c in dict.fromkeys(cols) if c in cntl]].copy().fillna("")
    for col in ("clsgn", "etryptYear", "etryptCo", "cntrlNm", "cntrlSe", "fcltyNm", "vsslNm", "vsslKndNm", "vsslGrtg", "cntrlOpertDt"):
        if col not in df:
            df[col] = ""
    digits = df["cntrlOpertDt"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    df["t"] = pd.to_datetime(digits.str.slice(0, 12).str.pad(12, side="right", fillchar="0"), format="%Y%m%d%H%M", errors="coerce")
    return df.dropna(subset=["t"]).drop_duplicates()


def reproduce_labels(cntl: pd.DataFrame, voy: pd.DataFrame, folders: dict[str, Path]) -> dict:
    df = cntl_for_datakit(cntl)
    out = {}
    key = ["clsgn", "etryptYear", "etryptCo"]
    for tag, folder in folders.items():
        if not (folder / "build_wait_labels.py").exists():
            out[tag] = {"error": f"{folder}에 build_wait_labels.py 없음"}
            continue
        t0 = time.time()
        mod, cfg = import_datakit(folder)
        v = mod.build(cfg, df)
        m = v.merge(voy, on=key, how="outer", suffixes=("", "_given"), indicator=True)
        both = m[m["_merge"] == "both"]
        same_wait = (both["wait_h"].round(2) == both["wait_h_given"].round(2)).mean() if len(both) else None
        out[tag] = {"rows": int(len(v)), "gt12": int(v["wait_gt_threshold"].sum()),
                    "excluded_other_berth_first": v.attrs.get("excluded_other_berth_first"),
                    "only_rebuilt": int((m["_merge"] == "left_only").sum()), "only_given": int((m["_merge"] == "right_only").sum()),
                    "same_wait_pct": pct(same_wait, 2), "seconds": round(time.time() - t0, 1)}
    return out


def label_audit(voy: pd.DataFrame, key: dict[str, str]) -> tuple[dict, list[str], list[str]]:
    md, local = [], []
    w = voy["wait_h"]
    res = {"n": int(len(voy)), "gt12": int((w > 12).sum()), "kinds": voy["wait_kind"].value_counts().to_dict()}
    q = w[w > 0].quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]).round(1).to_dict()
    res["wait_pos_quantiles"] = {str(k): v for k, v in q.items()}
    bins = [-0.01, 0, 2, 6, 12, 24, 48, 72, 168, 336, 720, np.inf]
    labels = ["0", "0~2", "2~6", "6~12", "12~24", "24~48", "48~72", "72~168", "168~336", "336~720", "720+"]
    hist = pd.cut(w, bins=bins, labels=labels).value_counts().reindex(labels).fillna(0).astype(int)
    res["wait_hist"] = hist.to_dict()
    res["max_wait_h"] = float(w.max())

    # 선석·도착 시설
    bf = voy["berth_facility"].value_counts()
    res["berth_facilities"] = int(bf.size)
    res["berth_non_newport"] = int(sum(n for f, n in bf.items() if not re.match(NEWPORT_BERTH, str(f))))
    north = r"감천|감만|신선대|자성대|신감만|북항|우암|양곡|영도|중앙|다대포"
    res["berth_north_or_gamcheon"] = int(sum(n for f, n in bf.items() if re.search(north, str(f))))
    af = voy.loc[voy["wait_kind"] != "바로 접안", "arrival_facility"].value_counts()
    res["anchorage_facilities"] = af.to_dict()  # 정박지 이름은 공개 시설명(터미널 아님)
    by_term = voy.assign(term=voy["berth_facility"].map(lambda f: key.get(terminal_of(f) or "", "기타"))).groupby("term").agg(
        n=("wait_h", "size"), gt12=("wait_gt_threshold", "sum"))
    res["by_terminal_anon"] = {k: {"n": int(r.n), "gt12": int(r.gt12)} for k, r in by_term.iterrows()}

    # 극단값 상위 30: 보고서용은 배를 알아볼 수 없게 거칠게(총톤수 구간·도착 수역·도착 월, 선석·터미널 없음), 자세한 표는 local_only
    top = voy.nlargest(30, "wait_h")
    rows = [[i + 1, int(round(r.wait_h)), gt_band(r.vsslGrtg), arrival_area(r.arrival_facility), str(r.arrival_t)[:7]]
            for i, r in enumerate(top.itertuples())]
    res["top30"] = [dict(zip(["rank", "wait_h", "grt_band", "arrival_area", "arrival_month"], r)) for r in rows]
    local_rows = [[i + 1, round(r.wait_h, 1), r.vsslKndNm, int(r.vsslGrtg) if pd.notna(r.vsslGrtg) else None, r.arrival_facility,
                   r.berth_facility, str(r.arrival_t)] for i, r in enumerate(top.itertuples())]

    # 연·월별 12시간 초과율
    ym = voy.assign(ym=voy["arrival_t"].dt.to_period("M")).groupby("ym").agg(n=("wait_h", "size"), gt12=("wait_gt_threshold", "sum"),
                                                                            waited=("wait_h", lambda s: int((s > 0).sum())))
    ym["rate"] = ym["gt12"] / ym["n"]
    res["monthly"] = {str(k): {"n": int(r.n), "gt12": int(r.gt12), "waited": int(r.waited), "rate_pct": pct(r.rate, 2)} for k, r in ym.iterrows()}
    yr = voy.groupby(voy["arrival_t"].dt.year).agg(n=("wait_h", "size"), gt12=("wait_gt_threshold", "sum"))
    res["yearly"] = {int(k): {"n": int(r.n), "gt12": int(r.gt12), "rate_pct": pct(r.gt12 / r.n, 2)} for k, r in yr.iterrows()}

    # 상한 후보: 긴 대기의 성격
    long = voy[voy["wait_h"] > 168]
    res["long_168"] = {"n": int(len(long)), "kinds": long["vsslKndNm"].value_counts().to_dict(),
                       "grt_median": float(long["vsslGrtg"].median()) if len(long) else None,
                       "grt_median_all_waited": float(voy.loc[voy["wait_h"] > 12, "vsslGrtg"].median())}
    for cap in (72, 168, 336, 720):
        res[f"n_gt_{cap}"] = int((voy["wait_h"] > cap).sum())
    return res, md, local_rows


def system_target(voy: pd.DataFrame, grid: pd.DatetimeIndex, thr: float, cap: float | None, horizon_h: int = 24) -> pd.Series:
    """T1: (τ, τ+horizon]에 도착한 신항 컨테이너선 중 대기 > thr(시간)이 하나라도 있으면 1. cap을 넘는 대기는 뺀다."""
    w = voy["wait_h"]
    ok = (w > thr) & ((w <= cap) if cap else True)
    arr = np.sort(voy.loc[ok, "arrival_t"].to_numpy())
    g = grid.to_numpy()
    lo = np.searchsorted(arr, g, side="right")  # arrival > τ
    hi = np.searchsorted(arr, g + np.timedelta64(horizon_h, "h"), side="right")  # arrival <= τ+h
    return pd.Series((hi - lo) > 0, index=grid)


def episodes(voy: pd.DataFrame, thr: float, cap: float | None, gap_h: int = 48) -> pd.DataFrame:
    """T3: 대기 > thr 선박의 도착이 gap_h 안에 이어지면 한 에피소드."""
    w = voy["wait_h"]
    ok = (w > thr) & ((w <= cap) if cap else True)
    a = voy.loc[ok, "arrival_t"].sort_values().reset_index(drop=True)
    if a.empty:
        return pd.DataFrame(columns=["start", "end", "ships"])
    new = a.diff() > pd.Timedelta(hours=gap_h)
    ep = new.cumsum()
    return a.groupby(ep).agg(["min", "max", "size"]).rename(columns={"min": "start", "max": "end", "size": "ships"}).reset_index(drop=True)


def sensitivity(voy: pd.DataFrame, data_end: pd.Timestamp) -> dict:
    grid = pd.date_range("2023-01-01", data_end.floor("2h") - pd.Timedelta(hours=24), freq="2h")
    out = {"grid": [str(grid[0]), str(grid[-1]), int(len(grid))], "rows": []}
    for thr in THRESHOLDS:
        for cap in CAPS:
            y = system_target(voy, grid, thr, cap)
            ep = episodes(voy, thr, cap)
            row = {"thr": thr, "cap": cap, "ships": int(((voy["wait_h"] > thr) & ((voy["wait_h"] <= cap) if cap else True)).sum()),
                   "ship_rate_pct": pct(((voy["wait_h"] > thr) & ((voy["wait_h"] <= cap) if cap else True)).mean(), 2),
                   "T1_rate_pct": pct(y.mean(), 1), "episodes": int(len(ep))}
            for pname, (a, b) in PERIODS.items():
                sel = (grid >= a) & (grid < b)
                row[f"T1_{pname}_pct"] = pct(y[sel].mean(), 1) if sel.any() else None
                row[f"ep_{pname}"] = int(((ep["start"] >= a) & (ep["start"] < b)).sum())
            out["rows"].append(row)
    return out


# ---------- 3. 이안·선석 체류 ----------
BERTH_ON = ("입항", "접안")   # 선석에 바로 들어오면 '입항', 정박지·다른 선석에서 옮겨 오면 '접안'
BERTH_OFF = ("이안", "출항")  # 다른 선석으로 옮기면 '이안', 마지막 선석을 떠나면 '출항'


def berth_stays(cntl: pd.DataFrame, voy: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """신항 선석 체류 구간(컨테이너선): 같은 항차·같은 시설에서 (입항|접안) 다음 첫 (이안|출항)을 짝짓는다."""
    c = cntl[cntl["vsslKndNm"].str.contains("컨테이너", na=False) & cntl["fcltyNm"].str.match(NEWPORT_BERTH, na=False)
             & cntl["cntrlNm"].isin(BERTH_ON + BERTH_OFF)]
    c = c[["clsgn", "etryptYear", "etryptCo", "fcltyNm", "cntrlNm", "cntrlOpertDt_t"]].drop_duplicates().sort_values("cntrlOpertDt_t")
    rows = []
    for (cs, y, n, fac), g in c.groupby(["clsgn", "etryptYear", "etryptCo", "fcltyNm"], sort=False):
        on = None
        for ev, t in zip(g["cntrlNm"].to_numpy(), g["cntrlOpertDt_t"].to_numpy()):
            if ev in BERTH_ON:
                on = t if on is None else on
            elif ev in BERTH_OFF and on is not None:
                rows.append((cs, y, n, fac, on, t))
                on = None
        if on is not None:
            rows.append((cs, y, n, fac, on, pd.NaT))
    s = pd.DataFrame(rows, columns=["clsgn", "etryptYear", "etryptCo", "fcltyNm", "on", "off"])
    s["stay_h"] = (s["off"] - s["on"]).dt.total_seconds() / 3600
    key = ["clsgn", "etryptYear", "etryptCo"]
    first = voy[key + ["berth_t", "berth_facility"]].merge(
        s.rename(columns={"fcltyNm": "berth_facility", "on": "berth_t"}), on=key + ["berth_facility", "berth_t"], how="left")
    res = {"intervals": int(len(s)), "open_intervals": int(s["off"].isna().sum()),
           "voyages_with_unberth_pct": pct(first["off"].notna().mean(), 1),
           "stay_quantiles_h": {str(k): round(float(v), 1) for k, v in s["stay_h"].dropna().quantile([0.1, 0.25, 0.5, 0.75, 0.9, 0.99]).items()},
           "stay_mean_h": round(float(s["stay_h"].dropna().clip(upper=s["stay_h"].dropna().quantile(0.99)).mean()), 1),
           "shifts_per_voyage_mean": round(float(s.groupby(key).size().mean()), 2)}
    # 2시간 격자 동시 접안 척수
    grid = pd.date_range("2023-01-08", s["on"].max().floor("2h"), freq="2h")
    on = np.sort(s["on"].to_numpy())
    off = np.sort(s["off"].fillna(pd.Timestamp("2100-01-01")).to_numpy())
    g = grid.to_numpy()
    busy = np.searchsorted(on, g, side="right") - np.searchsorted(off, g, side="right")
    res["concurrent_berthed"] = {"p50": float(np.percentile(busy, 50)), "p95": float(np.percentile(busy, 95)),
                                 "p99": float(np.percentile(busy, 99)), "max": int(busy.max())}
    res["mu0_per_h"] = round(1 / res["stay_mean_h"], 4) if res["stay_mean_h"] else None
    return res, s


# ---------- 3b. 판단 시각에 알 수 있는 대기 척수 ----------
ANCHOR = r"박지|묘지|정박지"
ANCHOR_ON = ("입항", "투묘")
ANCHOR_OFF = ("양묘", "접안", "출항")


def asof_waiting(cntl: pd.DataFrame, wait2h: pd.DataFrame, voy: pd.DataFrame) -> dict:
    """관제 이벤트만으로(τ까지) 센 정박지 대기 컨테이너선 수와, 최종 라벨로 만든 n_waiting(앞으로 신항에 댈 배만 셈)을 비교한다."""
    c = cntl[cntl["vsslKndNm"].str.contains("컨테이너", na=False) & cntl["cntrlNm"].isin(ANCHOR_ON + ANCHOR_OFF)]
    c = c[["clsgn", "etryptYear", "etryptCo", "fcltyNm", "cntrlNm", "cntrlOpertDt_t"]].drop_duplicates().sort_values("cntrlOpertDt_t")
    c["anc"] = c["fcltyNm"].str.contains(ANCHOR, na=False)  # 묶음마다 Series로 찾으면 매우 느리다(pandas 3)
    rows = []
    for k, g in c.groupby(["clsgn", "etryptYear", "etryptCo"], sort=False):
        on = None
        for ev, fac, t, anc in zip(g["cntrlNm"].to_numpy(), g["fcltyNm"].to_numpy(), g["cntrlOpertDt_t"].to_numpy(), g["anc"].to_numpy()):
            if ev in ANCHOR_ON and anc and on is None:
                on = (t, fac)
            elif ev in ANCHOR_OFF and on is not None:
                rows.append((*k, on[1], on[0], t))
                on = None
        if on is not None:
            rows.append((*k, on[1], on[0], pd.NaT))
    iv = pd.DataFrame(rows, columns=["clsgn", "etryptYear", "etryptCo", "anchorage", "start", "end"])
    grid = pd.DatetimeIndex(wait2h["t"])
    res = {"intervals": int(len(iv)), "anchorages": iv["anchorage"].value_counts().head(15).to_dict()}
    key = ["clsgn", "etryptYear", "etryptCo"]
    iv["to_newport"] = iv.set_index(key).index.isin(voy.set_index(key).index)
    for tag, sel in {"newport_anchorages": iv["anchorage"].str.startswith("신항"), "all_anchorages": pd.Series(True, index=iv.index)}.items():
        s = iv[sel]
        st = np.sort(s["start"].to_numpy())
        en = np.sort(s["end"].fillna(pd.Timestamp("2100-01-01")).to_numpy())
        g = grid.to_numpy()
        n = np.searchsorted(st, g, side="right") - np.searchsorted(en, g, side="right")
        lab = wait2h["n_waiting"].to_numpy()
        res[tag] = {"mean": round(float(n.mean()), 2), "label_mean": round(float(lab.mean()), 2),
                    "corr_with_label_n_waiting": round(float(np.corrcoef(n, lab)[0, 1]), 3),
                    "share_ships_later_newport_pct": pct(s["to_newport"].mean(), 1)}
        # 정박지 범위를 고른 근거는 학습 기간만으로(PROTOCOL v1.4 ⑤)
        tr = g < np.datetime64(PERIODS["학습"][1])
        s_tr = s[s["start"] < pd.Timestamp(PERIODS["학습"][1])]
        res[tag]["train"] = {"mean": round(float(n[tr].mean()), 2), "label_mean": round(float(lab[tr].mean()), 2),
                             "corr_with_label_n_waiting": round(float(np.corrcoef(n[tr], lab[tr])[0, 1]), 3),
                             "share_ships_later_newport_pct": pct(s_tr["to_newport"].mean(), 1)}
    return res


def pretest_patterns(voy: pd.DataFrame) -> dict:
    """평가 기간 전(학습+검증, 2023-01-01 ~ 2025-06-30)의 12시간 초과 대기 모양: 하루 건수의 분산/평균(무작위 도착이면 1 근처)과
    총톤수 구간별로 '전체 도착 중 비율' 대 '12시간 초과(168시간 이하) 대기 중 비율'. REPORT·웹이 인용하는 숫자는 여기서만 나온다."""
    a, b = pd.Timestamp(PERIODS["학습"][0]), pd.Timestamp(PERIODS["검증"][1])
    v = voy[(voy["arrival_t"] >= a) & (voy["arrival_t"] < b)].copy()
    q = v[(v["wait_h"] > 12) & (v["wait_h"] <= 168)]
    days = pd.date_range(a, b - pd.Timedelta(days=1), freq="D")
    daily = q["arrival_t"].dt.floor("D").value_counts().reindex(days, fill_value=0)
    v["band"] = v["vsslGrtg"].map(gt_band)
    bands = ["5천 미만", "5천~1만", "1만~3만", "3만~10만", "10만 이상", "모름"]
    arr = v["band"].value_counts(normalize=True).reindex(bands, fill_value=0.0)
    gt = v.loc[q.index, "band"].value_counts(normalize=True).reindex(bands, fill_value=0.0)
    return {"period": [str(a.date()), str((b - pd.Timedelta(days=1)).date())], "days": int(len(days)), "voyages": int(len(v)),
            "gt12_capped": int(len(q)), "daily_mean": round(float(daily.mean()), 3), "daily_var": round(float(daily.var(ddof=0)), 3),
            "daily_var_over_mean": round(float(daily.var(ddof=0) / daily.mean()), 3),
            "grt_bands": {k: {"arrivals_pct": pct(arr[k], 1), "gt12_pct": pct(gt[k], 1)} for k in bands if arr[k] > 0 or gt[k] > 0}}


# ---------- 4. 선박운항정보의 예정·신고 시각 ----------
def vssl_audit(vssl: pd.DataFrame, voy: pd.DataFrame) -> dict:
    res = {"columns": [c for c in vssl.columns if not c.startswith("_") and not c.endswith("_t")],
           "reqstSeNm": vssl["reqstSeNm"].value_counts().to_dict(), "etryndNm": vssl["etryndNm"].value_counts().to_dict()}
    res["has_report_time_col"] = any(re.search(r"신고|reqst.*Dt|rptDt|regDt|crtDt", c, re.I) for c in res["columns"] if c.endswith("Dt"))
    arr = vssl[vssl["etryndNm"] == "입항"].copy()
    key = ["clsgn", "etryptYear", "etryptCo"]
    m = voy[key + ["arrival_t"]].merge(arr[key + ["etryptDt_t", "reqstSeNm", "_day"]], on=key, how="left")
    res["voyages_matched_pct"] = pct(m.groupby(key)["etryptDt_t"].apply(lambda s: s.notna().any()).mean(), 1)
    d = (m["etryptDt_t"] - m["arrival_t"]).dt.total_seconds() / 3600
    res["etryptDt_minus_arrival_h"] = {str(k): round(float(v), 2) for k, v in d.dropna().quantile([0.05, 0.25, 0.5, 0.75, 0.95]).items()}
    res["etryptDt_midnight_pct"] = pct(((arr["etryptDt_t"].dt.hour == 0) & (arr["etryptDt_t"].dt.minute == 0)).mean(), 1)
    day = pd.to_datetime(arr["_day"], format="%Y%m%d", errors="coerce")
    res["file_day_vs_etryptDt_date_same_pct"] = pct((day.dt.date == arr["etryptDt_t"].dt.date).mean(), 1)
    return res


# ---------- 5. BPA 입출항 집계와 비교(2023~2024) ----------
def bpa_compare(bpa: pd.DataFrame, voy: pd.DataFrame, stays: pd.DataFrame) -> dict:
    b = bpa.copy()
    b["year"] = pd.to_numeric(b["입항연도"], errors="coerce")
    b["co"] = pd.to_numeric(b["입항회수"], errors="coerce")
    v = voy.copy()
    v["year"] = pd.to_numeric(v["etryptYear"], errors="coerce")
    v["co"] = pd.to_numeric(v["etryptCo"], errors="coerce")
    # BPA 선박키는 IMO·호출부호·국내 번호가 섞여 있어 (선박명, 입항 시각)으로 붙인다. 호출부호로 붙는 비율도 함께 적는다.
    by_key = v.merge(b[["선박키", "year", "co"]].drop_duplicates(), left_on=["clsgn", "year", "co"], right_on=["선박키", "year", "co"], how="left")
    norm = lambda s: s.astype("string").str.upper().str.replace(r"[^0-9A-Z가-힣]", "", regex=True)  # noqa: E731
    v["_nm"], b["_nm"] = norm(v["vsslNm"]), norm(b["선박명"])
    b1 = b.dropna(subset=["입항일시_t"]).drop_duplicates(subset=["_nm", "입항일시_t"])
    m = v.merge(b1, left_on=["_nm", "arrival_t"], right_on=["_nm", "입항일시_t"], how="left", suffixes=("", "_bpa"))
    v2324 = v[v["arrival_t"] < "2025-01-01"]
    mm = m[m["arrival_t"] < "2025-01-01"]
    res = {"bpa_rows": int(len(b)), "bpa_range": [str(b["입항일시_t"].min()), str(b["입항일시_t"].max())],
           "bpa_bad_dates": int((b["입항일시_t"] < "2000-01-01").sum()),
           "voyages_2023_24": int(len(v2324)), "matched_by_callsign_pct": pct(by_key.loc[by_key["arrival_t"] < "2025-01-01", "선박키"].notna().mean(), 1),
           "matched_pct": pct(mm["입항일시_t"].notna().mean(), 1)}
    d = (mm["입항일시_t"] - mm["arrival_t"]).dt.total_seconds() / 3600
    res["bpa_arrival_minus_label_h"] = {str(k): round(float(x), 2) for k, x in d.dropna().quantile([0.05, 0.25, 0.5, 0.75, 0.95]).items()}
    res["within_1h_pct"] = pct((d.abs() <= 1).mean() if d.notna().any() else None, 1)
    # 월별 추이: 우리 항차 수·12h 초과율 대 BPA 컨테이너선 입항 수(부산항 전체)·체류시간
    bc = b[b["선박종류명"].str.contains("컨테이너", na=False) & (b["입항일시_t"] >= "2023-01-01") & (b["입항일시_t"] < "2025-01-01")]
    bm = bc.groupby(bc["입항일시_t"].dt.to_period("M")).agg(bpa_calls=("선박키", "size"))
    bm["bpa_stay_h_median"] = bc.assign(st=(bc["출항일시_t"] - bc["입항일시_t"]).dt.total_seconds() / 3600).groupby(
        bc["입항일시_t"].dt.to_period("M"))["st"].median()
    vm = v2324.groupby(v2324["arrival_t"].dt.to_period("M")).agg(ours=("wait_h", "size"), gt12=("wait_gt_threshold", "sum"),
                                                                wait_mean=("wait_h", "mean"))
    mt = mm.assign(st=(mm["출항일시_t"] - mm["입항일시_t"]).dt.total_seconds() / 3600)
    vm["matched_bpa_stay_h_median"] = mt.groupby(mt["arrival_t"].dt.to_period("M"))["st"].median()
    j = vm.join(bm, how="outer")
    res["monthly"] = {str(k): {kk: (None if pd.isna(vv) else round(float(vv), 2)) for kk, vv in r.items()} for k, r in j.iterrows()}
    ok = j.dropna(subset=["ours", "bpa_calls"])
    res["corr_monthly_counts"] = round(float(ok["ours"].corr(ok["bpa_calls"])), 3) if len(ok) > 2 else None
    ok2 = j.dropna(subset=["wait_mean", "bpa_stay_h_median"])
    res["corr_wait_vs_bpa_stay"] = round(float(ok2["wait_mean"].corr(ok2["bpa_stay_h_median"])), 3) if len(ok2) > 2 else None
    return res


# ---------- 6. 기상·도로·태풍 ----------
def weather_audit(asos: pd.DataFrame, buoy: pd.DataFrame) -> dict:
    res = {"asos": {}, "buoy": {}}
    for stn, g in asos.groupby("stn"):
        gc = grid_check(g["t"], 1)
        res["asos"][str(stn)] = {"name": g["stn_name"].iloc[0], "start": str(g["t"].min()), "end": str(g["t"].max()),
                                 "hours_missing": gc["missing"], "wind_missing_pct": pct(g["wind"].isna().mean(), 2)}
    for stn, g in buoy.groupby("stn"):
        gc = grid_check(g["t"], 1)
        res["buoy"][str(stn)] = {"start": str(g["t"].min()), "end": str(g["t"].max()), "hours_missing": gc["missing"],
                                 "wave_sig_missing_pct": pct(g["wave_sig"].isna().mean(), 2), "wind_missing_pct": pct(g["wind"].isna().mean(), 2)}
    w = asos.pivot_table(index="t", columns="stn", values="wind")
    bw = buoy.pivot_table(index="t", columns="stn", values="wind")
    both = w.join(bw, how="inner", rsuffix="_b")
    corr = {}
    if "159" in map(str, w.columns):
        base = both[[c for c in both.columns if str(c) == "159"][0]]
        for c in both.columns:
            if str(c) != "159":
                corr[str(c)] = round(float(base.corr(both[c])), 3)
    res["wind_corr_with_busan159"] = corr
    return res


def road_audit(road: pd.DataFrame) -> dict:
    r = road.copy()
    r["zero"] = r["speed"] <= 0
    z = r.groupby("zone_id").agg(name=("zone_name", lambda s: " / ".join(sorted(set(s)))), rows=("speed", "size"), zero=("zero", "mean"))
    res = {"zone_ids": int(r["zone_id"].nunique()), "zone_names": int(r["zone_name"].nunique()),
           "zero_speed_pct": pct(r["zero"].mean(), 1), "start": str(r["t"].min()), "end": str(r["t"].max()),
           "zones_mostly_zero": int((z["zero"] > 0.9).sum()), "zones_usable": int((z["zero"] < 0.2).sum()),
           "zones_multi_name": int(z["name"].str.contains(" / ").sum())}
    hours = pd.date_range(r["t"].min(), r["t"].max(), freq="1h")
    per_hour = r[~r["zero"]].groupby("t")["zone_id"].nunique().reindex(hours, fill_value=0)
    res["hours_no_valid_zone"] = int((per_hour == 0).sum())
    res["valid_zones_per_hour_median"] = float(per_hour.median())
    month_zero = r.groupby(r["t"].dt.to_period("M"))["zero"].mean()
    res["zero_pct_by_month_minmax"] = [pct(month_zero.min(), 1), pct(month_zero.max(), 1)]
    res["zone_table"] = [{"zone_id": k, "name": v["name"], "rows": int(v["rows"]), "zero_pct": pct(v["zero"], 1)} for k, v in z.sort_values("zero").iterrows()]
    return res


def haversine_km(lat, lon, lat0=NEWPORT[0], lon0=NEWPORT[1]):
    lat, lon = np.radians(lat), np.radians(lon)
    a = np.sin((lat - np.radians(lat0)) / 2) ** 2 + np.cos(lat) * np.cos(np.radians(lat0)) * np.sin((lon - np.radians(lon0)) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def typhoon_audit(track: pd.DataFrame, tlist: pd.DataFrame) -> dict:
    tr = track.copy()
    tr["km"] = haversine_km(tr["lat"], tr["lon"])
    near = tr[tr["km"] <= 500]
    ev = near.groupby(["year", "num"]).agg(start=("t", "min"), end=("t", "max"), min_km=("km", "min")).reset_index()
    names = tlist.set_index(["year", "num"])["name"].to_dict()
    ev["name"] = [names.get((int(y), int(n))) for y, n in zip(ev["year"], ev["num"])]
    return {"track_points": int(len(tr)), "storms_in_track": int(tr.groupby(["year", "num"]).ngroups),
            "track_range": [str(tr["t"].min()), str(tr["t"].max())], "list_rows": int(len(tlist)),
            "list_years": [int(tlist["year"].min()), int(tlist["year"].max())],
            "within_500km": [{"year": int(r.year), "num": int(r.num), "name": r.name, "start": str(r.start), "end": str(r.end),
                              "min_km": round(float(r.min_km))} for r in ev.itertuples()]}


def platform_audit() -> dict:
    out = {}
    for p in sorted(public_dir().glob("manual/platform_*/*.zip")):
        for member, df in read_platform_zip(p).items():
            dates = []
            for c in df.columns:
                if re.search(r"일시|일자", c):
                    d = pd.to_datetime(df[c].str.slice(0, 10), errors="coerce")
                    d = d[(d > "2000-01-01") & (d < "2030-01-01")]
                    if len(d):
                        dates += [d.min(), d.max()]
            port_col = next((c for c in df.columns if c in ("항만명", "항만코드", "선박일정항만코드")), None)
            out[f"{p.parent.name}/{member}"] = {"rows": int(len(df)), "cols": int(df.shape[1]),
                                               "date_range": [str(min(dates))[:10], str(max(dates))[:10]] if dates else None,
                                               "busan_rows": None if not port_col else int(df[port_col].astype(str).str.contains("부산|KRPUS").sum())}
    return out


# ---------- 7. Clarksons(local_only) ----------
def clarksons_audit() -> list[str]:
    lines = ["# Clarksons 자료 점검 (local_only — 외부 공개 금지)", "",
             "이 파일과 수치는 저장소·웹·README·REPORT에 넣지 않는다. 발표 사용 여부는 사용자가 정한다.", ""]
    rows = []
    for p in clarksons_files():
        info = ""
        if p.suffix == ".xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(p, read_only=True)
                info = "; ".join(f"{ws.title}({ws.max_row}×{ws.max_column})" for ws in wb.worksheets[:4])
            except Exception as e:  # noqa: BLE001
                info = f"열지 못함: {e}"
        elif p.suffix == ".csv":
            info = f"{sum(1 for _ in open(p, encoding='utf-8-sig', errors='replace')) - 1}행"
        rows.append([p.parent.name, p.name, f"{p.stat().st_size / 1e3:.0f}KB", info])
    lines += md_table(rows, ["폴더", "파일", "크기", "시트(행×열)"])
    return lines


# ---------- 보고서 ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datakit", default=os.environ.get("RDELAY_DATAKIT_DIR", ""),
                    help="라벨을 만든 data-kit 폴더(재현 확인용). 저장소 data-kit과 함께 비교한다")
    a = ap.parse_args()
    t0 = time.time()
    REPORTS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    LOCAL_ONLY.mkdir(parents=True, exist_ok=True)
    data = {n: load(n) for n in SPECS}
    voy, cntl = data["voyages"], data["cntl"]
    key = terminal_key(pd.concat([voy["berth_facility"], cntl["fcltyNm"]]).unique())
    (LOCAL_ONLY / "terminal_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")

    res = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "data_root_public": "<RDELAY_DATA_ROOT>/public_data", "datasets": {}}
    for n, df in data.items():
        res["datasets"][n] = dataset_summary(n, df)
    res["grid"] = {"waiting_2h": grid_check(data["waiting_2h"]["t"], 2)}
    data_end = cntl["cntrlOpertDt_t"].max()
    res["label_data_end"] = str(data_end)
    lab, _, local_top = label_audit(voy, key)
    by_term = lab.pop("by_terminal_anon")  # 공개 처리량 통계와 맞대면 익명이 풀릴 수 있어 local_only에만
    lab["terminals_anon"] = len([k for k in by_term if k != "기타"])
    res["labels"] = lab
    res["sensitivity"] = sensitivity(voy, voy["arrival_t"].max())
    folders = {"repo_datakit": REPO / "data-kit"}
    if a.datakit:
        folders["used_datakit"] = Path(a.datakit).expanduser()
    res["label_reproduction"] = reproduce_labels(cntl, voy, folders)
    res["berth"], stays = berth_stays(cntl, voy)
    res["asof_waiting"] = asof_waiting(cntl, data["waiting_2h"], voy)
    res["pretest_patterns"] = pretest_patterns(voy)
    res["vssl"] = vssl_audit(data["vssl"], voy)
    res["bpa"] = bpa_compare(data["bpa_calls"], voy, stays)
    res["weather"] = weather_audit(data["asos"], data["buoy"])
    road = road_audit(data["road"])
    res["road"] = {k: v for k, v in road.items() if k != "zone_table"}
    res["typhoon"] = typhoon_audit(data["typhoon_track"], data["typhoon_list"])
    res["platform"] = platform_audit()
    # 최신 태풍목록(데이터 루트에 따로 받은 파일)과 행 수 비교
    extra = sorted(public_dir().parent.parent.glob("FCT_태풍목록_*.csv"))
    if extra:
        from .io import read_typhoon_list
        res["typhoon"]["newer_list_file"] = {"file": extra[-1].name, "rows": int(len(read_typhoon_list([extra[-1]])))}
    res["elapsed_s"] = round(time.time() - t0, 1)
    (RESULTS / "audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # 보고서(로컬 전용, 익명 집계 수준)
    write_report(res, road)
    # local_only 상세(시설 이름 포함, 선박 이름·호출부호는 넣지 않음)
    det = ["# 자료 점검 상세 (local_only)", "", "터미널 익명화 대응표: local_only/terminal_key.json", "",
           "## 대기 극단값 상위 30(실제 시설명, 선박 이름 없음)", ""]
    det += md_table(local_top, ["순위", "대기(h)", "선종", "총톤수", "도착 시설", "선석 시설", "도착 시각"])
    det += ["", "## 터미널별(익명 T1~T8) 항차·12시간 초과", ""]
    det += md_table([[k, v["n"], v["gt12"]] for k, v in sorted(by_term.items())], ["터미널", "항차", "12h 초과"])
    det += ["", "## 라벨의 선석 시설(실명)", ""]
    det += md_table([[f, n] for f, n in voy["berth_facility"].value_counts().items()], ["선석 시설", "항차"])
    det += ["", "## 도로 콘존 표", ""]
    det += md_table([[z["zone_id"], z["name"], z["rows"], z["zero_pct"]] for z in road["zone_table"]], ["콘존ID", "콘존명", "행", "속도 0 비율(%)"])
    (LOCAL_ONLY / "data_audit_detail.md").write_text("\n".join(det) + "\n", encoding="utf-8")
    (LOCAL_ONLY / "clarksons_audit.md").write_text("\n".join(clarksons_audit()) + "\n", encoding="utf-8")
    print(f"→ {REPORTS / 'data_audit.md'}\n→ {RESULTS / 'audit.json'}\n→ {LOCAL_ONLY / 'data_audit_detail.md'}\n→ {LOCAL_ONLY / 'clarksons_audit.md'}"
          f"\n({res['elapsed_s']}초)")


def write_report(res: dict, road: dict) -> None:
    L = ["# 실자료 점검 보고서 (단계 0)", "",
         f"만든 시각 {res['generated']} · `python -m realdata.audit`가 `realdata/results/audit.json`과 함께 만든다.",
         "로컬 전용이다(공개 저장소에 올리지 않음). 그래도 익명 집계 수준으로 적는다: "
         "부산신항 전체 수준으로만 적고, 선박 이름·호출부호·터미널별 수치는 넣지 않는다. "
         "Clarksons 점검은 `local_only/`에만 있다.", ""]
    L += ["## 1. 자료별 요약", ""]
    rows = []
    for n, d in res["datasets"].items():
        miss = ", ".join(f"{k} {v}%" for k, v in list(d["missing_pct"].items())[:3]) or "-"
        rows.append([n, d["note"], d["files"], d["mb"], d["encoding"], f"{d['rows']:,}", d.get("start", "-")[:16], d.get("end", "-")[:16],
                     d.get("resolution", "-"), d["dup_rows"], miss])
    L += md_table(rows, ["자료", "내용", "파일", "MB", "인코딩", "행", "시작", "끝", "시각 단위", "중복 행", "결측 많은 열(상위 3)"])
    g = res["grid"]["waiting_2h"]
    L += ["", f"- 2시간 대기 척수: 짝수 시각 정렬 {'예' if g['aligned'] else '아니오'}, 칸 {g['present']:,}/{g['expected']:,}(빠진 칸 {g['missing']})."]
    L += [f"- 관제정보 마지막 이벤트 {res['label_data_end']} (파일은 2026-11-04까지)."]

    lab = res["labels"]
    L += ["", "## 2. 정답 라벨(신항 컨테이너선 대기)", "",
          f"- 항차 {lab['n']:,} · 12시간 초과 {lab['gt12']:,}({pct(lab['gt12'] / lab['n'], 2)}%) · 판정 종류 {lab['kinds']}",
          f"- 대기가 있는 항차(정박지→선석)의 대기시간 분위수(시간): {lab['wait_pos_quantiles']} · 최대 {round(lab['max_wait_h'], 1)}",
          f"- 선석 시설 {lab['berth_facilities']}곳, 신항 패턴 밖 {lab['berth_non_newport']}건, 북항·감천 계열 이름 {lab['berth_north_or_gamcheon']}건",
          f"- 대기 정박지(도착 시설): {lab['anchorage_facilities']}"]
    L += ["", "대기시간 분포", ""]
    L += md_table([[k, v] for k, v in lab["wait_hist"].items()], ["대기(시간)", "항차"])
    L += ["", f"터미널 {lab['terminals_anon']}곳의 항차·12시간 초과 표는 이 보고서에 넣지 않는다(공개 처리량 통계와 맞대면 익명 표기가 풀릴 수 있어 "
          "`local_only/`에만 둔다).", ""]
    L += ["대기 극단값 상위 30(배를 알아볼 수 없게 총톤수 구간·도착 수역·도착 월만, 선석·터미널 없음)", ""]
    L += md_table([[r["rank"], r["wait_h"], r["grt_band"], r["arrival_area"], r["arrival_month"]] for r in lab["top30"]],
                  ["순위", "대기(h)", "총톤수 구간", "도착 수역", "도착 월"])
    L += ["", "연도별 12시간 초과", ""]
    L += md_table([[y, v["n"], v["gt12"], v["rate_pct"]] for y, v in lab["yearly"].items()], ["연도", "항차", "12h 초과", "비율(%)"])
    L += ["", "월별 12시간 초과율(%) — 행 연도, 열 월", ""]
    mon = lab["monthly"]
    years = sorted({k[:4] for k in mon})
    L += md_table([[y] + [mon.get(f"{y}-{m:02d}", {}).get("rate_pct") for m in range(1, 13)] for y in years],
                  ["연도"] + [f"{m}월" for m in range(1, 13)])
    L += ["", "월별 12시간 초과 척수", ""]
    L += md_table([[y] + [mon.get(f"{y}-{m:02d}", {}).get("gt12") for m in range(1, 13)] for y in years],
                  ["연도"] + [f"{m}월" for m in range(1, 13)])
    lg = lab["long_168"]
    L += ["", f"- 168시간 초과 {lg['n']}건: 선종 {lg['kinds']}, 총톤수 중앙값 {lg['grt_median']} (12h 초과 전체 중앙값 {lg['grt_median_all_waited']})",
          f"- 대기 초과 건수: 72h {lab['n_gt_72']}, 168h {lab['n_gt_168']}, 336h {lab['n_gt_336']}, 720h {lab['n_gt_720']}"]

    s = res["sensitivity"]
    L += ["", "### 기준·상한 민감도", "",
          f"T1 = (τ, τ+24h]에 도착한 신항 컨테이너선 중 대기 > 기준이 1척 이상. τ는 2시간 격자 {s['grid'][0]} ~ {s['grid'][1]}({s['grid'][2]:,}칸). "
          "상한을 넘는 대기는 양성에서 뺀다. 에피소드 = 초과 선박 도착이 48시간 안에 이어지는 묶음.", ""]
    L += md_table([[r["thr"], r["cap"] or "없음", r["ships"], r["ship_rate_pct"], r["T1_rate_pct"], r["T1_학습_pct"], r["T1_검증_pct"], r["T1_시험_pct"],
                    r["episodes"], r["ep_학습"], r["ep_검증"], r["ep_시험"]] for r in s["rows"]],
                  ["기준(h)", "상한(h)", "선박", "선박 비율(%)", "T1 양성(%)", "학습", "검증", "평가", "에피소드", "학습", "검증", "평가"])

    rep = res["label_reproduction"]
    L += ["", "### 라벨 재현(관제정보 → 라벨)", ""]
    L += md_table([[k, v.get("rows"), v.get("gt12"), v.get("excluded_other_berth_first"), v.get("only_rebuilt"), v.get("only_given"), v.get("same_wait_pct")]
                   for k, v in rep.items()], ["스크립트", "항차", "12h 초과", "다른 선석 먼저(제외)", "재현에만", "주어진 라벨에만", "대기 일치(%)"])

    b = res["berth"]
    L += ["", "## 3. 이안(출항) 시각과 선석 체류", "",
          f"- 신항 컨테이너선 접안→이안 구간 {b['intervals']:,}개(이안이 아직 없는 구간 {b['open_intervals']}), 라벨 항차 중 첫 선석의 이안을 찾은 비율 {b['voyages_with_unberth_pct']}%",
          f"- 선석 체류시간 분위수(시간) {b['stay_quantiles_h']}, 평균(상위 1% 자름) {b['stay_mean_h']}시간 → μ0 ≈ {b['mu0_per_h']}/시간",
          f"- 항차당 접안 구간 수 평균 {b['shifts_per_voyage_mean']}(선석 이동 포함)",
          f"- 2시간 격자 동시 접안 척수: 중앙값 {b['concurrent_berthed']['p50']}, 95분위 {b['concurrent_berthed']['p95']}, 99분위 {b['concurrent_berthed']['p99']}, 최대 {b['concurrent_berthed']['max']}"]

    aw = res["asof_waiting"]
    L += ["", "### 판단 시각에 알 수 있는 대기 척수", "",
          "`newport_waiting_2h`의 대기 척수는 최종 라벨(나중에 신항 선석에 댄 배)로 센 값이라, 판단 시각에는 모르는 정보(어디에 댈지)가 들어 있다. "
          "관제 이벤트만으로 τ까지 센 정박지 대기 컨테이너선 수와 비교한다(입항·투묘 → 양묘·접안·출항).", ""]
    L += md_table([[k, p_, v[p_]["mean"] if p_ != "전체" else v["mean"], (v[p_] if p_ != "전체" else v)["label_mean"],
                    (v[p_] if p_ != "전체" else v)["corr_with_label_n_waiting"], (v[p_] if p_ != "전체" else v)["share_ships_later_newport_pct"]]
                   for k, v in aw.items() if isinstance(v, dict) and "mean" in v for p_ in ("전체", "train")],
                  ["정박지 범위", "기간", "τ까지 센 평균 척수", "라벨 기반 평균", "상관", "나중에 신항에 댄 배 비율(%)"])
    L += ["", f"- 정박지 구간 {aw['intervals']:,}개 · 많이 쓰인 정박지 {aw['anchorages']} · 기간 train = 학습 기간(정박지 범위를 고른 근거)"]
    pp = res["pretest_patterns"]
    L += ["", "### 12시간 초과 대기의 모양(평가 기간 전: 학습+검증)", "",
          f"- 기간 {pp['period'][0]} ~ {pp['period'][1]}({pp['days']}일), 항차 {pp['voyages']:,}, 12시간 초과(168시간 이하) {pp['gt12_capped']}건",
          f"- 하루 건수 평균 {pp['daily_mean']}, 분산 {pp['daily_var']}, 분산/평균 {pp['daily_var_over_mean']}(무작위 도착이면 1 근처)", ""]
    L += md_table([[k, v["arrivals_pct"], v["gt12_pct"]] for k, v in pp["grt_bands"].items()], ["총톤수 구간", "도착 중 비율(%)", "12시간 초과 중 비율(%)"])

    v = res["vssl"]
    L += ["", "## 4. 선박운항정보의 예정·신고 시각", "",
          f"- 신고 구분 {v['reqstSeNm']} · 입출항 {v['etryndNm']}",
          f"- 신고(제출) 시각 열: {'있음' if v['has_report_time_col'] else '없음'} — 열 목록에 신고 일시가 없다.",
          f"- 라벨 항차 중 운항정보 입항 기록이 붙는 비율 {v['voyages_matched_pct']}%, 운항정보 입항일시 − 관제 입항 시각(시간) {v['etryptDt_minus_arrival_h']}",
          f"- 입항일시가 자정(00:00)인 비율 {v['etryptDt_midnight_pct']}%, 받은 날짜 파일과 입항일 날짜가 같은 비율 {v['file_day_vs_etryptDt_date_same_pct']}%"]

    bp = res["bpa"]
    L += ["", "## 5. 부산항만공사 입출항 집계와 비교(2023~2024)", "",
          f"- BPA {bp['bpa_rows']:,}행, 입항일시 범위 {bp['bpa_range']}(2000년 이전 잘못된 날짜 {bp['bpa_bad_dates']}행)",
          f"- 라벨 항차(2023~2024) {bp['voyages_2023_24']:,} 중 (선박명·입항 시각)으로 BPA에 붙는 비율 {bp['matched_pct']}% "
          f"(BPA 선박키는 IMO·호출부호·국내 번호가 섞여 호출부호로는 {bp['matched_by_callsign_pct']}%만 붙음)",
          f"- BPA 입항일시 − 라벨 도착 시각(시간) {bp['bpa_arrival_minus_label_h']} · 1시간 안 {bp['within_1h_pct']}%",
          f"- 월별 항차 수 상관(라벨 대 BPA 부산항 컨테이너선 입항) {bp['corr_monthly_counts']} · 월 평균 대기 대 BPA 체류시간 중앙값 상관 {bp['corr_wait_vs_bpa_stay']}", ""]
    L += md_table([[k, r.get("ours"), r.get("gt12"), r.get("wait_mean"), r.get("bpa_calls"), r.get("bpa_stay_h_median"), r.get("matched_bpa_stay_h_median")]
                   for k, r in bp["monthly"].items()],
                  ["월", "라벨 항차", "12h 초과", "평균 대기(h)", "BPA 컨테이너선 입항(부산항 전체)", "BPA 체류 중앙값(h)", "라벨 항차의 BPA 체류 중앙값(h)"])

    w = res["weather"]
    L += ["", "## 6. 기상·해상", ""]
    L += md_table([[k, v["name"], v["start"][:16], v["end"][:16], v["hours_missing"], v["wind_missing_pct"]] for k, v in w["asos"].items()],
                  ["ASOS", "지점", "시작", "끝", "빠진 시간", "풍속 결측(%)"])
    L += [""]
    L += md_table([[k, v["start"][:16], v["end"][:16], v["hours_missing"], v["wave_sig_missing_pct"], v["wind_missing_pct"]] for k, v in w["buoy"].items()],
                  ["부이", "시작", "끝", "빠진 시간", "유의파고 결측(%)", "풍속 결측(%)"])
    L += ["", f"- 부산(159) 풍속과의 시간별 상관: {w['wind_corr_with_busan159']}"]

    r = res["road"]
    L += ["", "## 7. 배후 고속도로 속도", "",
          f"- 콘존 ID {r['zone_ids']}개(이름 {r['zone_names']}개, 이름이 둘 이상인 ID {r['zones_multi_name']}), 기간 {r['start'][:16]} ~ {r['end'][:16]}",
          f"- 속도 0(측정 없음으로 봄) 비율 {r['zero_speed_pct']}% · 월별 {r['zero_pct_by_month_minmax'][0]}~{r['zero_pct_by_month_minmax'][1]}%",
          f"- 거의 늘 0인 콘존(90% 초과) {r['zones_mostly_zero']}개, 쓸 만한 콘존(0이 20% 미만) {r['zones_usable']}개",
          f"- 쓸 만한 측정이 하나도 없는 시간 {r['hours_no_valid_zone']}시간, 시간당 유효 콘존 수 중앙값 {r['valid_zones_per_hour_median']}"]

    t = res["typhoon"]
    L += ["", "## 8. 태풍", "",
          f"- 경로 {t['track_points']:,}점·태풍 {t['storms_in_track']}개({t['track_range'][0][:10]}~{t['track_range'][1][:10]}), 목록 {t['list_rows']}개({t['list_years'][0]}~{t['list_years'][1]})"
          + (f", 데이터 루트의 최신 목록 {t['newer_list_file']['file']} {t['newer_list_file']['rows']}개" if t.get("newer_list_file") else ""), "",
          "신항 500km 안에 들어온 태풍", ""]
    L += md_table([[e["year"], e["num"], e["name"], e["start"][:16], e["end"][:16], e["min_km"]] for e in t["within_500km"]],
                  ["연도", "번호", "이름", "500km 진입", "500km 이탈", "최근접(km)"])

    L += ["", "## 9. 물류 플랫폼 표본", ""]
    L += md_table([[k, v["rows"], v["cols"], v["date_range"], v["busan_rows"]] for k, v in res["platform"].items()],
                  ["파일", "행", "열", "날짜 범위", "부산 행"])
    (REPORTS / "data_audit.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
