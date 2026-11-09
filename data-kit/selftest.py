"""인증키 없이 키트가 제대로 도는지 확인합니다. 가짜 서버(관제정보·API허브)와 가짜 파일로 전 과정을 돌립니다.

  python selftest.py      # 30초 안팎, 인터넷 필요 없음. 실제 data 폴더와 .env는 건드리지 않습니다.
"""
from __future__ import annotations

import datetime as dt
import http.server
import json
import os
import random
import socketserver
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
KEY = "TEST+KEY/abc=="                      # 포털 'Decoding' 키처럼 +, /, = 가 섞인 값
ENC_KEY = urllib.parse.quote(KEY, safe="")  # 포털 'Encoding' 키 — 이걸 넣어도 돼야 함
KMA_KEY = "kmaTESTkey"
D0 = dt.date(2026, 10, 1)
DAYS = [D0 + dt.timedelta(days=i) for i in range(4)]
DSTR = [d.strftime("%Y%m%d") for d in DAYS]
STATE = {"glitch_html": False, "glitch_23": False, "calls": 0}


def synth():
    rng = random.Random(7)
    ev, truth, vid = [], [], 0
    for d in DAYS:
        for _ in range(6):  # 컨테이너선
            vid += 1
            cs = f"TST{vid:03d}"
            t0 = dt.datetime.combine(d, dt.time(rng.randint(0, 23), rng.choice([0, 10, 30, 45])))
            direct = rng.random() < 0.35
            wait = 0.0 if direct else rng.choice([2, 5, 8, 11.5, 13, 18, 26, 40])
            gt = rng.choice([9500, 28000, 54000, 98000, 152000, 228000])
            base = dict(prtAgCd="020", prtAgNm="부산", etryptYear=str(d.year), etryptCo=str(100 + vid), clsgn=cs,
                        vsslKndCd="2", vsslKndNm="풀컨테이너선", vsslNm=f"TEST CARRIER {vid}", vsslGrtg=str(gt))
            berth = rng.choice(["부산신항 3부두(PNIT)", "신항 5부두(BNCT)", "부산신항 2부두(PNC)"])
            arr_fac = berth if direct else rng.choice(["신항 정박지 S-1", "신항 정박지 S-3"])
            tb = t0 + dt.timedelta(hours=wait)
            ev.append({**base, "cntrlNm": "입항", "cntrlSe": "I", "cntrlOpertDt": t0.strftime("%Y%m%d%H%M"), "fcltyNm": arr_fac})
            if not direct:
                ev.append({**base, "cntrlNm": "이동", "cntrlSe": "M", "cntrlOpertDt": tb.strftime("%Y%m%d%H%M"), "fcltyNm": berth})
            ev.append({**base, "cntrlNm": "출항", "cntrlSe": "O", "cntrlOpertDt": (tb + dt.timedelta(hours=20)).strftime("%Y%m%d%H%M"),
                       "fcltyNm": berth})
            truth.append({"clsgn": cs, "name": base["vsslNm"], "gt": gt, "berth_day": tb.date(), "wait": wait})
        for _ in range(4):  # 다른 선종(빠져야 함)
            vid += 1
            t0 = dt.datetime.combine(d, dt.time(rng.randint(0, 20)))
            base = dict(prtAgCd="020", etryptYear=str(d.year), etryptCo=str(100 + vid), clsgn=f"OIL{vid:03d}",
                        vsslKndNm="유조선", vsslNm=f"TEST TANKER {vid}", vsslGrtg="30000")
            ev.append({**base, "cntrlNm": "입항", "cntrlSe": "I", "cntrlOpertDt": t0.strftime("%Y%m%d%H%M"), "fcltyNm": "북항 정박지 N-2"})
            ev.append({**base, "cntrlNm": "이동", "cntrlSe": "M", "cntrlOpertDt": (t0 + dt.timedelta(hours=3)).strftime("%Y%m%d%H%M"),
                       "fcltyNm": "감천항 1부두"})
    ev.sort(key=lambda e: e["cntrlOpertDt"])
    return ev, truth


EVENTS, TRUTH = synth()


