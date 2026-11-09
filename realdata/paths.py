"""경로·.env 읽기. 경로는 코드에 박지 않고 저장소 루트 .env의 RDELAY_DATA_ROOT에서 읽는다."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "realdata"
CACHE = PKG / "cache"          # gitignore: 읽은 원자료(parquet)
REPORTS = PKG / "reports"
RESULTS = PKG / "results"
LOCAL_ONLY = REPO / "local_only"  # gitignore: Clarksons 계산·이름이 남는 상세 표


def console_safe() -> None:
    """콘솔이 CP949여도 한글·기호 출력 때문에 멈추지 않게 한다."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def load_env(path: Path = REPO / ".env") -> None:
    """KEY=VALUE 형식의 .env를 환경변수로 넣는다(이미 있는 값은 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def data_root() -> Path:
    """RDelay_Data/Raw_data 폴더."""
    load_env()
    v = os.environ.get("RDELAY_DATA_ROOT", "").strip()
    if not v:
        raise SystemExit("[중단] 저장소 루트 .env에 RDELAY_DATA_ROOT=<RDelay_Data/Raw_data 폴더>를 적으세요.")
    p = Path(v).expanduser()
    if not p.is_dir():
        raise SystemExit(f"[중단] RDELAY_DATA_ROOT 폴더가 없습니다: {p}")
    return p


def public_dir() -> Path:
    """공개 자료(derived·portmis·manual). data-kit의 RDELAY_DATA_DIR와 같은 폴더."""
    load_env()
    v = os.environ.get("RDELAY_PUBLIC_DIR", "").strip()
    return Path(v).expanduser() if v else data_root() / "public_data"


def clarksons_dir() -> Path:
    """Clarksons 원자료(A1_…·A2_…·C1_…·E1_…·D2_E2_…·meta). 외부 공개 금지."""
    load_env()
    v = os.environ.get("RDELAY_CLARKSONS_DIR", "").strip()
    return Path(v).expanduser() if v else data_root()
