# GitHub에 올리고 앱 확인하기

> 이 저장소(jaewansh/R-Delay_ver3)의 데모 주소: https://shinshinjae.github.io/R-Delay/

이 저장소는 웹 앱이 빌드 과정 없이 `docs/` 폴더에 그대로 들어 있습니다. GitHub에 올린 뒤 **Pages 설정을 한 번**만 하면 누구나 주소로 앱을 열 수 있습니다. 서버는 필요 없고, 계산 엔진(Python)도 브라우저 안에서 실행됩니다.

## 1. 파일 올리기

처음 한 번은 GitHub에 새 저장소를 만들어 올립니다. 명령어로는 아래 ‘처음 올릴 때’를, GitHub Desktop은 방법 B를, 웹 브라우저는 방법 C를 따릅니다. 그 뒤로 고친 파일은 방법 A나 B로 올립니다.

### 처음 올릴 때(새 저장소)

GitHub에 `jaewansh/R-Delay_ver3`가 아직 없을 때 한 번만 합니다.

1. 빈 **공개(Public)** 저장소를 만듭니다. https://github.com/new 에서 이름을 `R-Delay_ver3`로 하고 README·.gitignore·license는 추가하지 않습니다. 2번의 `gh repo create`를 쓰면 이 단계는 건너뜁니다.
2. 저장소 폴더 맨 위에서 커밋한 뒤 올립니다.

   ```bash
   cd R-Delay_ver3
   git add -A                                            # .gitignore가 자료·캐시·.env를 뺍니다
   python3 -m pytest -q tests/test_no_data_in_repo.py    # 올릴 파일에 자료가 없는지 확인(requirements.txt 설치 후)
   git commit -m "R-Delay_ver3: 코드와 문서"
   # (가) GitHub CLI: 공개 저장소를 만들면서 바로 올리기
   gh repo create jaewansh/R-Delay_ver3 --public --source=. --remote=origin --push
   # (나) 1번에서 웹으로 만든 빈 저장소에 올리기
   git remote add origin https://github.com/shinshinjae/R-Delay.git && git push -u origin main
   ```

