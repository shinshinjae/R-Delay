"""R-Delay 데이터 수집 키트 공통 함수: .env 읽기, HTTP 재시도, 경로."""
from __future__ import annotations

import http.client
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("RDELAY_DATA_DIR", ROOT / "data"))

for _s in (sys.stdout, sys.stderr):  # 콘솔 인코딩이 CP949여도 ✅ 같은 글자 때문에 멈추지 않게
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

try:  # macOS python.org 설치본처럼 인증서 묶음이 없는 경우 대비
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi가 없으면 시스템 기본값
    _CTX = ssl.create_default_context()
_HTTP_NOTICE = [False]


class HttpFail(RuntimeError):
    """4xx 응답. 본문 앞부분을 함께 들고 다닌다."""

    def __init__(self, code: int, body: bytes):
        super().__init__(f"HTTP {code}")
        self.code, self.body = code, body

    def text(self, n: int = 300) -> str:
        return self.body[:n].decode("utf-8", "replace").strip()


def load_env(path: Path = ROOT / ".env") -> None:
    """KEY=VALUE 형식의 .env를 읽어 환경변수로 넣는다(이미 있는 값은 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val or val.startswith("여기에"):
        sys.exit(f"[중단] {name}가 비어 있습니다. .env.example을 복사해 .env를 만들고 인증키를 넣으세요(README 1단계).")
    return val


def _open(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "rdelay-data-kit/1.0"})
    ctx = _CTX if url.startswith("https") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def http_get(url: str, params: dict, timeout: int = 40, retries: int = 4, backoff: float = 2.0) -> bytes:
    """GET 요청. 네트워크 오류·5xx·429는 지수 백오프로 재시도, 그 밖의 4xx는 HttpFail.

    인증서 확인에 실패하면(정부 사이트 중간 인증서 누락 등) 같은 주소를 http로 한 번 더 시도한다.
    공공데이터포털 공식 예시도 http 주소를 쓴다. 인증키는 화면에 찍지 않는다.
    """
    q = urllib.parse.urlencode(params, safe=":")
    full = f"{url}?{q}"
    last = None
    for i in range(retries):
        try:
            return _open(full, timeout)
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                try:
                    body = e.read()
                except Exception:
                    body = b""
                raise HttpFail(e.code, body) from None
        except urllib.error.URLError as e:
            last = e
            if isinstance(e.reason, ssl.SSLCertVerificationError) and full.startswith("https://"):
                if not _HTTP_NOTICE[0]:
                    print("  · 인증서 확인 실패 → 같은 주소를 http로 받습니다(공공데이터포털 공식 예시와 같은 방식).")
                    _HTTP_NOTICE[0] = True
                full = "http://" + full[len("https://"):]
                continue
        except (TimeoutError, ConnectionError, http.client.HTTPException) as e:
            last = e
        time.sleep(backoff ** i)
    host = urllib.parse.urlsplit(url).netloc
    raise RuntimeError(f"요청 실패({retries}회 재시도): {host} — {last}")


def daterange(start: str, end: str):
    """'2023-01-01'~'2023-01-31'을 하루씩 돌려준다(끝 날짜 포함)."""
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    d1 = dt.date.fromisoformat(end)
    while d0 <= d1:
        yield d0
        d0 += dt.timedelta(days=1)
