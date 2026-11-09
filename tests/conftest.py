"""pytest 공통 설정: 실자료가 있어야 도는 테스트(@pytest.mark.realdata)를 고른다.

- 실자료 테스트는 realdata/cache/cntl이 있고 .env의 RDELAY_DATA_ROOT 폴더가 있을 때만 돈다.
- CI(환경변수 CI가 있으면)에서는 자료가 있어도 돌리지 않는다.
웹앱용 CI('test' 작업)에는 numpy·pytest만 있으므로 여기서 pandas를 읽지 않는다(realdata.paths는 표준 라이브러리만 쓴다).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 'pytest'로 바로 실행해도 realdata를 찾게(python -m pytest와 같게)
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "realdata: 실자료 캐시(realdata/cache)와 RDELAY_DATA_ROOT가 있어야 도는 테스트(CI에서는 건너뜀)")


def _on_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false", "no")


def realdata_skip_reason() -> str | None:
    """실자료 테스트를 건너뛸 까닭. 돌려도 되면 None."""
    if _on_ci():
        return "CI에서는 실자료 테스트를 돌리지 않음"
    if not (ROOT / "realdata" / "cache" / "cntl").exists():
        return "실자료 캐시 없음(realdata/cache/cntl, python -m realdata.io 먼저)"
    try:
        from realdata.paths import data_root
        data_root()
    except SystemExit as e:  # RDELAY_DATA_ROOT가 없거나 그 폴더가 없음
        return f"실자료 폴더 없음: {e}"
    except (ImportError, OSError) as e:
        return f"실자료 경로를 확인하지 못함: {e}"
    return None


def pytest_collection_modifyitems(config, items):
    marked = [it for it in items if it.get_closest_marker("realdata")]
    if not marked:
        return
    reason = realdata_skip_reason()
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for it in marked:
            it.add_marker(skip)