3. 저장소의 **Settings → Pages**에서 `main` 브랜치의 `/docs` 폴더를 고르고 저장합니다([2절](#2-github-pages-켜기)).

### 방법 A. git 명령어 — GitHub 저장소가 이미 연결된 폴더일 때

`git remote -v`에 `origin  https://github.com/shinshinjae/R-Delay.git`이 나오는 폴더라면 푸시만 하면 됩니다.

```bash
cd R-Delay_ver3
git push origin main --follow-tags
```

새로 고친 파일을 올릴 때는 다음 순서입니다.

```bash
git clone https://github.com/shinshinjae/R-Delay.git
cd R-Delay_ver3
# 파일을 바꾼 뒤
git add -A
git commit -m "R-Delay 수정"
git push
```

GitHub 로그인 창이 뜨면 저장소 주인 계정으로 로그인합니다.

### 방법 B. GitHub Desktop

1. https://desktop.github.com 에서 설치하고 GitHub 계정으로 로그인합니다.
2. **File → Add local repository**에서 `R-Delay_ver3` 폴더를 고릅니다. GitHub Desktop은 `.gitignore`를 따르므로 작업 폴더를 그대로 써도 됩니다.
3. 바뀐 파일이 있으면 왼쪽 아래 Summary를 적고 **Commit to main**을 누릅니다.
4. 오른쪽 위 **Push origin**을 누릅니다.
   - 아직 GitHub에 없는 새 저장소라면 이 자리에 **Publish repository**가 나옵니다. 이름을 `R-Delay_ver3`로 두고 **Keep this code private**의 체크를 끈 뒤 올립니다. 무료 계정에서는 공개 저장소여야 Pages가 켜집니다.

### 방법 C. 웹 브라우저로 끌어다 놓기 — 새 저장소일 때만

작업 폴더에서 바로 끌어다 놓으면 안 됩니다. 작업 폴더에는 올리면 안 되는 로컬 파일(`realdata/cache/`, `realdata/results/`, `realdata/reports/`, `local_only/`, `presentation/`, `realdata/WORKLOG.md`, `data-kit/data/`, `.env` 등)이 함께 있고, 웹 업로드는 `.gitignore`를 따르지 않습니다. git이 추적하는 파일만 새 폴더에 복사한 뒤 그 복사본에서만 올립니다.

1. 저장소 폴더 맨 위에서 올릴 파일만 새 폴더 `../R-Delay_ver3_upload/`에 복사합니다(그 폴더가 이미 있으면 먼저 지웁니다).

   ```bash
   # 첫 커밋 전: 스테이징한 파일을 그대로 복사
   git add -A
   git checkout-index -a --prefix=../R-Delay_ver3_upload/
   # 커밋한 뒤: 마지막 커밋을 압축했다가 풀기
   git archive --format=zip -o ../R-Delay_ver3.zip HEAD
   unzip ../R-Delay_ver3.zip -d ../R-Delay_ver3_upload
   ```

   - 복사본에는 `git ls-files`에 나오는 파일만 들어갑니다. `git ls-files`에 없는 파일은 로컬 파일이므로 올리지 않습니다.
2. 새 저장소 화면에서 **uploading an existing file**을 누릅니다.
3. 복사본 `R-Delay_ver3_upload` 폴더 **안의** 항목을 모두 골라 업로드 영역에 끌어다 놓습니다.
   - 대상: `docs`, `realdata`, `scripts`, `tests`, `data-kit`, `image`, `.github`, `README.md`, `REPORT.md`, `MODEL.md`, `GUIDE_GITHUB.md`, `LICENSE`, `requirements.txt`, `requirements-backtest.txt`, `.gitignore`
   - Chrome·Edge에서는 폴더째 끌어다 놓으면 폴더 구조가 그대로 올라갑니다.
   - 맥 Finder는 `.github`·`.gitignore`를 숨깁니다. Cmd+Shift+.(마침표)를 눌러 보이게 한 뒤 고릅니다.
4. **Commit changes**를 누릅니다.

> 한 번에 100개 파일까지 올릴 수 있습니다(이 저장소는 120개가 넘어 두 번에 나눠 올립니다). 웹 업로드로는 예전 파일을 지울 수 없습니다. 기존 저장소를 바꿀 때는 방법 A나 B를 쓰세요.

> 확인: 저장소 첫 화면에 `docs` 폴더가 바로 보이고 `docs/index.html`이 있어야 합니다. `R-Delay_ver3/docs/...`처럼 한 단계 더 들어가 있으면 Pages가 찾지 못합니다.

## 2. GitHub Pages 켜기

1. 저장소에서 **Settings**를 열고 왼쪽 메뉴의 **Pages**로 갑니다.
2. **Build and deployment → Source**를 **Deploy from a branch**로 둡니다.
3. **Branch**는 `main`, 폴더는 <b>`/docs`</b>를 고르고 **Save**를 누릅니다.
4. 1\~3분 뒤 같은 화면 위쪽에 <b>"Your site is live at https://&lt;GitHub아이디&gt;.github.io/R-Delay_ver3/"</b>가 나타납니다. 진행 상황은 **Actions** 탭(pages build and deployment)에서도 볼 수 있습니다.

명령어로 켜려면(GitHub CLI):

```bash
gh api -X POST "repos/<GitHub아이디>/R-Delay_ver3/pages" -f "source[branch]=main" -f "source[path]=/docs"
```

## 3. 앱 확인하기

| 무엇을 볼까 | 주소 |
|---|---|
| 배차 담당자 화면(배차판·브리핑) | `https://<GitHub아이디>.github.io/R-Delay_ver3/` |
| 장면·화물 바로 열기 | `…/R-Delay_ver3/?s=terminal_failure&c=ulsan_export` |
| 기사님 화면 | `…/R-Delay_ver3/driver.html?s=terminal_failure&c=ulsan_export&a=1` |
| 분석 화면 | `…/R-Delay_ver3/analysis.html?s=terminal_failure` |
| 작동 원리·검증 | `…/R-Delay_ver3/method.html` |

- **장면**(`s`): `base` 평시, `terminal_failure` 장비 고장, `typhoon_diversion` 태풍 우회 물량, `holiday_surge` 명절 물량, `epidemic` 감염병 결근, `h2_trucks` 수소 트럭.
- **화물**(`c`): `import_factory` 양산 공장 원자재, `ulsan_parts` 울산 완성차 부품, `sea_air` Sea&Air 인천, `ulsan_export` 울산 부품 수출.
- **오프라인**: 화면 맨 아래 상태가 ‘계산 엔진: 계산 준비됨 · 오프라인 가능’이 되면 인터넷 없이도 열리고 다시 계산됩니다. 계산 엔진(Pyodide)은 처음 한 번 인터넷으로 내려받습니다.
- **휴대폰**: 주소를 휴대폰 브라우저로 열고 공유 메뉴의 **홈 화면에 추가**를 누르면 앱처럼 실행됩니다. 발표 자료에는 주소를 QR 코드로 넣어 심사위원이 바로 열어 볼 수 있게 합니다.
- 보내는 사람(회사·담당·연락처)은 그 브라우저에만 저장되고 다른 사람에게 보이지 않습니다.

## 4. 고친 뒤 다시 올리기

- **화면 파일(css·js)을 고쳤다면** `docs/sw.js`의 `V`와 모든 `?v=` 값을 같은 새 값으로 올립니다. 그다음 `node scripts/check-assets.mjs --write`를 실행합니다. 그래야 오프라인 캐시가 옛 파일과 섞이지 않습니다.
- **엔진·장면을 바꿨다면** `python3 scripts/build_data.py`로 `docs/data/precomputed.json`을 다시 만듭니다.
- 푸시하면 **Actions**의 CI가 엔진 테스트(저장소에 자료 파일이 없는지 확인 포함), 실자료 백테스트 코드 시험(가짜 자료), 화면 규칙 테스트, 화면 파일 버전, 데이터 키트 자체 시험을 돌립니다. 1\~2분 뒤 같은 주소에 반영됩니다.
- **다른 계정·이름으로 올렸다면** 데모 주소를 쓰는 곳을 함께 바꿉니다.
  - `README.md`·`REPORT.md`·`MODEL.md`·`GUIDE_GITHUB.md`의 데모·저장소 주소
  - `docs/assets/js/board.js`의 `PUBLIC_BASE`(기사님 링크에 쓰는 공개 주소)
  - `docs/index.html`·`docs/analysis.html`·`docs/method.html`의 GitHub 저장소 링크
  - `scripts/demo/stage.html`의 주소 표시와 `qr.png`(새 주소로 다시 만듦)

## 5. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 404 페이지 | Pages 설정이 `main` / `/docs`인지, 저장소가 Public인지, 저장소 첫 화면에 `docs/index.html`이 있는지 확인하고 2\~3분 기다립니다. |
| 고친 내용이 안 보임 | 배포가 끝났는지(Actions 탭) 확인합니다. 서비스 워커가 옛 화면을 보여 줄 수 있으니 새로 고침을 두 번 하거나 Ctrl+Shift+R(맥: Cmd+Shift+R)을 누릅니다. |
| ‘조건 바꾸기’ 재계산이 안 됨 | 엔진을 처음 내려받는 중입니다. 인터넷에 연결된 상태에서 화면 맨 아래 상태가 ‘계산 준비됨’이 될 때까지 기다립니다. |
| Actions에 빨간 X | Actions 탭 로그를 엽니다. ‘Asset versions’ 단계라면 4번의 버전 올리기 절차를 따릅니다. |

## 6. 공개 범위 참고

- 코드는 [MIT License](LICENSE)입니다. 글꼴은 SIL OFL, Pyodide는 MPL-2.0입니다.
- 저장소에는 **코드와 문서만** 있습니다. 웹 앱이 쓰는 `docs/data/`는 합성 시연 자료이며, 실제 기업·터미널 자료는 없습니다.
- 실자료 백테스트의 원자료·캐시(`realdata/cache/`)와 실자료에서 나온 결과 파일(`realdata/results/`)·그림과 점검 보고서(`realdata/reports/`), `local_only/`(터미널 익명 대응표·실제 시설명·Clarksons 점검), `presentation/`(발표 그림)은 `.gitignore`로 올리지 않고 로컬에만 둡니다. 실자료 결과의 요약 숫자는 문서(README·REPORT·MODEL·realdata/PROTOCOL.md)와 웹의 ‘작동 원리·검증’ 화면(`docs/method.html`)에 글로만 적었습니다.
- 공개 데이터 수집 키트가 받은 자료(`data-kit/data/`), 인증키(`.env`), Clarksons 원자료는 `.gitignore`로 올리지 않습니다. Clarksons 학술 구독 자료는 공개 저장소·공유 링크에 올리면 안 됩니다.
- 선행연구 [P1]의 데이터·보정 결과는 포함되어 있지 않습니다.