def xml_body(code, msg, total, items, rows=50, page=1):
    its = "".join("<item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in it.items()) + "</item>" for it in items)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response><header><resultCode>{code}</resultCode>'
            f"<resultMsg>{msg}</resultMsg></header><body><items>{its}</items><numOfRows>{rows}</numOfRows>"
            f"<pageNo>{page}</pageNo><totalCount>{total}</totalCount></body></response>").encode()


def xml_gateway(code, msg):
    return (f"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg><returnAuthMsg>{msg}</returnAuthMsg>"
            f"<returnReasonCode>{code}</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>").encode()


def kma_text(q, buoy):
    t1 = dt.datetime.strptime(q["tm1"], "%Y%m%d%H%M")
    t2 = dt.datetime.strptime(q["tm2"], "%Y%m%d%H%M")
    stn = q["stn"]
    out = ["#START7777", "#" + "-" * 60]
    if buoy:
        out += ["# 해양기상부이 시간자료", "# TM           STN   WD1  WS1  GST1   PA    HM    TA    TW  WH_MAX WH_SIG WH_AVE  WP"]
    else:
        out += ["#  기상청 지상관측 시간자료 [입력인수형태][예] ?tm=201007151200&stn=0&help=1",
                "#  1. TM     : 관측시각 (KST)", "#  2. STN    : 국내 지점번호", "#  3. WD     : 풍향 (16방위)",
                "#  4. WS     : 풍속 (m/s)", "#  5. TA     : 기온 (C)", "#  6. RN     : 강수량 (mm)",
                "# YYMMDDHHMI STN  WD   WS    TA    RN", "#        KST  ID  16  m/s     C    mm"]
    t = t1
    while t <= t2:
        if buoy:
            out.append(f"{t:%Y%m%d%H%M} {stn} 200 4.1 6.0 1012.3 80 15.2 17.1 1.8 1.1 0.7 5.2")
        else:
            out.append(f"{t:%Y%m%d%H%M} {stn} 20 3.4 12.1 -9.0")
        t += dt.timedelta(hours=1)
    out.append("#7777END")
    return "\n".join(out).encode("euc-kr")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/xml;charset=UTF-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        if u.path == "/1192000/CntlVssl2/Info":
            return self.cntl(q)
        if u.path.endswith(("kma_sfctm3.php", "kma_buoy2.php")):
            if q.get("authKey") != KMA_KEY:
                return self.send(401, b'{"result":{"status":401,"message":"invalid authKey"}}', "application/json")
            return self.send(200, kma_text(q, u.path.endswith("kma_buoy2.php")), "text/plain;charset=EUC-KR")
        self.send(404, b"not found", "text/plain")

    def cntl(self, q):
        key = q.get("serviceKey", "")
        if key == "QUOTA":
            return self.send(200, xml_gateway("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"))
        if key != KEY:
            return self.send(200, xml_gateway("30", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"))
        STATE["calls"] += 1
        sde, ede = q["sde"], q["ede"]
        page, rows = int(q.get("pageNo", 1)), int(q.get("numOfRows", 10))
        if sde == DSTR[1] and page == 1 and not STATE["glitch_html"]:
            STATE["glitch_html"] = True
            return self.send(200, b"<html><body>Gateway Timeout</body></html>", "text/html")
        if sde == DSTR[2] and page == 1 and not STATE["glitch_23"]:
            STATE["glitch_23"] = True
            return self.send(200, xml_body("23", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR", 0, []))
        items = [e for e in EVENTS if sde <= e["cntrlOpertDt"][:8] <= ede]
        if not items:
            return self.send(200, xml_body("03", "NODATA_ERROR", 0, []))
        rows = min(rows, 50)
        return self.send(200, xml_body("00", "NORMAL SERVICE.", len(items), items[(page - 1) * rows: page * rows], rows, page))


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    srv = Server(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="rdelay_selftest_"))
    data = tmp / "data"
    env = {**os.environ, "RDELAY_DATA_DIR": str(data), "DATA_GO_KR_KEY": ENC_KEY, "KMA_APIHUB_KEY": KMA_KEY,
           "PYTHONIOENCODING": "utf-8"}
    results = []

    def run(name, args, expect_ok=True, must=None, env_=None):
        r = subprocess.run([sys.executable, str(ROOT / args[0]), *args[1:]], cwd=ROOT, env=env_ or env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        ok = (r.returncode == 0) == expect_ok and (must is None or must in out)
        results.append((name, ok))
        print(f"{'통과' if ok else '실패'} · {name}")
        if not ok:
            print("   └", out.strip()[-1500:].replace("\n", "\n     "))
        return out

    d1, d4 = DAYS[0].isoformat(), DAYS[-1].isoformat()
    cntl = f"{base}/1192000/CntlVssl2/Info"
    run("관제정보 4일 받기(Encoding 키·HTML 오류·초당 한도 재시도 포함)",
        ["fetch_portmis.py", "--start", d1, "--end", d4, "--base-url", cntl, "--sleep", "0"], must="완료")
    files = sorted((data / "portmis/cntl/daily").glob("*.csv"))
    results.append(("날짜별 파일 4개", len(files) == 4))
    print(f"{'통과' if len(files) == 4 else '실패'} · 날짜별 파일 4개 ({len(files)}개)")
    run("--probe: 하루치 값·호출 수 가늠", ["fetch_portmis.py", "--probe", "--start", d1, "--end", d1, "--base-url", cntl],
        must="[호출 수 가늠]")
    calls_before = STATE["calls"]
    run("다시 실행하면 받은 날은 건너뜀", ["fetch_portmis.py", "--start", d1, "--end", d4, "--base-url", cntl], must="이미 있던 날 4")
    results.append(("재실행 시 추가 호출 없음", STATE["calls"] == calls_before))
    run("일일 한도(코드 22)면 멈추고 안내", ["fetch_portmis.py", "--start", "2026-10-10", "--end", "2026-10-10", "--base-url", cntl],
        expect_ok=False, must="일일 호출량", env_={**env, "DATA_GO_KR_KEY": "QUOTA"})
    run("미등록 키(코드 30)면 멈추고 안내", ["fetch_portmis.py", "--start", "2026-10-11", "--end", "2026-10-11", "--base-url", cntl],
        expect_ok=False, must="등록되지 않은", env_={**env, "DATA_GO_KR_KEY": "WRONG"})

    run("ASOS 3개 지점 받기·합치기(EUC-KR)", ["fetch_kma.py", "--kind", "asos", "--start", d1, "--end", d4,
                                       "--base-url", f"{base}/api/typ01/url/kma_sfctm3.php", "--sleep", "0"], must="288행")
    run("부이 받기(번호 없는 머리줄)", ["fetch_kma.py", "--kind", "buoy", "--start", d1, "--end", d4,
                               "--base-url", f"{base}/api/typ01/url/kma_buoy2.php", "--sleep", "0"], must="384행")
    hdr = (data / "kma/buoy/buoy_hourly.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    results.append(("부이 열 이름", hdr.startswith("TM,STN,WD1,WS1")))
    print(f"{'통과' if hdr.startswith('TM,STN,WD1,WS1') else '실패'} · 부이 열 이름 ({hdr[:40]})")
    run("API허브 키가 틀리면 멈춤", ["fetch_kma.py", "--kind", "asos", "--start", "2026-10-20", "--end", "2026-10-20",
                               "--base-url", f"{base}/api/typ01/url/kma_sfctm3.php"], expect_ok=False, must="HTTP 401",
        env_={**env, "KMA_APIHUB_KEY": "bad"})

    run("라벨: --inspect", ["build_wait_labels.py", "--inspect"], must="[fcltyNm]")
    run("라벨 만들기", ["build_wait_labels.py"], must="신항 컨테이너선 항차")
    s = json.loads((data / "derived/label_summary.json").read_text(encoding="utf-8"))
    exp = [t for t in TRUTH if t["berth_day"] <= DAYS[-1]]
    n_over = sum(t["wait"] > 12 for t in exp)
    ok = s["신항 컨테이너선 항차"] == len(exp) and s["대기 12시간 초과"] == n_over
    results.append(("라벨 수가 정답과 같음", ok))
    print(f"{'통과' if ok else '실패'} · 라벨 수가 정답과 같음 (항차 {s['신항 컨테이너선 항차']}/{len(exp)}, "
          f"12시간 초과 {s['대기 12시간 초과']}/{n_over})")

    # Clarksons B1 가짜 파일: 위에 제목 줄, 호출부호 있는 배·이름만 맞는 배·없는 배
    import pandas as pd
    rng = random.Random(3)
    rows = []
    for i, t in enumerate(TRUTH):
        if i % 4 == 3:
            continue  # WFR에 없는 배 → 근사
        cs = t["clsgn"] if i % 4 in (0, 1) else ""
        nm = ("M/V " if i % 4 == 2 else "") + t["name"]
        rows.append([nm, "", f"9{100000 + i}", cs, round(t["gt"] / 13.2), t["gt"] * (1 + rng.uniform(-0.01, 0.01)), 2015, "TEST LINE"])
    for i in range(40):
        gt = rng.choice([8000, 20000, 40000, 75000, 120000, 200000]) * rng.uniform(0.9, 1.1)
        rows.append([f"FILLER {i}", "", f"98{i:05d}", f"FL{i:03d}", round(gt / 13.0), gt, 2010, "OTHER"])
    raw = data / "clarksons/raw"
    raw.mkdir(parents=True, exist_ok=True)
    head = pd.DataFrame([["Clarksons Research - World Fleet Register export"] + [""] * 7, [""] * 8,
                         ["Name", "Ex Names", "IMO Number", "Call Sign", "TEU", "GT", "Built", "Operator"]])
    pd.concat([head, pd.DataFrame(rows)], ignore_index=True).to_excel(raw / "CLK_B1_containership_fleet_WFR.xlsx", header=False, index=False)
    out = run("Clarksons B1 ↔ PORT-MIS 매칭", ["match_clarksons_ships.py"], must="ship_teu_map.csv")
    m = pd.read_csv(data / "clarksons/derived/ship_teu_map.csv", dtype=str)
    counts = m["match_method"].value_counts().to_dict()
    ok = counts.get("callsign", 0) == 12 and counts.get("name_gt", 0) == 6 and counts.get("gt_regression_approx", 0) == 6
    results.append(("매칭 방법별 척수(호출부호 12·선명 6·근사 6)", ok))
    print(f"{'통과' if ok else '실패'} · 매칭 방법별 척수 {counts}")

    # 도로공사 가짜 ZIP(CP949)
    ex = data / "manual/ex_speed_1h"
    ex.mkdir(parents=True, exist_ok=True)
    lines = ["집계일자,집계시,콘존ID,콘존명,차로유형구분코드,평균속도"]
    for h in range(24):
        lines.append(f"20261001,{h:02d},0251CZE010,가락IC-서부산IC,0,{70 + h % 7}")
        lines.append(f"20261001,{h:02d},0010CZE200,서울TG-신갈JC,0,{80 + h % 5}")
    with zipfile.ZipFile(ex / "speed_202610.zip", "w") as z:
        z.writestr("speed_202610.csv", "\n".join(lines).encode("cp949"))
    run("도로공사 속도에서 신항 배후 구간만(ZIP·CP949)", ["filter_ex_speed.py"], must="24행")

    run("점검표 폴더 만들기", ["check_inventory.py", "--make-dirs"], must="폴더")
    out = run("점검표 만들기", ["check_inventory.py", "--start", d1, "--end", d4], must="준비된 자료")
    md = (data / "data_inventory.md").read_text(encoding="utf-8")
    ok = "4/4일" in md and "✅ 있음" in md and "❌ 없음" in md
    results.append(("점검표 내용(관제정보 4/4일·있음·없음 표시)", ok))
    print(f"{'통과' if ok else '실패'} · 점검표 내용")

    srv.shutdown()
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과 · 시험 폴더 {tmp}")
    if bad:
        print("실패:", ", ".join(bad))
        sys.exit(1)
    print("키트가 정상입니다. 이제 README 1단계(인증키)부터 진행하세요.")


if __name__ == "__main__":
    main()
