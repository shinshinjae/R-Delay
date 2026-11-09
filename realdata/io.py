"""자료별 읽기 함수와 parquet 캐시.

- 원본 파일 목록·크기·수정시각을 realdata/cache/_manifest.json에 적고, 바뀐 것만 다시 읽는다.
- PORT-MIS 일별 파일(관제·운항정보)은 월별로 나눠 캐시한다. 새 날짜 파일이 들어오면 그달만 다시 읽는다.
- 시각은 모두 한국시간이며 시간대 없는 datetime으로 통일한다(+09:00 표기는 떼고 읽는다).
- 인코딩: 공공데이터포털·기상청 CSV는 대부분 CP949, 직접 만든 파일은 UTF-8(BOM 가능) → 앞부분으로 판별한다.

실행: python -m realdata.io [--refresh] [이름 ...]   (이름을 빼면 모든 자료)
"""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from .paths import CACHE, clarksons_dir, data_root, public_dir

MANIFEST = CACHE / "_manifest.json"


# ---------- 공통 ----------
def sniff_encoding(raw: bytes) -> str:
    """앞부분 바이트로 utf-8(-sig)·cp949를 판별한다. 끝에서 글자 중간이 잘리지 않게 마지막 줄바꿈까지만 본다."""
    cut = raw.rfind(b"\n")
    if cut > 0:
        raw = raw[:cut]
    for enc in ("utf-8-sig", "cp949"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp949"


def read_csv_auto(path: Path, **kw) -> tuple[pd.DataFrame, str]:
    with open(path, "rb") as f:
        enc = sniff_encoding(f.read(400_000))
    df = pd.read_csv(path, encoding=enc, encoding_errors="replace", **kw)
    return df, enc


def parse_kst(s: pd.Series) -> pd.Series:
    """'2023-01-01T20:00:00+09:00'·'2024-12-31 00:00:00'·'2023-01-01 00:00'·'2016.07.03 09:00'을 시간대 없는 KST로."""
    t = s.astype("string").str.strip().str.replace("T", " ", regex=False).str.replace(".", "-", regex=False)
    t = t.str.replace(r"\+\d{2}:\d{2}$", "", regex=True).str.slice(0, 19)
    t = t.where(t.str.len() > 0)
    return pd.to_datetime(t, errors="coerce", format="mixed")


def _rel(p: Path) -> str:
    try:
        return p.relative_to(data_root()).as_posix()
    except ValueError:
        return p.as_posix()


def _stamp(files: list[Path]) -> list[list]:
    return [[_rel(p), p.stat().st_size, p.stat().st_mtime_ns] for p in files]


def _manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _save_manifest(m: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 자료 정의 ----------
@dataclass
class Spec:
    name: str
    files: Callable[[], list[Path]]
    reader: Callable[[list[Path]], pd.DataFrame]
    partition: Callable[[Path], str] | None = None  # 월별 분할 캐시(파일명 YYYYMMDD.csv → YYYYMM)
    note: str = ""
    version: int = 1  # 읽는 방법을 바꾸면 올린다(원본이 같아도 캐시를 다시 만든다)


def _glob(rel: str, pattern: str) -> Callable[[], list[Path]]:
    def f() -> list[Path]:
        d = public_dir() / rel
        return sorted(p for p in d.glob(pattern) if p.is_file() and not p.name.startswith("_") and p.stat().st_size > 0)
    return f


def _read_portmis(files: list[Path], time_cols: tuple[str, ...]) -> pd.DataFrame:
    frames = []
    for p in files:
        df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        if len(df):
            df["_day"] = p.stem  # 받은 날짜 파일(조회일)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for c in time_cols:
        if c in df:
            df[c + "_t"] = parse_kst(df[c])
    return df


CNTL_TIMES = ("cntrlOpertDt", "aprtfEtryptDt", "tkoffDt")
VSSL_TIMES = ("etryptDt", "tkoffDt", "tkoffPrrrnDt", "dstnEtryptDt")


def read_cntl(files):
    return _read_portmis(files, CNTL_TIMES)


def read_vssl(files):
    return _read_portmis(files, VSSL_TIMES)


def read_voyages(files):
    df, _ = read_csv_auto(files[0], dtype={"clsgn": str, "etryptYear": str, "etryptCo": str})
    for c in ("arrival_t", "berth_t"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["vsslGrtg"] = pd.to_numeric(df["vsslGrtg"], errors="coerce")
    return df


def read_waiting(files):
    df, _ = read_csv_auto(files[0])
    df["t"] = pd.to_datetime(df["t"], errors="coerce")
    return df


ASOS_COLS = {"지점": "stn", "지점명": "stn_name", "일시": "t", "기온(°C)": "temp", "강수량(mm)": "rain",
             "풍속(m/s)": "wind", "풍속 QC플래그": "wind_qc", "풍향(16방위)": "wind_dir", "습도(%)": "humid",
             "해면기압(hPa)": "slp", "시정(10m)": "vis", "현상번호(국내식)": "wx_code"}


def read_asos(files):
    frames = []
    for p in files:
        df, _ = read_csv_auto(p, dtype=str, keep_default_na=False)
        df = df[[c for c in ASOS_COLS if c in df]].rename(columns=ASOS_COLS)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    df["t"] = parse_kst(df["t"])
    for c in ("temp", "rain", "wind", "wind_dir", "humid", "slp", "vis"):
        if c in df:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


BUOY_COLS = {"지점": "stn", "일시": "t", "풍속(m/s)": "wind", "풍향(deg)": "wind_dir", "GUST풍속(m/s)": "gust",
             "현지기압(hPa)": "pressure", "습도(%)": "humid", "기온(°C)": "temp", "수온(°C)": "sst",
             "최대파고(m)": "wave_max", "유의파고(m)": "wave_sig", "평균파고(m)": "wave_mean",
             "파주기(sec)": "wave_period", "파향(deg)": "wave_dir"}


def read_buoy(files):
    frames = []
    for p in files:
        df, _ = read_csv_auto(p, dtype=str, keep_default_na=False)
        frames.append(df[[c for c in BUOY_COLS if c in df]].rename(columns=BUOY_COLS))
    df = pd.concat(frames, ignore_index=True)
    df["t"] = parse_kst(df["t"])
    for c in BUOY_COLS.values():
        if c not in ("stn", "t") and c in df:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


def _latlon(s: pd.Series, pos: str) -> pd.Series:
    v = pd.to_numeric(s.astype("string").str.extract(r"([0-9.]+)")[0], errors="coerce")
    sign = s.astype("string").str.contains(pos, na=False).map({True: 1.0, False: -1.0})
    return v * sign


def read_typhoon_track(files):
    df, _ = read_csv_auto(files[0], dtype=str, keep_default_na=False)
    df = df.rename(columns={"년도": "year", "태풍번호": "num", "분석시각": "t", "위도": "lat", "경도": "lon", "태풍등급": "grade"})
    # 경로의 분석시각은 UTC(00·06·12·18시)다: 태풍목록의 발생 시각(KST)이 첫 경로점 + 9시간과 같다 → KST로 바꾼다
    df["t"] = parse_kst(df["t"]) + pd.Timedelta(hours=9)
    df["lat"] = _latlon(df["lat"], "N")
    df["lon"] = _latlon(df["lon"], "E")
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["num"] = pd.to_numeric(df["num"], errors="coerce").astype("Int64")
    return df


def read_typhoon_list(files):
    df, _ = read_csv_auto(files[0], dtype=str, keep_default_na=False)
    df = df.rename(columns={"태풍번호": "num", "태풍명": "name", "영문명": "ename", "진행여부": "status",
                            "영향도": "impact", "발생": "start", "소멸": "end", "비고": "note"})
    df["start"] = parse_kst(df["start"])
    df["end"] = parse_kst(df["end"])
    df["num"] = pd.to_numeric(df["num"], errors="coerce").astype("Int64")
    df["year"] = df["start"].dt.year.astype("Int64")
    return df


def read_road(files):
    df, _ = read_csv_auto(files[0], dtype=str, keep_default_na=False)
    df = df.rename(columns={"집계일자": "date", "집계시": "hour", "콘존ID": "zone_id", "콘존명": "zone_name",
                            "차로유형구분코드": "lane_type", "평균속도": "speed"})
    df = df[[c for c in ("date", "hour", "zone_id", "zone_name", "lane_type", "speed") if c in df]]
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce").astype("Int64")
    df["t"] = pd.to_datetime(df["date"], errors="coerce") + pd.to_timedelta(df["hour"].astype("float"), unit="h")
    df["speed"] = pd.to_numeric(df["speed"], errors="coerce")
    return df


BPA_CALL_TIMES = ("출항예정일시", "전출항지출항일시", "입항일시", "출항일시")


def read_bpa_calls(files):
    df, _ = read_csv_auto(files[0], dtype=str, keep_default_na=False)
    for c in BPA_CALL_TIMES:
        if c in df:
            df[c + "_t"] = parse_kst(df[c])
    for c in ("총톤수", "국제총톤수", "적재톤수", "양하톤수", "환적톤수"):
        if c in df:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


def read_plain(files):
    frames = []
    for p in files:
        df, _ = read_csv_auto(p, dtype=str, keep_default_na=False)
        df["_file"] = p.name
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def read_platform_zip(path: Path) -> dict[str, pd.DataFrame]:
    """물류 플랫폼 표본 ZIP(코드북 제외)을 표로 읽는다. 캐시하지 않는다(작음)."""
    out = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.lower().endswith(".csv") or "코드북" in n:
                continue
            raw = z.read(n)
            from io import BytesIO
            out[n] = pd.read_csv(BytesIO(raw), dtype=str, keep_default_na=False, encoding=sniff_encoding(raw[:400_000]),
                                 encoding_errors="replace")
    return out


def _month(p: Path) -> str:
    return p.stem[:6]


SPECS: dict[str, Spec] = {s.name: s for s in [
    Spec("cntl", _glob("portmis/cntl/daily", "*.csv"), read_cntl, _month, "PORT-MIS 관제정보(입항·이동·출항 이벤트)"),
    Spec("vssl", _glob("portmis/vssl/daily", "*.csv"), read_vssl, _month, "PORT-MIS 선박운항정보(입출항 신고)"),
    Spec("voyages", _glob("derived", "newport_voyages.csv"), read_voyages, None, "신항 컨테이너선 항차·대기 라벨"),
    Spec("waiting_2h", _glob("derived", "newport_waiting_2h.csv"), read_waiting, None, "2시간 대기 척수"),
    Spec("asos", _glob("manual/kma_asos", "*.csv"), read_asos, None, "ASOS 시간자료(부산·울산·창원)"),
    Spec("buoy", _glob("manual/kma_buoy", "*.csv"), read_buoy, None, "해양기상부이 시간자료"),
    Spec("typhoon_track", _glob("manual/kma_typhoon", "*경로*.csv"), read_typhoon_track, None, "태풍경로", version=2),  # v2: UTC → KST
    Spec("typhoon_list", _glob("manual/kma_typhoon", "*목록*.csv"), read_typhoon_list, None, "태풍목록"),
    Spec("road", _glob("derived", "ex_speed_busan.csv"), read_road, None, "신항 배후 고속도로 1시간 속도"),
    Spec("bpa_calls", _glob("manual/bpa_vessel_calls", "*.csv"), read_bpa_calls, None, "부산항만공사 선박 입출항 집계"),
    Spec("bpa_vessel_monthly", _glob("manual/bpa_vessel_monthly", "*.csv"), read_plain, None, "외내항 선박 통합집계(월)"),
    Spec("bpa_container_monthly", _glob("manual/bpa_container_monthly", "*.csv"), read_plain, None, "외내항 컨테이너 통합집계(월)"),
    Spec("bpa_container_annual", _glob("manual/bpa_container_annual", "*.csv"), read_plain, None, "컨테이너 수송통계(연)"),
    Spec("tide_stations", _glob("manual/khoa_tide_stations", "*.csv"), read_plain, None, "조위관측소 목록"),
]}


def _part_path(name: str, key: str | None) -> Path:
    return CACHE / (f"{name}.parquet" if key is None else f"{name}/{key}.parquet")


def load(name: str, refresh: bool = False, verbose: bool = False) -> pd.DataFrame:
    """캐시가 원본과 같으면 parquet을 읽고, 아니면 원본을 다시 읽어 캐시한다."""
    spec = SPECS[name]
    files = spec.files()
    if not files:
        raise FileNotFoundError(f"{name}: 원본 파일이 없습니다({spec.note})")
    man = _manifest()
    groups: dict[str | None, list[Path]] = {}
    for p in files:
        groups.setdefault(spec.partition(p) if spec.partition else None, []).append(p)
    rebuilt = []
    for key, fs in groups.items():
        mkey = name if key is None else f"{name}:{key}"
        path = _part_path(name, key)
        if not refresh and path.exists() and man.get(mkey, {}).get("files") == _stamp(fs) and man.get(mkey, {}).get("version", 1) == spec.version:
            continue
        t0 = time.time()
        df = spec.reader(fs)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        man[mkey] = {"files": _stamp(fs), "version": spec.version, "rows": int(len(df)), "built": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "seconds": round(time.time() - t0, 1)}
        rebuilt.append(key or name)
    if spec.partition:  # 원본에서 사라진 달의 캐시는 지운다
        for old in [k for k in man if k.startswith(name + ":") and k.split(":", 1)[1] not in groups]:
            _part_path(name, old.split(":", 1)[1]).unlink(missing_ok=True)
            man.pop(old)
    if rebuilt:
        _save_manifest(man)
    if verbose:
        print(f"{name}: 파일 {len(files)}개 · 다시 읽음 {len(rebuilt)}{'개 묶음' if spec.partition else ''}")
    if spec.partition:
        return pd.concat([pd.read_parquet(_part_path(name, k)) for k in sorted(groups)], ignore_index=True)
    return pd.read_parquet(_part_path(name, None))


def source_files(name: str) -> list[Path]:
    return SPECS[name].files()


def clarksons_files() -> list[Path]:
    """Clarksons 원자료 목록(읽기만, 공개 산출물에 쓰지 않음)."""
    root = clarksons_dir()
    dirs = ["A1_port_congestion_index", "A2_capacity_in_port_by_region", "B1_containership_fleet",
            "C1_containership_timecharter", "D2_E2_port_trade_context", "E1_freight_indices", "meta"]
    return sorted(p for d in dirs for p in (root / d).glob("*") if p.is_file() and not p.name.startswith("."))


def main():
    ap = argparse.ArgumentParser(description="실자료 캐시 만들기")
    ap.add_argument("names", nargs="*", help=f"자료 이름(없으면 전부): {', '.join(SPECS)}")
    ap.add_argument("--refresh", action="store_true", help="캐시를 무시하고 모두 다시 읽기")
    a = ap.parse_args()
    for name in a.names or list(SPECS):
        t0 = time.time()
        df = load(name, refresh=a.refresh, verbose=True)
        print(f"   → {len(df):,}행 · {time.time() - t0:.1f}초")


if __name__ == "__main__":
    main()
