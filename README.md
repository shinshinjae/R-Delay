# R-Delay — 지연이 번지기 전에, 고객 화물을 지킵니다
## 2026 동남권 멀티도메인 모빌리티 비즈니스 해커톤 출품작
#### 개인부문 · 중소 포워더를 위한 해상–육상–항공 연쇄지연 조기경보와 반출입 시각 결정

![R-Delay](./image/cover.jpg)

---
### 개발 기간
- 기획·예선: 2026.10 (참가신청서·종합설계서, 합성 시제품)
- 본선판 v2.0: 2026.10 – 2026.11 (배차 담당자 화면·브리핑 4종·기사님 화면, 본선 시연용)
- 실자료 백테스트: 2026.11 (부산신항 공개 자료로 되돌려 본 검증, 결과 요약은 [REPORT](REPORT.md#검증-실제-공개-자료-백테스트))
- 공개 저장소 R-Delay_ver2: 2026.11 (코드와 문서만 새로 올림, 자료는 올리지 않음)
- **본선판 v3.0: 2026.11.9** (주 사용자를 중소 포워더로 전환 — 관리 화물판·화주 안내·운송사 배차 요청·선사·터미널 협의, 시연 영상 재녹화, 팬스타 크루즈·중소 포워더 무료 시범운영 설계)
- 공개 저장소 R-Delay_ver3: 2026.11 (ver2를 이어받은 포워더판, 자료는 올리지 않음)

---

## 프로젝트 개요

R-Delay는 부산신항에서 시작된 지연이 게이트·도로를 거쳐 항공 반입까지 번지는 과정을 **감염병 확산 모형**으로 계산하는 중소 포워더용 결정 도구입니다.
중소 포워더의 수출입 담당자가 오전 10시에 한 화면에서 **지연이 번지는지(번짐 지수 R<sub>d</sub>) → 왜 그런지 → 고객 화물 중 무엇이 위험한지 → 몇 시에 반출입해야 하는지**를 보고, 화주 안내·운송사 배차 요청·사내 보고·선사·터미널 협의 문서까지 바로 만듭니다.

**웹 데모 → https://shinshinjae.github.io/R-Delay/** — 설치·가입 없이 PC·휴대폰 브라우저에서 바로 실행됩니다. 계산 엔진(Python)도 브라우저 안에서 돌아가고, 한 번 열면 인터넷이 끊겨도 열립니다.

| 포워더 | 기사님(운송사 경유) | 분석 화면 | 수리모형 | 실자료 검증 |
|:---:|:---:|:---:|:---:|:---:|
| [관리 화물판·브리핑](https://shinshinjae.github.io/R-Delay/?s=terminal_failure&c=ulsan_export) | [기사님 화면](https://shinshinjae.github.io/R-Delay/driver.html?s=terminal_failure&c=ulsan_export&a=1) | [분석·근거](https://shinshinjae.github.io/R-Delay/analysis.html?s=terminal_failure) | [작동 원리·검증](https://shinshinjae.github.io/R-Delay/method.html) | [공개 자료 백테스트(보고서)](REPORT.md#검증-실제-공개-자료-백테스트) |

> 합성 데이터 개념검증(TRL 3)입니다. 화면의 수치는 공개 보도로 확인한 사건 유형을 본뜬 가상 자료로 계산했으며, 특정 터미널·기업의 자료나 판정이 아닙니다(터미널 이름은 ‘가상 자동화 터미널 A’).
>
> **실제 공개 자료로 되돌려 본 결과**(공개 실자료 후향 검증, 실시간 운영 검증 아님): 평가 기간(2025-07-01 \~ 2026-10-29, 판단 시각 5,821개)에 ‘앞으로 24시간 안에 12시간 넘게 기다릴 컨테이너선이 오는가’를 맞힌 정도(PR-AUC)는 R-Delay 0.407, 지속성(지금 정박지 대기) 0.387, 기후값 0.291입니다. 기후값보다는 확실히 낫지만 지속성과는 차이가 없고, 대기 에피소드 81개 중 63개는 시작 전 72시간 안에 경보가 없었습니다. 앱의 확산 경보(P(R<sub>d</sub>&gt;1) ≥ 0.5)는 실자료에서 한 번도 울리지 않았고(P(R<sub>d</sub>&gt;1) 최대 0.0019), 스케줄 기준선에 R-Delay 상태를 더하면 조금 나아집니다(PR-AUC 차이 95% 구간 +0.003–+0.066).
>
> 공개 저장소(R-Delay_ver3)에는 코드와 문서만 있습니다. 원자료·캐시와 실자료에서 나온 결과 파일·그림·점검 보고서는 올리지 않았고(로컬에만 보관), 이 문서의 실자료 숫자는 그 결과 파일에서 옮긴 요약값입니다.
>
> → 자세한 결과: [기술 보고서의 해당 절](REPORT.md#검증-실제-공개-자료-백테스트)

---

## 전체 개발 구조

![전체 개발 구조](./image/architecture.png)

## 시연 영상

![시연 영상](./image/demo.gif)

본선판 v3.0 포워더 화면(약 65초): 관리 화물 확인 → 근거 → 출발 시각 변경 → 운송사 배차 요청·기사님 화면 → 화주 사전 안내 → 사내 보고·선사·터미널 협의 → 상황별 장면. 라이브 데모: https://shinshinjae.github.io/R-Delay/

---

## 핵심 기능

![주요 기능 1 — 연쇄지연 경보 · 관리 화물판](./image/feature_1_board.jpg)
![주요 기능 2 — 반출입 시각 추천 · 근거](./image/feature_2_depart.jpg)
![주요 기능 3 — 화주 사전 안내문 자동 작성](./image/feature_3_briefing.jpg)
![주요 기능 4 — 운송사 배차 요청 · 기사님 화면](./image/feature_4_driver.jpg)
![주요 기능 5 — 선사·터미널 협의 · 사내 보고](./image/feature_5_port.jpg)

---

## 사업화 방향 (본선판 v3.0)

| 순위 | 고객 | 요금(가설) | 산식 |
|---|---|---|---|
| 1 | B2G(부산항만공사·부산시·해수부) | 연 5천만–8천만 원(1년 차 실증 과제 → 효과 확인 뒤 관측망 라이선스) | 연 12시간 초과 대기 약 150척 × 대기 1건 3,142만 원 ≈ 47억 원 중 약 1.1–1.7%(가정) |
| 2 | **중소 포워더(앱의 주 사용자)** | 월 10만 원 + 경보가 울린 달 B/L당 2.5만 원 | 60FEU × 지연 5% × 건당 손실 40만 원 × 피함 30% × 25% |
| 3 | 부산신항 출입 중소 운송사 | 차량 10대 기준 월 40만 원 | 10대 × 월 4시간 덜 대기 × 대기료 4만 원/시간 × 25% |
| 4 | 부울경 화주 | 포워더 안내는 무료 · 리포트 B/L당 1,500원 | 건당 손실 40만 원 × 5% × 30% × 25% |

- 손실 단가: 40ft 롤오버(다음 배 +7일) 약 40만 원(13.5만–71.5만 원, 선사 공표 요율·지체상금률로 조립한 추정), 항공 대체 시 약 2.3억 원(10톤), 트럭 대기 1시간 4만 원(2026 안전운임, 부두 안 1시간 초과분), 선박 대기 1건 3,142만 원\~(공개 정기용선료). 평균 손실을 공표한 공식 조사는 없어 물량·확률·회피율·‘피한 손실의 25%’는 가정이며 시범운영에서 확정합니다.
- **무료 시범운영(12주, 섀도 운영)**: 제안 단계(참여 미확정). 팬스타 미라클호(부산–오사카) 화물 + 중소 포워더 3–5곳 + 부산항만공사·부산시. 돈을 받기 전에 아이디어가 현장에서 맞는지와 무엇을 고칠지 확인합니다. 자세한 내용은 [REPORT의 누가 왜 쓰나](REPORT.md#누가-왜-쓰나).

---

## 기술 스택

| 분야 | 기술 |
|------|------|
| Web App | HTML · CSS · JavaScript(빌드 없음), PWA(서비스 워커 오프라인 캐시·홈 화면 설치), SVG 차트 |
| Math Engine | Python — 물동량 보정 다층 SIS, 번짐 지수 R<sub>d</sub>, 입자필터·평활(음이항+베타-이항), 시간가변 선석 대기열(전진방정식), 기회제약 배차 |
| In-browser Runtime | Pyodide 0.27.2 · NumPy, Web Worker에서 실행(입력값 외부 전송 없음) |
| AI Module | 릿지 회귀 f<sub>ω</sub> — 풍속·파고·주말·장비 상태 → 전파율 사전평균(보조 모듈, 실자료 백테스트에서는 효과가 없어 끔) |
| Briefing | 화주 안내 · 배차 요청(협력 운송사, 기사님 링크 포함) · 사내 보고 · 선사·터미널 협의 자동 작성, 복사·공유·인쇄·저장 |
| Data Kit | Python — PORT-MIS 관제정보 수집, ‘선박 대기 12시간 초과’ 라벨, 자료 점검표 |
| Backtest | Python(pandas·scikit-learn·matplotlib) — 부산신항 공개 자료 후향 검증(`realdata/`): 기준선 S0–S3, 실자료 R-Delay(해상·육상 두 층), 평가 기간 1회 계산. 결과 파일·그림은 로컬에만 둠(저장소에 없음) |
| Test | pytest 70개(엔진 46, 실자료 백테스트 코드 23: 가짜 자료 단위 시험 20·실자료 누설 검사 3(비공개 실자료 캐시가 있어야 돌고 CI에서는 건너뜀), 저장소에 자료 파일이 올라가지 않았는지 확인 1), Node test 39개(화면 문구·브리핑 규칙), 합성 세계 60개 × 5개 장면 검증, 화면 파일 버전 점검, 데이터 키트 자체 시험 20개 |
| Deploy | GitHub Pages (`/docs`), GitHub Actions (CI) |

---

## 주요 구현 코드

![번짐 지수 — 물동량 보정 다층 SIS](./image/code_1_sis.png)
![입자필터 · 학습형 전파율](./image/code_2_filter.png)
![기회제약 배차 — 놓칠 확률 상한](./image/code_3_dispatch.png)

---

## 향후 구현 계획

![향후 구현 — 공개 데이터로 실자료 백테스트](./image/roadmap_data.png)

그림은 계획 당시의 로드맵입니다. ③ 실자료 백테스트는 공개 실자료 후향 검증(실시간 운영 검증 아님)으로 마쳤지만 계획과 다릅니다. 기간은 학습 2023-01-08 \~ 2024-12-31, 검증 2025-01-01 \~ 2025-06-30, 평가 기간 2025-07-01 \~ 2026-10-29로 나눴고, 공지 반영 ETA 규칙은 공개 자료에 도착 예정 신고 시각이 없어 비교하지 못했습니다(대신 기후값·지속성·스케줄 등 기준선과 비교, 기후값 대비 Brier 기술점수는 R-Delay 확률 0.063). 결과는 [REPORT](REPORT.md#검증-실제-공개-자료-백테스트)에 있습니다.

---

## 실행 방법

<details>
<summary><b>웹 앱</b> (설치·빌드 없음)</summary>

```bash
python3 -m http.server -d docs 8000      # http://localhost:8000 에서 열기
```

- 장면과 화물은 주소로 고릅니다: `?s=<장면>&c=<화물>` (예: `?s=terminal_failure&c=ulsan_export`)
  - 장면: `base` · `terminal_failure` · `typhoon_diversion` · `holiday_surge` · `epidemic` · `h2_trucks`
  - 화물: `import_factory` · `ulsan_parts` · `sea_air` · `ulsan_export`
- 기사님 화면: `driver.html?s=terminal_failure&c=ulsan_export&a=1` (`a=1`은 시간 변경을 적용한 화면)
- 화면 파일(css·js)을 고치면 `docs/sw.js`의 `V`와 모든 `?v=` 값을 함께 올린 뒤 `node scripts/check-assets.mjs --write`를 실행합니다(오프라인 캐시가 옛 파일과 섞이지 않게).
- 올리는 방법과 GitHub Pages 설정은 [GUIDE_GITHUB.md](GUIDE_GITHUB.md)에 있습니다.

</details>

<details>
<summary><b>Python 엔진·검증</b> (Python 3.12 이상, Node.js 22 이상)</summary>

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q                     # 엔진 46개 + 저장소 자료 파일 확인 1개(실자료 백테스트 코드 23개는 requirements-backtest.txt 설치 시, 그중 실자료 누설 검사 3개는 비공개 실자료 캐시가 있을 때만)
node --test tests/*.test.mjs             # 화면 문구·브리핑 규칙 39개
node scripts/check-assets.mjs            # 화면 파일 버전 점검
python3 scripts/train_ai.py              # 학습형 전파율 모듈 재학습(약 2분) → docs/data/ai_model.json
python3 scripts/build_data.py            # 6개 장면 미리 계산(약 4초) → docs/data/precomputed.json
python3 scripts/validate.py              # 합성 세계 검증(8코어 약 1분) → docs/data/validation.json
```

엔진만 쓸 때(브라우저와 같은 코드):

```python
import sys, json; sys.path.insert(0, "docs/py")
from rdelay import api
model = json.load(open("docs/data/ai_model.json"))
out = api.run({"scenario": "terminal_failure", "cargo": "ulsan_parts", "epsilon": 0.05}, ai_model=model)
print(out["now"]["alarm"], out["now"]["cause"], out["cargo"]["ulsan_parts"]["decision"]["message"])
```

시연 영상은 `scripts/demo/`에서 다시 만들 수 있습니다(Playwright·ffmpeg).

</details>

<details>
<summary><b>공개 데이터 수집 키트</b> (실자료 백테스트 준비)</summary>

```bash
cd data-kit
pip install -r requirements.txt
python selftest.py                       # 가짜 서버로 전 과정 시험(인터넷·인증키 불필요) → 20/20 통과
cp .env.example .env                     # 공공데이터포털 인증키 입력
python fetch_portmis.py --probe --start 2023-01-02 --end 2023-01-02   # 관제정보 제공 기간 확인
python run_all.py --start 2023-01-01     # 관제정보 수집 → 대기 라벨 → 점검표(하루 호출 한도마다 이어서)
```

화면별 단계 안내는 [data-kit/R-Delay_공개데이터_확보가이드.pdf](data-kit/R-Delay_공개데이터_확보가이드.pdf)에 있습니다. Clarksons 자료 요청서는 공개 저장소에 두지 않았습니다(이 저장소에는 Clarksons 수치가 없음). 받은 자료(`data-kit/data/`)와 인증키(`.env`)는 저장소에 올리지 않습니다.

</details>

<details>
<summary><b>실자료 백테스트</b> (자료가 있을 때)</summary>

원자료는 저장소에 없습니다. 위 수집 키트와 확보 가이드로 받은 공개 자료 폴더(그 아래 `public_data/`에 `portmis/`·`derived/`·`manual/`)를 저장소 루트 `.env`에 `RDELAY_DATA_ROOT=<자료 폴더>`로 적은 뒤 차례로 실행합니다. 검증 규칙과 변경 이력은 [realdata/PROTOCOL.md](realdata/PROTOCOL.md)에 있습니다.

결과 파일은 저장소에 없습니다. 원자료가 있으면 아래 명령으로 로컬에서 다시 만들 수 있습니다. 명령이 만드는 `realdata/cache/`(읽은 자료·중간 결과), `realdata/results/`(결과 파일), `realdata/reports/`(그림·점검 보고서), `local_only/`(`realdata.audit`이 만드는 터미널 익명 대응표·실제 시설명·Clarksons 점검 — 외부 공개 금지), `presentation/`(발표 그림)은 모두 로컬 전용이며 `.gitignore`로 올리지 않습니다(공개하지 않음).

```bash
python3 -m pip install -r requirements.txt -r requirements-backtest.txt
python3 -m realdata.io                               # 자료 읽기 → realdata/cache/
python3 -m realdata.audit                            # 자료 점검 → realdata/reports/data_audit.md, realdata/results/audit.json, local_only/(터미널 대응표·시설명·Clarksons 점검, 외부 공개 금지)
python3 -m realdata.baselines                        # 기준선 S0~S3(학습·검증 기간) → realdata/results/baselines.json
python3 -m realdata.rdelay_real calibrate            # 실자료 모수 보정(학습 기간) → realdata/results/rdelay_params.json
python3 -m realdata.rdelay_real run --period all     # 판단 시각마다 R-Delay 실행 → realdata/cache/rdelay_all_W24_P512.parquet
python3 -m realdata.ai_real --rdelay realdata/cache/rdelay_all_W24_P512.parquet   # f_ω 실자료 재학습 기록 → realdata/results/ai_model_real.json (build_backtest 입력, 로컬 전용)
python3 -m realdata.evaluate select --rdelay realdata/cache/rdelay_all_W24_P512.parquet   # 검증 기간에서 점수·설정 고르기
python3 -m realdata.evaluate final --rdelay realdata/cache/rdelay_all_W24_P512.parquet    # 평가 기간 1회 계산(기록이 있으면 멈춤)
python3 -m realdata.figures                          # 그림 → realdata/reports/fig_*.png
python3 -m realdata.report                           # 비교표 → realdata/results/compare.md
python3 -m realdata.cases                            # 사례 3개 → realdata/results/cases.json, realdata/reports/fig_case_*.png
python3 scripts/build_backtest.py                    # 결과 요약 → realdata/results/backtest.json(로컬, 발표·문서 숫자용)
python3 -m realdata.run_all --dry-run                # 새 자료가 들어왔을 때: 무엇이 바뀔지만 보기
python3 -m realdata.run_all                          # 새 자료 반영을 한 번에(모형·설정 고정, 평가 기간만 늘리고 이유를 기록)
```

- 평가 기간은 한 번만 계산합니다. 다시 계산하려면 `evaluate final`에 `--rerun-reason "이유"`를 붙여야 하고, 이유가 로컬 파일 `realdata/results/test_runs.json`에 쌓입니다.
- `realdata.ai_real`(f<sub>ω</sub> 실자료 재학습)은 건너뛰지 않습니다. f<sub>ω</sub>는 효과가 없어 껐지만, 그 기록(`realdata/results/ai_model_real.json`, 로컬 전용)이 `scripts/build_backtest.py`의 입력이라 없으면 결과 요약을 만들지 못하고 멈춥니다.

</details>

<details>
<summary><b>폴더 구조</b></summary>

```
R-Delay_ver3/
├─ docs/               GitHub Pages가 서비스하는 웹 앱 전체(빌드 없음)
│  ├─ index.html       포워더 화면(관리 화물판 + 브리핑 4종)
│  ├─ driver.html      기사님 화면
│  ├─ analysis.html    분석 화면(경보·원인·전망·검증 요약)
│  ├─ method.html      작동 원리·검증(수식·판정 규칙·검증 표)
│  ├─ assets/          화면 css·js, 글꼴(OFL), 아이콘
│  ├─ data/            미리 계산 결과 · AI 모듈 계수 · 합성 세계 검증 결과(모두 합성 자료)
│  ├─ py/rdelay/       Python 엔진: sis · volume · ai · engine · queue · cargo · stats · config · api
│  └─ sw.js            오프라인 캐시(서비스 워커)
├─ realdata/           실자료 백테스트(검증 규칙 PROTOCOL.md · 코드). 원자료·캐시(cache/)·결과 파일(results/)·그림과 점검 보고서(reports/)는 로컬에만 둠
├─ scripts/            학습 · 미리 계산 · 검증 · 화면 캡처 · 시연 영상(demo/)
│  └─ build_backtest.py  실자료 결과 요약 → realdata/results/backtest.json(로컬, 발표·문서 숫자용)
├─ tests/              엔진 · 실자료 백테스트 코드(가짜 자료) · 저장소 자료 파일 확인(pytest) · 화면 문구·브리핑 규칙(node --test)
├─ data-kit/           공개 데이터 수집 키트 · 데이터 확보 가이드
├─ image/              README 이미지
├─ MODEL.md            모형 정의·가정·증명
├─ REPORT.md           기술 보고서(문제 · 시연 장면별 결과 · 합성 검증 · 실자료 백테스트 · 로드맵 · 한계 · 출처)
├─ GUIDE_GITHUB.md     GitHub 업로드 · Pages 설정 · 확인 방법
└─ .github/workflows/ci.yml   테스트 자동 실행
```

</details>

## 데이터·인용 고지

- 화면의 물동량·처리능력·지연·비용·승인 확률·충전소 조건은 공개 보도로 확인한 사건 유형을 본뜬 **합성 가정치**입니다. ‘작동 원리·검증’의 검증 표는 필터가 참 구조·정적 모수를 아는 조건의 합성 세계 결과이며 실증 성능이 아닙니다. 실제 공개 자료로 되돌려 본 결과는 [REPORT](REPORT.md#검증-실제-공개-자료-백테스트)에 따로 있으며, 공개 실자료 후향 검증(실시간 운영 검증 아님)이고 해상·육상 두 층만 썼습니다(항공 자료 없음). 원인 판정은 모형상 배분이며 귀책 판단에 쓰지 않습니다. 의사결정 지원용이며 최종 판단과 책임은 사용자에게 있습니다.
- 브리핑 문서의 회사·담당자·연락처·컨테이너 번호(RDLU…)는 자리표시자·가상 값입니다.
- 문제 정의에 쓴 공개 보도와 지역 연계 근거는 [REPORT.md의 출처](REPORT.md#출처)에, 모형 상세는 [MODEL.md](MODEL.md)에 있습니다.
- 실자료 백테스트와 자료 점검에 쓴 공개 자료: 해양수산부 PORT-MIS 관제정보·선박운항정보, 부산항만공사 집계, 해양수산부 수출입 물류 플랫폼(이상 공공데이터포털), 기상청 ASOS·해양기상부이(기상자료개방포털)와 태풍경로(공공데이터포털)(기상청 자료는 공공누리 출처표시), 한국도로공사 구간통행속도(고속도로 공공데이터 포털). 데이터 ID와 링크는 [REPORT.md의 출처](REPORT.md#출처)에 있습니다. 공개 저장소(R-Delay_ver3)에는 코드와 문서만 있습니다. 원자료·캐시와 실자료에서 나온 결과 파일·그림·점검 보고서는 올리지 않았고(로컬에만 보관), 문서의 실자료 숫자는 그 결과 파일에서 옮긴 요약값입니다([공개 범위](REPORT.md#자료와-정답)). 선박 이름·호출부호·터미널은 넣지 않습니다. Clarksons Research 자료는 학술 이용 조건 때문에 저장소에 넣지 않습니다.
- [P1] 선행연구 — *A mobility-informed SIR model for regional transmission dynamics: Evidence from subway mobility data in Korea, 2016–2023*(한국자료분석학회지 2026년 28권 5호 게재). 이동량 보정 계수 구조를 물동량·처리능력으로 옮겼습니다.
- 라이브러리·글꼴: [Pyodide](https://pyodide.org) 0.27.2(MPL-2.0), [NumPy](https://numpy.org)(BSD-3-Clause), [KaTeX](https://katex.org) 0.16.11(MIT), Pretendard·Barlow Semi Condensed(SIL OFL). 코드: [MIT License](LICENSE).

---

## 팀원

| 이름 | 이메일 |
|---|---|
|신재완|sjw6258@g.kmou.ac.kr|
|명정효|karl0126@naver.com|
|김진현|jinh7205@naver.com|
|김태훈|kth060420@gmail.com|
