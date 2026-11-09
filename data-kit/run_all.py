"""한 번에 실행: API 자료 수집 → 대기 라벨 → 점검표. 여러 번 실행해도 받은 것은 건너뜁니다.

  python run_all.py                      # 기본 기간: 관제정보 2023-01-01~2026-11-07(최근부터), 기상 2019-01-01~
  python run_all.py --with-vssl          # 선박운항정보도 함께(선택)
  python run_all.py --end 2026-11-06     # 끝 날짜 바꾸기

키가 없는 부분은 건너뛰고 무엇이 필요한지 알려 줍니다. 하루 호출 한도에 걸리면 내일 같은 명령을 다시 실행하세요.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from common import DATA, ROOT, load_env


def run(label, args):
    print(f"\n━━ {label} ━━")
    r = subprocess.run([sys.executable, str(ROOT / args[0]), *args[1:]], cwd=ROOT)
    return r.returncode == 0


def has(name):
    v = os.environ.get(name, "").strip()
    return bool(v) and not v.startswith("여기에")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2023-01-01", help="관제정보 시작일")
    ap.add_argument("--end", default="2026-11-07")
    ap.add_argument("--weather-start", default="2019-01-01")
    ap.add_argument("--with-vssl", action="store_true")
    a = ap.parse_args()
    load_env()
    result = {}
    if has("DATA_GO_KR_KEY"):
        result["관제정보"] = run("1/5 해양수산부_관제정보 (최근 날짜부터)",
                              ["fetch_portmis.py", "--api", "cntl", "--start", a.start, "--end", a.end, "--reverse"])
        if a.with_vssl:
            result["선박운항정보"] = run("1+/5 해양수산부_선박운항정보",
                                   ["fetch_portmis.py", "--api", "vssl", "--start", a.start, "--end", a.end, "--reverse"])
    else:
        print("\n· DATA_GO_KR_KEY가 없어 관제정보를 건너뜁니다(README 1단계).")
    if has("KMA_APIHUB_KEY"):
        result["ASOS"] = run("2/5 기상청 ASOS 시간자료", ["fetch_kma.py", "--kind", "asos", "--start", a.weather_start, "--end", a.end])
        result["해양기상부이"] = run("3/5 해양기상부이(이름 확인 전 — 실패하면 개방포털에서 CSV)",
                               ["fetch_kma.py", "--kind", "buoy", "--start", a.weather_start, "--end", a.end])
    else:
        print("\n· 기상청 API허브 키가 없어 기상 API는 건너뜁니다 → 기상자료개방포털 CSV를 data/manual/kma_asos·kma_buoy에 넣으세요(가이드 5절).")
    if any((DATA / "portmis" / "cntl" / "daily").glob("*.csv")):
        result["대기 라벨"] = run("4/5 대기 12시간 초과 라벨(먼저 --inspect로 패턴 확인 권장)", ["build_wait_labels.py"])
        if any((DATA / "clarksons" / "raw").glob("CLK_B1_*")):
            result["선박 TEU 매칭"] = run("4+/5 Clarksons B1 ↔ PORT-MIS", ["match_clarksons_ships.py"])
    if (DATA / "manual" / "ex_speed_1h").exists() and any(p for p in (DATA / "manual" / "ex_speed_1h").iterdir() if not p.name.startswith("_")):
        result["도로 속도 추출"] = run("4+/5 도로공사 속도에서 신항 배후 구간만", ["filter_ex_speed.py"])
    run("5/5 자료 점검표", ["check_inventory.py", "--start", a.start, "--end", a.end])
    print("\n━━ 요약 ━━")
    for k, ok in result.items():
        print(f"  {'완료' if ok else '멈춤/실패 — 위 메시지 확인'} · {k}")
    print(f"  점검표: {DATA / 'data_inventory.md'}")


if __name__ == "__main__":
    main()
