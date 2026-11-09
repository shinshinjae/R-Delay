"""기상청 API허브 기간 조회 수집 → data/kma/{asos|buoy}/{지점}_{시작}_{끝}.txt + 합친 CSV

  --kind asos  지상관측(ASOS) 시간자료 kma_sfctm3.php   지점 기본: 부산 159 · 울산 152 · 창원 155   → asos_hourly.csv
  --kind buoy  해양기상부이 kma_buoy2.php               지점 기본: 거제도 22104 · 이수도 22513 · 지심도 22512 · 소매물도 22485
                                                         → buoy_hourly.csv
  · 거제도(22104)는 오래된 부이라 백테스트 기간 전체를 덮지만 번호는 공식 문서로 확인하지 못했습니다(--probe로 확인).
  · 이수도·지심도·소매물도는 2025-06-16에 개방포털에 추가된 지점이라 그 이전 자료가 없을 수 있습니다.

요청: ?tm1=YYYYMMDDHHmm&tm2=YYYYMMDDHHmm&stn=159&help=1&authKey=…  (tm1~tm2는 31일 이내 → 30일씩 나눠 받음)
응답: '#'으로 시작하는 설명 줄 + 공백으로 나뉜 값 줄. help=1이면 '# 1. TM : 관측시각' 같은 열 설명이 붙어 그 순서로
      열 이름을 정합니다(없으면 값 줄 바로 위의 '#' 머리줄을 씁니다). 결측은 -9, -99 등으로 옵니다.
부이 엔드포인트 이름(kma_buoy2.php)은 공식 문서로 확인하지 못했습니다. --probe로 먼저 보세요.

사용:
  python fetch_kma.py --kind asos --start 2019-01-01 --end 2026-11-07
  python fetch_kma.py --kind buoy --stn 22104 --start 2023-01-02 --end 2023-01-03 --probe
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import time

from common import DATA, HttpFail, http_get, load_env, need

KINDS = {
    "asos": {"url": "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm3.php", "stn": "159,152,155",
             "out": "kma/asos", "merged": "asos_hourly.csv"},
    "buoy": {"url": "https://apihub.kma.go.kr/api/typ01/url/kma_buoy2.php", "stn": "22104,22513,22512,22485",
             "out": "kma/buoy", "merged": "buoy_hourly.csv"},
}
COL_RE = re.compile(r"^#\s*(\d+)\.\s*([A-Za-z][A-Za-z0-9_]*)\s*:")


def windows(start: str, end: str, days: int = 30):
    d0 = dt.datetime.fromisoformat(start)
    d1 = dt.datetime.fromisoformat(end) + dt.timedelta(hours=23)
    while d0 <= d1:
        w1 = min(d0 + dt.timedelta(days=days) - dt.timedelta(hours=1), d1)
        yield d0, w1
        d0 = w1 + dt.timedelta(hours=1)


def parse_text(txt: str):
    """열 이름과 값 줄을 돌려준다. 번호 붙은 설명 줄이 없으면 값 줄 바로 위 '#' 머리줄을 이름으로 쓴다."""
    names, rows, hashes = [], [], []
    for line in txt.splitlines():
        m = COL_RE.match(line)
        if m:
            names.append(m.group(2).upper())
            continue
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            hashes.append(s.lstrip("#").split())
            continue
        if s.startswith("<") or s.startswith("{"):
            continue  # HTML·JSON 오류 응답
        rows.append(s.rstrip("=").split())
    if rows and len(names) != len(rows[0]):
        width = len(rows[0])
        for h in reversed(hashes):
            if len(h) == width and h[0].upper() in ("TM", "YYMMDDHHMI", "YYYYMMDDHHMI", "KST"):
                names = ["TM" if i == 0 else t.upper() for i, t in enumerate(h)]
                break
    return names, rows


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("euc-kr", "replace")  # API허브 설명 줄은 EUC-KR로 오기도 함


def merge(outdir, merged_name):
    out = outdir / merged_name
    header, n, dropped = None, 0, 0
    with out.open("w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        for f in sorted(outdir.glob("*_*_*.txt")):
            names, rows = parse_text(f.read_text(encoding="utf-8"))
            if not rows:
                continue
            width = len(rows[0])
            cols = names if len(names) == width else [f"c{i:02d}" for i in range(width)]
            if header is None:
                header = cols
                w.writerow(header)
            for r in rows:
                if len(r) == len(header):
                    w.writerow(r)
                    n += 1
                else:
                    dropped += 1
    note = f", 열 개수가 달라 뺀 줄 {dropped}" if dropped else ""
    print(f"합친 파일: {out} ({n}행{note})")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=sorted(KINDS), default="asos")
    ap.add_argument("--stn", default=None, help="쉼표로 여러 지점(기본값은 종류별 위 목록)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--probe", action="store_true", help="첫 구간만 받아 열 이름과 값을 보여 줌")
    a = ap.parse_args()
    kind = KINDS[a.kind]
    base = a.base_url or kind["url"]
    load_env()
    key = need("KMA_APIHUB_KEY")
    outdir = DATA / kind["out"]
    outdir.mkdir(parents=True, exist_ok=True)
    stations = [s.strip() for s in (a.stn or kind["stn"]).split(",") if s.strip()]
    calls = 0
    for stn in stations:
        for w0, w1 in windows(a.start, a.end, a.window_days):
            tm1, tm2 = w0.strftime("%Y%m%d%H00"), w1.strftime("%Y%m%d%H00")
            f = outdir / f"{stn}_{tm1}_{tm2}.txt"
            if f.exists() and not a.probe:
                continue
            try:
                raw = http_get(base, {"tm1": tm1, "tm2": tm2, "stn": stn, "help": 1, "authKey": key})
            except HttpFail as e:
                hint = {401: "인증키(authKey)를 확인하세요", 403: "이 API의 활용신청이 안 됐거나 일일 한도를 넘었습니다",
                        404: "주소가 다릅니다 — API허브 해당 자료 화면의 요청 주소를 --base-url로 지정하세요"}
                sys.exit(f"[중단] {stn} {tm1}: HTTP {e.code} — {hint.get(e.code, '응답을 확인하세요')}\n  응답: {e.text()}")
            except RuntimeError as e:
                sys.exit(f"[중단] {stn} {tm1}: 네트워크 오류: {e}\n  → 연결을 확인하고 같은 명령을 다시 실행하세요(받은 구간은 건너뜀).")
            calls += 1
            txt = decode(raw)
            names, rows = parse_text(txt)
            low = txt.lower()
            if not rows and any(k in low for k in ("error", "auth", "인증", "<html", "exceed", "초과")):
                sys.exit(f"[중단] {stn} {tm1}: 값이 없고 오류 응답으로 보입니다 → {txt[:300]}")
            f.write_text(txt, encoding="utf-8")
            print(f"{stn} {tm1}~{tm2}: {len(rows)}행, 열 {len(names) or '이름 없음'}")
            if a.probe:
                print("열:", names)
                for r in rows[:3]:
                    print("  ", r)
                return
            time.sleep(a.sleep)
    print(f"호출 {calls}회")
    merge(outdir, kind["merged"])
    if a.kind == "asos":
        print("앞 열: TM(관측시각 KST) · STN · WD(풍향 16방위) · WS(풍속 m/s) …  결측은 -9 계열")


if __name__ == "__main__":
    main()
