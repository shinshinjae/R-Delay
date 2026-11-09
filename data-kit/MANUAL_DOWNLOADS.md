# 직접 받아 넣을 자료

화면별 단계 안내는 `R-Delay_공개데이터_확보가이드.pdf`에 있습니다.

공공데이터포털 파일데이터는 **로그인 후** 각 페이지의 [다운로드] 버튼으로 받습니다. 받은 파일은 ZIP이면 압축을 풀지 않고 그대로 아래 폴더에 넣으세요.

1. `python check_inventory.py --make-dirs` — 아래 폴더와 폴더마다 `_여기에_넣을_자료.txt` 안내가 생깁니다.
2. 받은 파일을 해당 폴더에 넣습니다. 폴더 경로는 키트의 `data/`(또는 `.env`의 `RDELAY_DATA_DIR`) 기준입니다.
3. `python check_inventory.py` — `data/data_inventory.md`에 있음·없음이 정리됩니다.

## 필수 — 승선 전(11/7)까지

| 자료 | 받는 곳 | 넣을 폴더 | R-Delay에서 쓰는 곳 |
|---|---|---|---|
| 해양수산부_수출입 물류 플랫폼_적시선박 입출항 정보 (15151750) | [15151750](https://www.data.go.kr/data/15151750/fileData.do) | `data/manual/platform_timely_calls/` | 입항·접안·출항 일시 → 대기시간 직접 계산(라벨 검증용, 선사 입력분만) |
| 해양수산부_수출입 물류 플랫폼_터미널 선석 일정 정보 (15151748) | [15151748](https://www.data.go.kr/data/15151748/fileData.do) | `data/manual/platform_berth_schedule/` | 예정 접안(ETB) 대비 실제 접안 지연 |
| 부산항만공사_외내항컨테이너통합집계정보 (15147941) | [15147941](https://www.data.go.kr/data/15147941/fileData.do) | `data/manual/bpa_container_monthly/` | 시설(터미널)별 월 TEU → 물량 기준선<br>**11/5 갱신 예정 → 11/5\~7에 한 번 더 받기(2025년분)** |
| PNC 수출 컨테이너 반입 마감 규정(접안 12시간 전) 공지 PDF (Maersk 공지) | [링크](https://eascpcd.maersk.com/~/media_sc9/maersk/local-information/files/asia-pacific/korea/export/busan-pnc-export-full-container-late-gate-in-container-cut-off-rule-implementation.pdf) | `data/manual/rules/` | ‘12시간’ 기준의 사업적 근거(발표) |

## 권장

| 자료 | 받는 곳 | 넣을 폴더 | R-Delay에서 쓰는 곳 |
|---|---|---|---|
| 해양수산부_수출입 물류 플랫폼_선박 운영 예정일정 (15151744) | [15151744](https://www.data.go.kr/data/15151744/fileData.do) | `data/manual/platform_planned_schedule/` | 예상 입항·접안 시각 → 계획 대비 편차 |
| 부산항만공사_선박 입출항 집계 정보 (2019–2024) (15150637) | [15150637](https://www.data.go.kr/data/15150637/fileData.do) | `data/manual/bpa_vessel_calls/` | 예정 대비 실제 입항 지연 |
| 기상청_태풍경로 + 기상청_태풍목록 (15043568 · 15043569) | [15043568](https://www.data.go.kr/data/15043568/fileData.do) · [15043569](https://www.data.go.kr/data/15043569/fileData.do) | `data/manual/kma_typhoon/` | 태풍 장면 날짜·경로 표시 |
| 한국도로공사_노선별 방향별 일별 1시간 구간통행속도 (15043737) | [링크](http://data.ex.co.kr/portal/fdwn/view?type=VDS&num=C6&requestfrom=dataset) | `data/manual/ex_speed_1h/` | 육상층: 신항 배후 고속도로 속도 → filter_ex_speed.py로 필요한 구간만<br>**원본이 큼(GB 단위 추정). 2023-01 이후 월 파일만 받아도 충분** |

## 선택 — 시간이 남으면

| 자료 | 받는 곳 | 넣을 폴더 | R-Delay에서 쓰는 곳 |
|---|---|---|---|
| 해양수산부_수출입 물류 플랫폼_선박 입항신고 정보 (15151749) | [15151749](https://www.data.go.kr/data/15151749/fileData.do) | `data/manual/platform_arrival_reports/` | 입항 신고 시각 |
| 해양수산부_수출입 물류 플랫폼_항만 입항실적 정보 (15151756) | [15151756](https://www.data.go.kr/data/15151756/fileData.do) | `data/manual/platform_arrival_results/` | 관제 입항일시 교차검증 |
| 부산항만공사_외내항선박통합집계실시간 (15147939) | [15147939](https://www.data.go.kr/data/15147939/fileData.do) | `data/manual/bpa_vessel_monthly/` | 부두·선종별 월 척수·톤수(트래픽 기준선) |
| 부산항만공사_부산항 컨테이너 수송통계 (15055478) | [15055478](https://www.data.go.kr/data/15055478/fileData.do) | `data/manual/bpa_container_annual/` | 부두별 연간 TEU(발표 맥락) |
| 한국도로공사_구간 통행시간 (15043743) | [15043743](https://www.data.go.kr/data/15043743/fileData.do) | `data/manual/ex_travel_time/` | 콘존별 통행시간(속도 보완) |
| 해양수산부_공동활용체계_조위관측 (관측소 목록) (15153228) | [15153228](https://www.data.go.kr/dcat/metadata/15153228) | `data/manual/khoa_tide_stations/` | 부산 신항·가덕도 조위관측소 코드 확인(조위 API 15142507은 활용신청만) |

## 기상: 기상자료개방포털 CSV (API허브 대신)

API허브 키 없이 로그인만으로 받을 수 있습니다. 시간자료는 한 번에 **1년**까지 조회되므로 2023·2024·2025·2026(1/1\~11/7) 네 번 받습니다.

| 자료 | 메뉴 | 고를 것 | 넣을 폴더 |
|---|---|---|---|
| 종관기상관측(ASOS) | [기상자료개방포털](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36) 데이터 → 기상관측 → 지상 → 종관기상관측(ASOS) | 자료형태 '시간', 지점 부산·울산·창원, 요소 기온·강수·바람·기압·습도·시정 → 조회 → CSV | `data/manual/kma_asos/` |
| 해양기상부이 | [기상자료개방포털](https://data.kma.go.kr/data/sea/selectBuoyRltmList.do?pgmNo=52) 데이터 → 기상관측 → 해양 → 해양기상부이 | 자료형태 '시간', 지점 **거제도**(장기 자료) + 이수도·지심도(2025-06 추가 지점), 요소 바람·파고·파주기·기압 → 조회 → CSV | `data/manual/kma_buoy/` |

- 받은 CSV는 이름을 바꾸지 않아도 됩니다. 점검표가 폴더 안의 CSV를 셉니다.
- **선박운항정보**: `fetch_portmis.py --api vssl --probe`가 HTTP 404나 코드 12를 내면 [명세](https://www.data.go.kr/data/15006353/openapi.do)의 요청 주소를 `--base-url`로 지정하세요. 관제정보만으로도 라벨은 만들어집니다.

## Clarksons

Clarksons 데이터 요청서(`R-Delay_Clarksons_데이터요청서.pdf`)의 순서대로 받아 `data/clarksons/raw/`에 `CLK_A1_…`, `CLK_B1_…`처럼 요청서 5절의 이름으로 저장하고, `data/clarksons/meta/series_catalog.csv`에 실제 시계열 이름·번호·열람일을 적으세요. 원본은 공개 저장소·공유 링크에 올리지 않습니다. 요청서도 공개 저장소에 두지 않습니다(이 저장소에는 Clarksons 수치가 없음).

| 우선 | 항목 | 파일 이름 |
|---|---|---|
| 필수 | A1 Containership Port Congestion Index (일별) | `CLK_A1_…` |
| 필수 | A2 권역별 항만 체류 컨테이너선 선복(TEU, 일별) | `CLK_A2_…` |
| 필수 | B1 World Fleet Register 컨테이너선 선대 목록 | `CLK_B1_…` |
| 필수 | C1 컨테이너선 정기용선료(선형별, 주별) | `CLK_C1_…` |
| 필수 | series_catalog.csv (시계열 이름·번호·열람일) | `series_catalog.csv` |
| 선택 | C2 연료유 가격 VLSFO·MGO | `CLK_C2_…` |
| 선택 | D1 Sea/net 부산 기항 이력 | `CLK_D1_…` |
| 선택 | D2 SIN Ports: Busan | `CLK_D2_…` |
| 선택 | E1 SCFI·CCFI | `CLK_E1_…` |
| 선택 | E2 컨테이너 물동량(연간) | `CLK_E2_…` |

## API 활용신청 (자동 수집용, 오늘 바로)

| 사이트 | API | 우선 | 신청 주소 | `.env` 이름 |
|---|---|---|---|---|
| 공공데이터포털 | 해양수산부_관제정보 (15006354) | 필수 | https://www.data.go.kr/data/15006354/openapi.do | `DATA_GO_KR_KEY` |
| 공공데이터포털 | 해양수산부_선박운항정보 (15006353) | 선택 | https://www.data.go.kr/data/15006353/openapi.do | `DATA_GO_KR_KEY` (같은 키) |
| 기상청 API허브 | ASOS·해양기상부이 기간 조회 — 승인 대기가 있어 이번엔 생략, 위 '기상' 절의 개방포털 CSV로 대신 | 선택 | https://apihub.kma.go.kr | `KMA_APIHUB_KEY` |

공공데이터포털 개발계정은 승인 즉시 쓸 수 있지만, 키가 반영되기까지 1\~2시간 '등록되지 않은 인증키'가 나올 수 있습니다. 개발계정 하루 호출 한도는 API마다 10,000회입니다.
