"""단계 7: 자료 최신화를 한 명령으로. 모형·설정·경보 기준은 고정하고 평가(시험) 기간만 늘린다.

  python -m realdata.run_all --dry-run                 # 무엇이 바뀔지만 본다(캐시·라벨·결과를 바꾸지 않음)
  python -m realdata.run_all                           # 새 자료가 있으면 끝까지(이유는 자동으로 '마지막 판단 시각 A → B')
  python -m realdata.run_all --reason "11/7 자료 반영"   # 이유를 직접 적기(test_runs.json에 남음)
이미 만든 로컬 결과(realdata/results/rdelay.json 등)를 새 자료로 늘리는 명령이다. 처음이면 README ‘실자료 백테스트’ 순서를 먼저 끝까지 돌린다.

차례
  1. 캐시 갱신: 바뀐 원본만 다시 읽는다(--refresh면 전부 다시 읽고 R-Delay도 처음부터 다시 돌린다).
  2. 라벨: 관제정보 파일이 라벨(derived/newport_voyages.csv)보다 새로우면 저장소의 data-kit/build_wait_labels.py
     (지금 라벨을 만든 판과 같음, 100% 재현 확인)를 RDELAY_DATA_DIR=<공개 자료 폴더>로 돌린다. 이전 라벨은 derived/_prev_<시각>/에 복사해 둔다.
  3. 마지막 판단 시각 = 도착 자료 끝 − 7일(features.data_tau_end). 마지막으로 평가한 시각(rdelay.json)보다 늘지 않았고 라벨도 그대로면
     여기서 멈춘다(--force가 없으면 평가 기간을 다시 계산하지 않는다).
  4. 자료 점검(audit) → R-Delay(관측이 바뀐 가장 이른 시각과 '이전 끝 − 7일' 중 이른 쪽부터 다시 돌려 잇는다. 보정값 rdelay_params.json과
     검증 선택 rdelay_select.json은 그대로) → 기준선 → 기준선 설정이 그대로인지 확인(바뀌면 멈춤)
     → 평가 기간 다시 계산(evaluate final --rerun-reason, test_runs.json에 이유와 함께 쌓임) → 그림·비교표 → 사례
     → 집계 JSON(scripts/build_backtest.py → realdata/results/backtest.json) → 발표 그림
  5. 실자료 코드 단위 시험(tests/test_realdata_units.py·test_realdata_features.py)이 실패하면 멈춘다.
     단계별 걸린 시간을 realdata/results/refresh_log.json에 남긴다(실패해도).
결과·그림·집계는 모두 로컬 전용이다(gitignore, 공개 저장소에 올리지 않음: 사용자 결정 2026-11-07).
도로 1시간 속도(derived/ex_speed_busan.csv)는 data-kit/filter_ex_speed.py가 만든다. 새 원본이 있는데 걸러 낸 파일이 더 오래됐으면 알려만 준다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from . import features as F
from .io import SPECS, load
from .paths import CACHE, REPO, RESULTS, public_dir

RUN = CACHE / "rdelay_all_W24_P512.parquet"
OBS = CACHE / "rdelay_obs.parquet"
LOG = RESULTS / "refresh_log.json"
# 먼저 있어야 하는 로컬 결과(평가한 끝·기준선 설정·보정값·검증 선택). 없으면 처음부터 만드는 순서(README)를 먼저 돌린다
NEED = ("rdelay.json", "baselines.json", "rdelay_params.json", "rdelay_select.json")


def _sh(args: list[str], env: dict | None = None, cwd: Path | None = None) -> None:
    print("$", " ".join(args), flush=True)
    r = subprocess.run(args, cwd=cwd or REPO, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise SystemExit(f"[중단] 실패: {' '.join(args)} (코드 {r.returncode})")


def _newest(files) -> float:
    return max((Path(f).stat().st_mtime for f in files), default=0.0)


def labels_stale() -> bool:
    cntl = SPECS["cntl"].files()
    lab = SPECS["voyages"].files()
    return bool(cntl) and (not lab or _newest(cntl) > _newest(lab))


def rebuild_labels() -> tuple[bool, str]:
    """(다시 만들었는지, 설명)."""
    derived = public_dir() / "derived"
    if not labels_stale():
        return False, "라벨이 관제정보보다 새로움 → 그대로"
    keep = derived / f"_prev_{time.strftime('%Y%m%d_%H%M')}"
    keep.mkdir(parents=True, exist_ok=True)
    for n in ("newport_voyages.csv", "newport_waiting_2h.csv", "label_summary.json"):
        if (derived / n).exists():
            shutil.copy2(derived / n, keep / n)
    _sh([sys.executable, "build_wait_labels.py"], env={"RDELAY_DATA_DIR": str(public_dir())}, cwd=REPO / "data-kit")
    return True, f"라벨 다시 만듦(이전 라벨은 {keep.name}/)"


def road_note() -> str:
    raw = list((public_dir() / "manual" / "ex_speed_1h").glob("*")) if (public_dir() / "manual" / "ex_speed_1h").exists() else []
    der = SPECS["road"].files()
    if raw and der and _newest(raw) > _newest(der):
        return "[주의] 도로 원본(manual/ex_speed_1h)이 걸러 낸 파일보다 새롭다 → data-kit/filter_ex_speed.py를 먼저 돌려야 반영된다"
    return "도로 자료 그대로"


def dry_run(last_eval_end) -> None:
    """캐시·라벨을 건드리지 않고 파일 이름(날짜)만 보고 어림한다."""
    days = [re.search(r"(\d{8})", Path(f).stem) for f in SPECS["cntl"].files()]
    days = sorted(pd.Timestamp(m.group(1)) for m in days if m)
    est = (days[-1] + pd.Timedelta(days=1) - pd.Timedelta(days=7)).floor("2h") if days else None
    print(f"[라벨] {'관제정보가 라벨보다 새로움 → 라벨을 다시 만들 예정' if labels_stale() else '라벨이 관제정보보다 새로움 → 그대로'}")
    print(f"[도로] {road_note()}")
    print(f"마지막으로 평가한 판단 시각 {last_eval_end} → 관제정보 파일 날짜로 어림한 새 끝 {est}(라벨을 다시 만든 뒤 확정)")
    print("--dry-run: 여기서 멈춤(캐시·라벨·결과를 바꾸지 않음)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reason", default="", help="평가 기간 다시 계산 이유(test_runs.json에 남음). 없으면 실제로 바뀐 것을 자동으로 적음")
    ap.add_argument("--refresh", action="store_true", help="캐시를 무시하고 원본을 모두 다시 읽고, R-Delay도 처음부터 다시 돌리기")
    ap.add_argument("--force", action="store_true", help="새 판단 시각·새 라벨이 없어도 평가 기간을 다시 계산(이유를 --reason으로 적어야 함)")
    ap.add_argument("--allow-setting-change", action="store_true", help="기준선 설정이 바뀌어도 계속(이유에 적힘)")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.force and not a.reason:
        raise SystemExit("[중단] --force에는 --reason이 필요합니다.")
    missing = [n for n in NEED if not (RESULTS / n).exists()]
    if missing:
        raise SystemExit(f"[중단] realdata/results/{'·'.join(missing)}이 없습니다. run_all은 이미 만든 로컬 결과를 새 자료로 늘리는 명령입니다. "
                         "README ‘실자료 백테스트’ 순서를 먼저 끝까지 돌리세요.")
    r_prev = json.loads((RESULTS / "rdelay.json").read_text(encoding="utf-8"))
    last_eval_end = pd.Timestamp(r_prev["tau_end"])
    if a.dry_run:
        dry_run(last_eval_end)
        return
    t_all, steps, status = time.time(), [], "실패"
    log_entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "args": vars(a), "steps": steps}

    def step(name, fn):
        t0 = time.time()
        out = fn()
        steps.append({"step": name, "seconds": round(time.time() - t0, 1), "note": out})
        print(f"[{name}] {out} · {steps[-1]['seconds']}초", flush=True)
        return out

    try:
        before = json.loads((RESULTS / "baselines.json").read_text(encoding="utf-8"))["chosen"]
        step("캐시", lambda: ", ".join(f"{n} {len(load(n, refresh=a.refresh)):,}행" for n in SPECS))
        rebuilt = {}
        step("라벨", lambda: (lambda r: rebuilt.update(v=r[0]) or r[1])(rebuild_labels()))
        step("라벨 캐시", lambda: ", ".join(f"{n} {len(load(n)):,}행" for n in ("voyages", "waiting_2h")))
        step("도로", road_note)
        new_end = F.data_tau_end(load("voyages"))
        log_entry["tau_end"] = [str(last_eval_end), str(new_end)]
        print(f"마지막 판단 시각: 평가한 끝 {last_eval_end} → 자료로 정한 끝 {new_end}")
        if new_end <= last_eval_end and not rebuilt.get("v") and not a.force and not a.refresh:
            status = "새 자료 없음(평가 다시 안 함)"
            print("새 판단 시각도 새 라벨도 없습니다. 평가 기간을 다시 계산하지 않습니다(필요하면 --force --reason \"...\").")
            return
        step("자료 점검", lambda: _sh([sys.executable, "-m", "realdata.audit"]) or "audit.json·data_audit.md")

        def run_rdelay():
            from . import rdelay_real as RR
            old_obs = pd.read_parquet(OBS) if OBS.exists() else None
            _, _, obs = RR.load_inputs(rebuild=True)
            params = json.loads(RR.PARAMS_PATH.read_text(encoding="utf-8"))
            old = pd.read_parquet(RUN) if (RUN.exists() and not a.refresh) else None
            lo = pd.Timestamp("2023-01-08") if old is None else old.index.max() - pd.Timedelta(days=7)
            if old is not None and old_obs is not None:  # 늦게 들어온 관제 기록 등으로 예전 관측이 바뀌었으면 그 시각부터
                common = obs.index.intersection(old_obs.index)
                cols = [c for c in obs.columns if c in old_obs.columns]
                x, y = obs.loc[common, cols], old_obs.loc[common, cols]
                changed = ~((x == y) | (x.isna() & y.isna())).all(axis=1)
                if changed.any():
                    lo = min(lo, changed[changed].index.min())
            taus = [t for t in obs.index if lo <= t <= new_end and t - pd.Timedelta(hours=24) >= obs.index[0]]
            new = RR.run_many(taus, 24, 512, a.jobs, obs, params)
            merged = new if old is None else pd.concat([old[old.index < lo], new]).sort_index()
            merged.to_parquet(RUN)
            return f"τ {len(taus):,}개 다시 계산({lo} ~ {new_end}), 전체 {len(merged):,}개"
        step("R-Delay", run_rdelay)
        step("기준선", lambda: _sh([sys.executable, "-m", "realdata.baselines"]) or "baselines.json")
        after = json.loads((RESULTS / "baselines.json").read_text(encoding="utf-8"))["chosen"]
        if after != before and not a.allow_setting_change:
            raise SystemExit(f"[중단] 기준선 설정이 바뀌었습니다(학습·검증 기간 자료가 바뀐 듯): {before} → {after}. "
                             "확인한 뒤 --allow-setting-change로 다시 돌리세요(이유에 함께 적힘).")
        reason = a.reason or (f"자료 최신화: 마지막 판단 시각 {last_eval_end} → {new_end}"
                              + (", 라벨 다시 만듦" if rebuilt.get("v") else "") + " (모형·설정·경보 기준 고정)")
        if after != before:
            reason += f" [기준선 설정 바뀜: {before} → {after}]"
        log_entry["reason"] = reason
        step("평가 기간", lambda: _sh([sys.executable, "-m", "realdata.evaluate", "final", "--rdelay", str(RUN.relative_to(REPO)),
                                   "--rerun-reason", reason]) or "rdelay.json·test_runs.json")
        step("그림·표", lambda: [_sh([sys.executable, "-m", m]) for m in ("realdata.figures", "realdata.report")] and "fig_*.png, compare.md")
        step("사례", lambda: _sh([sys.executable, "-m", "realdata.cases"]) or "cases.json, fig_case_*.png")
        step("집계 JSON", lambda: _sh([sys.executable, "scripts/build_backtest.py"]) or "realdata/results/backtest.json(로컬 전용)")
        step("발표 그림", lambda: _sh([sys.executable, "-m", "realdata.slides"]) or "presentation/")
        step("결과 시험", lambda: _sh([sys.executable, "-m", "pytest", "-q", "tests/test_realdata_units.py",
                                    "tests/test_realdata_features.py"]) or "통과")
        status = "완료"
        print("[다음] README·REPORT·MODEL·method.html의 숫자를 realdata/results/backtest.json과 손으로 대조해 고치고"
              "(자동으로 맞춰 보는 시험은 없음), python -m pytest -q로 확인하세요.")
    finally:
        log_entry.update({"status": status, "seconds": round(time.time() - t_all, 1)})
        log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
        LOG.write_text(json.dumps(log + [log_entry], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"→ {LOG} · {status} · 모두 {time.time() - t_all:.0f}초")


if __name__ == "__main__":
    main()
