"""모든 시나리오를 기본 설정으로 미리 계산해 docs/data/precomputed.json에 저장한다.

한 번 실행에 4종 화물이 모두 들어 있다. 웹은 이 결과를 먼저 보여 주고(빠른 첫 화면·오프라인 시연),
사용자가 설정을 바꾸면 브라우저 안의 Pyodide로 같은 엔진을 다시 실행한다.

실행: python3 scripts/build_data.py
"""
from __future__ import annotations
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "py"))
from rdelay import api, config  # noqa: E402


def main():
    model = json.loads((ROOT / "docs" / "data" / "ai_model.json").read_text(encoding="utf-8"))
    out = {"presets": api.presets(), "runs": {}}
    t0 = time.time()
    for scen in config.SCENARIOS:
        r = api.run({"scenario": scen}, ai_model=model)
        out["runs"][scen] = r
        decisions = " / ".join(f"{k}:{v['decision']['status'][:3]}" for k, v in r["cargo"].items())
        print(f"{scen:18s} {r['now']['alarm']:9s} {r['now']['cause']:10s} truth {r['truth_now']['label']:6s} | {decisions}")
    path = ROOT / "docs" / "data" / "precomputed.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"saved {path} ({path.stat().st_size / 1024:.0f} KB) in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
