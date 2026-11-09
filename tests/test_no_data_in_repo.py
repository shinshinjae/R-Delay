"""공개 저장소에 데이터가 들어가지 않았는지 본다. pytest 말고는 표준 라이브러리만 쓴다(웹앱용 CI 'test' 작업에서도 돈다).

규칙의 출처: 사용자 결정(2026-11-07) "GitHub에는 데이터를 올리지 않는다."
공개 저장소(R-Delay_ver2, 이를 이어받은 R-Delay_ver3)에는 코드와 문서만 둔다. 원자료·캐시, 실자료에서 나온 결과 JSON·그림·점검 보고서·집계는
로컬에만 두고 .gitignore로 막는다. 이 테스트는 git이 추적하는 파일(git ls-files: 커밋됐거나 스테이징된 파일)만 본다.
- docs/data/에는 화면용 합성 자료 3개(ai_model.json·precomputed.json·validation.json)만 둔다.
- 경로에 'results'나 'reports' 폴더가 있으면 막는다(realdata/·realdata_ship/ 등 실자료 결과·보고서 자리).
git이 없거나, git 작업 폴더가 아니거나, 추적하는 파일이 없으면 건너뛴다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 로컬에만 두는 폴더(사용자 결정 2026-11-07): 실자료 결과·점검 보고서·캐시, Clarksons 계산, 발표 산출물, 데이터 키트가 받은 자료
LOCAL_ONLY_DIRS = ("realdata/results/", "realdata/reports/", "realdata/cache/", "realdata_ship/cache/", "local_only/", "presentation/",
                   "data-kit/data/")
LOCAL_ONLY_FILES = ("docs/data/backtest.json",)  # 예전 '실자료 검증' 화면용 집계. 지금은 realdata/results/backtest.json(로컬 전용)
DOCS_DATA_OK = {"docs/data/ai_model.json", "docs/data/precomputed.json", "docs/data/validation.json"}  # 합성 세계·화면용 자료
LOCAL_ONLY_SEGMENTS = {"results", "reports"}  # 이 이름의 폴더는 어디에 있든 실자료 결과·보고서 자리로 본다
DATA_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".feather", ".pkl", ".pickle", ".h5", ".hdf5", ".zip", ".gz", ".7z",
            ".db", ".sqlite", ".npz", ".npy"}
MAX_BYTES = 2_000_000
BIG_OK = {"image/demo.gif": "데모 녹화"}  # 2,000,000바이트가 넘어도 되는 파일과 그 까닭(크기 규칙만 면제)


def _tracked() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git이 없음")

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=60)

    r = git("rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != b"true":
        pytest.skip("git 작업 폴더가 아님")
    r = git("ls-files", "-z")  # -z: 한글 파일 이름도 그대로 받는다
    if r.returncode != 0:
        pytest.skip(f"git ls-files 실패: {r.stderr.decode('utf-8', 'replace').strip()}")
    files = [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
    if not files:
        pytest.skip("git이 추적하는 파일이 없음(아직 커밋·스테이징 전)")
    return files


def _reason(path: str) -> str | None:
    """올리면 안 되는 까닭. 괜찮으면 None."""
    p = PurePosixPath(path)
    name, ext = p.name.lower(), p.suffix.lower()
    if path.startswith(LOCAL_ONLY_DIRS):
        return "로컬 전용 폴더"
    if path in LOCAL_ONLY_FILES:
        return "로컬 전용 파일"
    if path.startswith("docs/data/") and path not in DOCS_DATA_OK:
        return "docs/data/에는 ai_model.json·precomputed.json·validation.json만 둠"
    if LOCAL_ONLY_SEGMENTS & set(p.parts):
        return "results·reports 폴더는 실자료 결과·보고서 자리"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "인증키 파일 .env"
    if ext in DATA_EXT:
        return f"데이터 형식 {ext}"
    if "clarksons" in path.lower() and ext != ".py":
        return "Clarksons 자료"
    if path not in BIG_OK:
        try:
            size = (ROOT / path).stat().st_size
        except OSError:  # 추적 중이지만 작업 폴더에서 지운 파일
            return None
        if size > MAX_BYTES:
            return f"{size:,}바이트, 한도 {MAX_BYTES:,}바이트"
    return None


def test_repository_has_no_data_files():
    bad = [(p, r) for p in _tracked() if (r := _reason(p))]
    assert not bad, "\n".join([f"공개 저장소에는 데이터를 올리지 않습니다: {p} ({r})" for p, r in bad]
                              + ["git rm --cached <경로>로 추적을 풀고 .gitignore에 넣으세요."])
