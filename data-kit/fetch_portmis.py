"""해양수산부 PORT-MIS 오픈API 수집: 부산(prtAgCd=020)을 하루씩 받아 날짜별 CSV로 저장.

  --api cntl  해양수산부_관제정보(15006354)      → data/portmis/cntl/daily/YYYYMMDD.csv   [필수: 대기 라벨]
  --api vssl  해양수산부_선박운항정보(15006353)  → data/portmis/vssl/daily/YYYYMMDD.csv   [선택: 입항 신고·도착률]

명세(관제정보):
  GET https://apis.data.go.kr/1192000/CntlVssl2/Info
  serviceKey(필수) pageNo numOfRows(최대 50) prtAgCd(필수, 부산 020) sde ede(필수, YYYYMMDD) clsgn(선택)
  응답 XML: resultCode(00 정상) totalCount items/item{clsgn vsslNm vsslKndNm vsslGrtg etryptYear etryptCo
            cntrlNm cntrlSe cntrlOpertDt fcltyCd fcltyNm aprtfEtryptDt tkoffDt ...}
선박운항정보는 같은 요청 변수(+ deGb=I 입항일 기준)를 씁니다. 오퍼레이션 경로(/Info)는 확인 전이라 --probe로 먼저 보세요.

사용:
  python fetch_portmis.py --probe                                   # 어제 하루만 받아 필드·값 확인
  python fetch_portmis.py --start 2023-01-01 --end 2026-11-07 --reverse   # 최근 날짜부터
이미 받은 날짜는 건너뛰므로, 호출량 초과(코드 22)로 멈추면 다음 날 같은 명령을 다시 실행하면 됩니다.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
from collections import Counter
from xml.etree import ElementTree as ET

from common import DATA, HttpFail, daterange, http_get, load_env, need

APIS = {
    "cntl": {"url": "https://apis.data.go.kr/1192000/CntlVssl2/Info", "out": "portmis/cntl/daily",
             "name": "해양수산부_관제정보(15006354)", "extra": {}},
    "vssl": {"url": "https://apis.data.go.kr/1192000/VsslEtrynd5/Info", "out": "portmis/vssl/daily",
             "name": "해양수산부_선박운항정보(15006353)", "extra": {"deGb": "I"}},
}
OK_CODES = {"00", "0", "000", "03"}  # 03 = NODATA(그날 기록 없음)
STOP_CODES = {"20": "이 API에 대한 활용신청(권한)이 없습니다 — 공공데이터포털에서 활용신청",
              "30": "등록되지 않은 인증키 — 승인 직후 1~2시간은 이럴 수 있으니 잠시 뒤 다시, 계속되면 키 확인",
              "31": "인증키 기한 만료", "32": "등록되지 않은 IP",
              "12": "서비스 없음/폐기 — 주소(오퍼레이션)를 명세에서 확인해 --base-url로 지정",
              "22": "일일 호출량 초과 — 내일 같은 명령으로 이어서 받으세요(받은 날짜는 건너뜀)",
              "29": "차단된 IP"}
RETRY_CODES = {"01", "04", "05", "23", "XML"}


def parse(xml_bytes: bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        head = xml_bytes[:200].decode("utf-8", "replace").strip()
        return "XML", f"XML이 아닌 응답: {head}", 0, []
    code = (root.findtext(".//resultCode") or "").strip()
    msg = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "").strip()
    if not code:  # 포털 게이트웨이 오류 형식(OpenAPI_ServiceResponse)
        code = (root.findtext(".//returnReasonCode") or "").strip()
    if not code:  # HTML 오류 화면 등 형식은 맞지만 결과 코드가 없는 응답 → 다시 시도
        head = xml_bytes[:200].decode("utf-8", "replace").strip()
        return "XML", f"결과 코드 없는 응답: {head}", 0, []
    try:
        total = int((root.findtext(".//totalCount") or "0").strip() or 0)
    except ValueError:
        total = 0
    items = [{ch.tag: (ch.text or "").strip() for ch in it} for it in root.iter("item")]
    return code, msg, total, items


def fail_http(e: HttpFail, day: str):
    hint = {401: "인증키가 아직 활성화되지 않았거나(승인 후 1~2시간) 잘못 들어갔습니다",
            403: "활용신청이 안 됐거나 인증키가 맞지 않습니다",
            404: "주소(오퍼레이션 경로)가 다릅니다 — 명세의 요청주소를 --base-url로 지정하세요",
            429: "호출이 너무 잦습니다 — --sleep을 늘리세요"}.get(e.code, "응답 본문을 확인하세요")
    sys.exit(f"[중단] {day} HTTP {e.code}: {hint}\n  응답: {e.text()}")


def fetch_day(key, day, port, rows, sleep, base, extra, counter):
    out, page, total = [], 1, None
    while True:
        params = {"serviceKey": key, "pageNo": page, "numOfRows": rows, "prtAgCd": port, "sde": day, "ede": day, **extra}
        last = ""
        for attempt in range(5):
            try:
                raw = http_get(base, params)
            except HttpFail as e:
                fail_http(e, day)
            except RuntimeError as e:
                sys.exit(f"[중단] {day} 네트워크 오류: {e}\n  → 연결을 확인하고 같은 명령을 다시 실행하세요(받은 날짜는 건너뜀).")
            counter[0] += 1
            code, msg, tot, items = parse(raw)
            if code in OK_CODES:
                break
            if code in STOP_CODES:
                sys.exit(f"[중단] {day} 응답 코드 {code} {msg}\n  → {STOP_CODES[code]}")
            last = f"{code} {msg}"
            if code in RETRY_CODES:
                time.sleep(2 + 3 * attempt)
                continue
            sys.exit(f"[중단] {day} 알 수 없는 응답 {code} {msg}")
        else:
            sys.exit(f"[중단] {day} 5번 다시 시도해도 실패: {last}")
        total = tot if total is None else total
        out.extend(items)
        if len(out) >= total or not items:
            break
        page += 1
        time.sleep(sleep)
    if total is not None and len(out) != total:
        print(f"  ! {day}: totalCount {total}건 중 {len(out)}건만 받음")
    return out


def main():
    yday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", choices=sorted(APIS), default="cntl")
    ap.add_argument("--start", default=yday)
    ap.add_argument("--end", default=yday)
    ap.add_argument("--reverse", action="store_true", help="최근 날짜부터 받기(호출량이 끊겨도 최근 구간이 먼저 채워짐)")
    ap.add_argument("--port", default="020", help="항만청 코드(부산 020)")
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--sleep", type=float, default=0.25, help="요청 사이 대기(초)")
    ap.add_argument("--max-calls", type=int, default=9500, help="이번 실행의 최대 호출 수(개발계정 하루 10,000)")
    ap.add_argument("--base-url", default=None, help="요청 주소 바꾸기(명세가 다를 때·시험용)")
    ap.add_argument("--probe", action="store_true", help="시작 날짜 하루만 받아 필드·값 분포를 보여 줌")
    a = ap.parse_args()

    api = APIS[a.api]
    base = a.base_url or api["url"]
    load_env()
    # 포털의 'Encoding' 키를 넣어도 되도록 한 번 풀어 둔다(Decoding 키는 그대로).
    key = urllib.parse.unquote(need("DATA_GO_KR_KEY"))
    outdir = DATA / api["out"]
    outdir.mkdir(parents=True, exist_ok=True)
    days = list(daterange(a.start, a.end))
    if a.reverse:
        days.reverse()
    print(f"{api['name']} · 부산({a.port}) · {a.start}~{a.end} ({len(days)}일) → {outdir}")
    counter, fields = [0], []
    done = skipped = 0
    for i, d in enumerate(days):
        day = d.strftime("%Y%m%d")
        f = outdir / f"{day}.csv"
        if f.exists() and not a.probe:
            skipped += 1
            continue
        if counter[0] >= a.max_calls:
            left = sum(1 for x in days[i:] if not (outdir / f"{x:%Y%m%d}.csv").exists())
            print(f"[멈춤] 이번 실행 호출 {counter[0]}회 — 남은 {left}일은 내일 같은 명령으로 이어서 받으세요.")
            break
        items = fetch_day(key, day, a.port, a.rows, a.sleep, base, api["extra"], counter)
        for it in items:
            for k in it:
                if k not in fields:
                    fields.append(k)
        cols = fields or ["clsgn"]
        tmp = f.with_suffix(".part")
        with tmp.open("w", newline="", encoding="utf-8-sig") as fp:
            w = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(items)
        tmp.replace(f)  # 다 받은 날만 확정(중간에 끊기면 그날은 다시 받음)
        done += 1
        print(f"{day}: {len(items)}건 (누적 호출 {counter[0]})")
        if a.probe:
            print("\n필드:", ", ".join(cols))
            for col in ("cntrlNm", "cntrlSe", "vsslKndNm"):
                if any(col in it for it in items):
                    print(f"\n[{col}]", Counter(it.get(col, "") for it in items).most_common(15))
            if any("fcltyNm" in it for it in items):
                print("\n[fcltyNm 상위 30]")
                for name, n in Counter(it.get("fcltyNm", "") for it in items).most_common(30):
                    print(f"  {n:4d}  {name}")
            print("\n예시 3건:", json.dumps(items[:3], ensure_ascii=False, indent=1))
            n_days = (dt.date(2026, 11, 7) - dt.date(2023, 1, 1)).days + 1
            est = counter[0] * n_days
            print(f"\n[호출 수 가늠] 하루치 {counter[0]}회 → 2023-01-01~2026-11-07({n_days:,}일)이면 약 {est:,}회, "
                  f"하루 9,500회 기준 약 {max(1, -(-est // 9500))}일 (날마다 기록 수가 달라 대략치)")
            break
        time.sleep(a.sleep)
    print(f"\n완료: 새로 받은 날 {done}, 이미 있던 날 {skipped}, 호출 {counter[0]}회 → {outdir}")


if __name__ == "__main__":
    main()
