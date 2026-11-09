# R-Delay 데이터 수집 키트

이 키트는 승선(11/8) 전에 공개 데이터를 받아 R-Delay 백테스트 입력으로 만듭니다. 단계별 화면 안내는 `R-Delay_공개데이터_확보가이드.pdf`에 있습니다.

- 인터넷이 되는 노트북에서 실행하세요.
- 받은 자료는 `data/`에 쌓입니다.
- 여러 번 실행해도 이미 받은 것은 건너뜁니다.
- 명령은 키트 폴더에서 실행합니다. Windows는 `python`, Mac은 `python3`·`pip3`를 씁니다.

## 오늘 할 일 (순서대로)

### 0. 설치와 자체 점검 (5분)

```
pip install -r requirements.txt
python selftest.py
```

`20/20 통과`가 나오면 키트는 정상입니다. 이 점검은 가짜 서버로 하므로 인터넷도 인증키도 필요 없습니다.

### 1. 인증키 받기 (오늘 바로)

키가 실제로 쓰이기까지 1\~2시간 걸릴 수 있으니 가장 먼저 하세요.

1. **공공데이터포털 키를 받습니다.**
   - [해양수산부_관제정보](https://www.data.go.kr/data/15006354/openapi.do)에 로그인해 활용신청합니다. 자동승인입니다.
   - 마이페이지에서 일반 인증키(Decoding)를 복사합니다. Encoding 키를 넣어도 됩니다.
2. **기상자료개방포털(data.kma.go.kr)에 가입합니다.**
   - 기상청 API허브는 활용신청 뒤 승인을 기다려야 해서 이번에는 쓰지 않습니다.
   - 기상 자료(ASOS·해양기상부이)는 개방포털에서 로그인만 하면 CSV로 바로 받습니다. 방법은 `R-Delay_공개데이터_확보가이드.pdf` 5절과 `MANUAL_DOWNLOADS.md`의 '기상' 절에 있습니다.
3. **`.env` 파일을 만듭니다.**
   - `.env.example`을 `.env`로 복사합니다. Windows는 `copy .env.example .env`, Mac은 `cp .env.example .env`입니다.
   - `DATA_GO_KR_KEY`에 공공데이터포털 키를 붙여 넣습니다. `KMA_APIHUB_KEY`는 비워 둡니다.

### 2. API 자료 받기

노트북을 켜 두고 절전 모드를 끄세요.

```
python fetch_portmis.py --probe --start 2023-01-02 --end 2023-01-02   # 2023년 자료가 나오는지(이력 깊이) 먼저 확인
python fetch_portmis.py --probe     # 어제 하루만 받아 실제 값과 필요한 호출 수를 보여 줌
python run_all.py                   # 관제정보(최근 날짜부터) → (API허브 키가 있으면 기상) → 대기 라벨 → 점검표
```

2023-01-02가 0건이면 2024-01-02, 2025-01-02 순으로 확인해 기록이 나오는 해를 찾고, `python run_all.py --start 그해-01-01`로 실행합니다(없는 기간에 호출을 쓰지 않게).

- 관제정보는 개발계정 기준 **하루 10,000회**까지 호출됩니다.
- 한도에 걸리면 안내하고 멈춥니다. 다음 날 `python run_all.py`를 그대로 다시 실행하면 이어서 받습니다.
- 최근 날짜부터 받으므로, 중간에 멈춰도 최근 구간은 바로 쓸 수 있습니다.
- `--probe`가 하루치 호출 수로 전체에 며칠 걸릴지 가늠해 줍니다.

### 3. 로그인해서 직접 받을 파일 (30분\~1시간)

`MANUAL_DOWNLOADS.md`의 **필수 4개**부터 받습니다.

```
python check_inventory.py --make-dirs   # 넣을 폴더와 폴더별 안내 파일 만들기
python check_inventory.py               # 있는 것·빠진 것 정리 → data/data_inventory.md
```

부산항만공사 컨테이너 집계(15147941)는 11/5에 갱신될 예정입니다. 11/5\~7 사이에 한 번 더 받으세요. 점검표에 ⚠️로 표시됩니다.

### 4. 대기 라벨 맞추기

관제정보가 며칠치라도 생기면 바로 할 수 있습니다.

1. 관제구분과 시설명이 실제로 어떻게 적혀 있는지 봅니다.

   ```
   python build_wait_labels.py --inspect
   ```

2. `label_config.json`에서 두 정규식을 실제 표기에 맞게 고칩니다.
   - `anchorage_pattern`: 정박지
   - `newport_berth_pattern`: 신항 선석
3. 라벨을 만듭니다.

   ```
   python build_wait_labels.py
   ```

결과는 `data/derived/`에 세 파일로 나옵니다.

| 파일 | 내용 |
|---|---|
| `newport_voyages.csv` | 항차별 도착·접안 시각, 대기시간, 12시간 초과 여부 |
| `newport_waiting_2h.csv` | 2시간 단위 대기 척수, 도착 수, 접안 수 |
| `label_summary.json` | 건수·비율 요약 |

출력 끝의 '선석으로 잡힌 시설' 목록에 북항이나 감천 시설이 섞여 있으면 패턴을 다시 고칩니다.

### 5. Clarksons (구독 접속이 되는 사람)

1. Clarksons 데이터 요청서대로 받아 `data/clarksons/raw/`에 요청서 이름(`CLK_B1_…` 등)으로 저장합니다. 요청서(`R-Delay_Clarksons_데이터요청서.pdf`)는 공개 저장소에 두지 않습니다(이 저장소에는 Clarksons 수치가 없음).
2. 선박을 맞춥니다.

   ```
   python match_clarksons_ships.py
   ```

   결과는 `data/clarksons/derived/ship_teu_map.csv`(호출부호 → TEU)입니다.
3. 자동으로 맞지 않은 배를 사람이 확인합니다.
   - `unmatched_vessels.csv`를 입항 횟수가 많은 위쪽부터 봅니다.
   - 확인한 짝을 `data/clarksons/meta/manual_matches.csv`에 `clsgn,imo` 두 열로 적습니다.
   - 다시 실행하면 반영됩니다.

원본(raw)은 GitHub나 공유 링크에 올리지 않습니다. `.gitignore`에 `data/`가 들어 있습니다.

### 6. 도로 속도 (파일을 받았다면)

```
python filter_ex_speed.py
```

신항 배후 구간만 골라 `data/derived/ex_speed_busan.csv`로 저장합니다. 화면에 나오는 콘존 목록을 보고, 빠진 구간이 있으면 `--pattern`에 더합니다.

### 7. 승선 전 마무리 (11/7)

- `python check_inventory.py`를 실행해 필수 항목이 모두 ✅인지 확인합니다.
- `data/` 폴더를 팀 노트북 2대 이상과 USB에 복사합니다. 선상 인터넷은 1인 하루 2.5GB까지입니다.

## 파일 구성

| 파일 | 하는 일 | 결과 |
|---|---|---|
| `run_all.py` | 아래 수집과 가공을 차례로 실행 | `data/data_inventory.md` |
| `fetch_portmis.py` | 해양수산부 관제정보·선박운항정보 API를 하루씩 수집 | `data/portmis/{cntl,vssl}/daily/YYYYMMDD.csv` |
| `fetch_kma.py` | 기상청 API허브 ASOS·해양기상부이를 30일씩 수집 | `data/kma/{asos,buoy}/` + 합친 CSV |
| `build_wait_labels.py` | ‘선박 대기 12시간 초과’ 라벨과 2시간 대기 척수 생성 | `data/derived/` |
| `match_clarksons_ships.py` | Clarksons B1 ↔ PORT-MIS 선박 매칭 | `data/clarksons/derived/` |
| `filter_ex_speed.py` | 도로공사 1시간 속도에서 신항 배후 구간만 추출 | `data/derived/ex_speed_busan.csv` |
| `check_inventory.py` | 받은 자료 점검, 폴더·안내 파일 생성 | `data/data_inventory.md` |
| `selftest.py` | 가짜 서버로 전 과정 시험 | 화면 출력 |
| `catalog.py` | 받아야 할 자료 목록(점검표와 MANUAL_DOWNLOADS의 원본) | — |
| `label_config.json` | 라벨 판정 정규식 | — |

## 자주 나는 오류

| 메시지 | 뜻 | 할 일 |
|---|---|---|
| 등록되지 않은 인증키 (코드 30) | 키가 아직 반영되지 않았거나 잘못 복사됨 | 1\~2시간 뒤 다시 실행하고, 계속되면 키를 다시 복사 |
| 활용신청(권한)이 없습니다 (코드 20) | 그 API를 활용신청하지 않음 | 해당 API 페이지에서 활용신청 |
| 일일 호출량 초과 (코드 22) | 하루 한도에 도달 | 다음 날 같은 명령 실행(받은 날짜는 건너뜀) |
| HTTP 404 또는 코드 12 | 요청 주소(오퍼레이션)가 다름 | 명세의 요청 주소를 `--base-url`로 지정 |
| HTTP 401·403 (API허브) | 키가 틀렸거나 그 자료를 활용신청하지 않음 | API허브에서 해당 자료 활용신청 확인 |
| 인증서 확인 실패 | 정부 사이트의 인증서 문제 | 키트가 자동으로 http로 다시 시도함(공식 예시와 같은 방식) |

## 아직 확인하지 못한 것

- **관제정보 이력 깊이**: 2023년까지 거슬러 받을 수 있는지 모릅니다. 오래된 날짜가 '기록 없는 날'로 쌓이면 제공 기간 밖이라는 뜻이고, 점검표에 표시됩니다.
- **관제구분·시설명 표기**: `--inspect`로 확인합니다.
- **API 이름 두 가지**: 선박운항정보 오퍼레이션 경로(`/Info`)와 해양기상부이 API 이름(`kma_buoy2.php`)은 `--probe`로 확인합니다.
- **거제도 부이 번호**: 22104로 알려져 있지만 공식 문서로 확인하지 못했습니다. 개방포털에서는 이름(거제도)으로 고르면 됩니다.
- **도로공사 콘존명 표기**: `filter_ex_speed.py` 출력으로 확인합니다.
- **항계 밖 대기**: 입항 신고 전에 항계 밖에서 기다린 시간은 관제정보에 잡히지 않을 수 있습니다. 플랫폼 적시선박 자료나 Clarksons Sea/net(D1)으로 교차검증합니다.

## 출처 표기

- 해양수산부·부산항만공사 공공데이터(공공데이터포털)
- 기상청(공공누리)
- 한국도로공사
- Clarksons Research(학술 이용 조건)

발표 자료에는 출처를 적습니다. Clarksons 인용 문구는 요청서 6절에 있습니다(요청서는 공개 저장소에 없음).
