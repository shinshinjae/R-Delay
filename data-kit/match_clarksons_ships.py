"""Clarksons B1(World Fleet Register) 선박 목록을 PORT-MIS 관제정보 선박과 맞춰 호출부호 → TEU 표를 만든다.

입력: data/clarksons/raw/CLK_B1_*.xlsx|csv  (WFR 내보내기, 위쪽 제목 줄이 있어도 됨)
      data/portmis/cntl/daily/*.csv         (fetch_portmis.py 결과)
      data/clarksons/meta/manual_matches.csv (선택: clsgn,imo 두 열 — 사람이 확인한 짝)
출력: data/clarksons/derived/ship_teu_map.csv    clsgn, vsslNm, imo, teu, gt, built, operator, match_method, n_calls
      data/clarksons/derived/unmatched_vessels.csv 입항 횟수 많은 순, 비슷한 이름 후보 3개

순서(요청서 4절): ① 호출부호 일치 ② 선명(대문자·기호·M/V 제거, 이전 선명 포함) + 총톤수 ±3%
                 ③ 남은 배는 unmatched_vessels.csv로 사람이 확인 ④ 그래도 남으면 총톤수로 TEU 근사(gt_regression_approx)
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys

import numpy as np
import pandas as pd

from common import DATA, ROOT

COLS = {  # WFR 열 이름 찾기(대소문자 무시, 위에서부터 먼저 맞는 열)
    "name": [r"^(vessel|ship)?\s*name$", r"^name\b"],
    "former": [r"former|ex[\s.-]*name|previous\s*name"],
    "callsign": [r"call\s*-?\s*sign"],
    "imo": [r"^imo\b", r"imo\s*(no|number)"],
    "teu": [r"^teu\b", r"\bteu\b"],
    "gt": [r"^gt$", r"^grt$", r"gross\s*ton", r"\bgt\b"],
    "built": [r"^built", r"year\s*(of\s*)?build", r"^yob$", r"date\s*of\s*build"],
    "operator": [r"^operator", r"commercial\s*(manager|operator)", r"^manager", r"owner"],
}


def norm_name(s) -> str:
    s = str(s or "").upper().strip()
    s = re.sub(r"^(M/V|M\.V\.?|MV|M/S|MS|M/T)\s+", "", s)
    return re.sub(r"[^0-9A-Z가-힣]", "", s)


def norm_cs(s) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(s or "").upper())


def num(s):
    try:
        return float(re.sub(r"[^0-9.]", "", str(s)) or "nan")
    except ValueError:
        return float("nan")


def read_table(path):
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=str)
    else:
        raw = None
        for enc in ("utf-8-sig", "cp949", "latin-1"):
            try:
                raw = pd.read_csv(path, header=None, dtype=str, encoding=enc, keep_default_na=False)
                break
            except UnicodeDecodeError:
                continue
    raw = raw.fillna("")
    for i in range(min(40, len(raw))):
        cells = [str(x).strip() for x in raw.iloc[i].tolist()]
        found = {k for k, pats in COLS.items() for c in cells if any(re.search(p, c, re.I) for p in pats)}
        if {"name", "teu"} <= found and len(found) >= 3:
            df = raw.iloc[i + 1:].copy()
            df.columns = [c if c else f"col{j}" for j, c in enumerate(cells)]
            return df.reset_index(drop=True)
    sys.exit(f"[중단] {path.name}에서 머리줄(Name·TEU 등)을 찾지 못했습니다.")


def pick(df):
    got = {}
    for k, pats in COLS.items():
        for p in pats:
            hit = next((c for c in df.columns if re.search(p, str(c), re.I)), None)
            if hit is not None:
                got[k] = hit
                break
    return got


def portmis_ships(cfg, all_types):
    files = sorted((DATA / "portmis" / "cntl" / "daily").glob("*.csv"))
    if not files:
        sys.exit("[중단] data/portmis/cntl/daily/*.csv가 없습니다. 먼저 fetch_portmis.py를 실행하세요.")
    df = pd.concat([pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False) for f in files], ignore_index=True)
    for c in ("clsgn", "vsslNm", "vsslGrtg", "vsslKndNm", "etryptYear", "etryptCo"):
        if c not in df:
            df[c] = ""
    df = df[df["clsgn"].str.strip() != ""]
    if not all_types:
        df = df[df["vsslKndNm"].str.contains(cfg["container_ship_pattern"], regex=True, na=False)]
    df = df.assign(gt_num=df["vsslGrtg"].map(num))
    g = df.groupby("clsgn")
    ships = pd.DataFrame({
        "vsslNm": g["vsslNm"].agg(lambda s: s.mode().iat[0] if len(s.mode()) else ""),
        "gt_p": g["gt_num"].median(),
        "n_calls": df.drop_duplicates(["clsgn", "etryptYear", "etryptCo"]).groupby("clsgn").size(),
    }).reset_index().rename(columns={"index": "clsgn"})
    return ships.sort_values("n_calls", ascending=False).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wfr", default=None, help="WFR 파일 경로(기본: data/clarksons/raw/CLK_B1_* 중 최신)")
    ap.add_argument("--gt-tol", type=float, default=0.03, help="총톤수 허용 오차(기본 ±3%)")
    ap.add_argument("--all-types", action="store_true", help="컨테이너선 외 선박도 포함")
    a = ap.parse_args()
    cfg = json.loads((ROOT / "label_config.json").read_text(encoding="utf-8"))

    if a.wfr:
        from pathlib import Path
        wfr_path = Path(a.wfr)
    else:
        cands = sorted((DATA / "clarksons" / "raw").glob("CLK_B1_*"), key=lambda p: p.stat().st_mtime)
        if not cands:
            sys.exit("[중단] data/clarksons/raw/CLK_B1_*.xlsx가 없습니다(요청서 PDF B1).")
        wfr_path = cands[-1]
    w = read_table(wfr_path)
    col = pick(w)
    print(f"WFR {wfr_path.name}: {len(w):,}척 · 찾은 열 {col}")
    if "callsign" not in col:
        print("  · 호출부호 열이 없습니다 → 2단계(선명+총톤수)로만 맞춥니다.")
    W = pd.DataFrame({
        "imo": w[col["imo"]].astype(str).str.replace(r"\.0$", "", regex=True) if "imo" in col else "",
        "wname": w[col["name"]] if "name" in col else "",
        "former": w[col["former"]] if "former" in col else "",
        "cs": w[col["callsign"]].map(norm_cs) if "callsign" in col else "",
        "teu": w[col["teu"]].map(num) if "teu" in col else np.nan,
        "gt": w[col["gt"]].map(num) if "gt" in col else np.nan,
        "built": w[col["built"]] if "built" in col else "",
        "operator": w[col["operator"]] if "operator" in col else "",
    })
    W = W[W["wname"].astype(str).str.strip() != ""].reset_index(drop=True)
    names = {}  # 정규화 선명 → WFR 행 번호들(현재·이전 선명)
    for i, r in W.iterrows():
        for nm in [r["wname"]] + re.split(r"[;,/|]", str(r["former"] or "")):
            k = norm_name(nm)
            if k:
                names.setdefault(k, []).append(i)
    by_cs = {cs: i for i, cs in W["cs"].items() if cs}
    by_imo = {str(x): i for i, x in W["imo"].items() if str(x).strip()}

    P = portmis_ships(cfg, a.all_types)
    manual = {}
    mpath = DATA / "clarksons" / "meta" / "manual_matches.csv"
    if mpath.exists():
        mm = pd.read_csv(mpath, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        manual = {norm_cs(r["clsgn"]): str(r["imo"]).strip() for _, r in mm.iterrows() if r.get("clsgn") and r.get("imo")}
        print(f"사람이 확인한 짝 {len(manual)}건 반영")

    out, unmatched = [], []
    for _, p in P.iterrows():
        cs, gt_p = norm_cs(p["clsgn"]), p["gt_p"]
        idx, how = None, ""
        if cs in manual and manual[cs] in by_imo:
            idx, how = by_imo[manual[cs]], "manual"
        elif cs and cs in by_cs:
            idx, how = by_cs[cs], "callsign"
        else:
            for i in names.get(norm_name(p["vsslNm"]), []):
                gt_w = W.at[i, "gt"]
                if np.isfinite(gt_p) and np.isfinite(gt_w) and gt_p > 0 and abs(gt_w - gt_p) / gt_p <= a.gt_tol:
                    idx, how = i, "name_gt"
                    break
        if idx is None:
            unmatched.append(p)
            continue
        r = W.loc[idx]
        out.append({"clsgn": p["clsgn"], "vsslNm": p["vsslNm"], "imo": r["imo"], "teu": r["teu"], "gt": gt_p if np.isfinite(gt_p) else r["gt"],
                    "built": r["built"], "operator": r["operator"], "match_method": how, "n_calls": p["n_calls"]})

    # ④ 총톤수 → TEU 근사(WFR 안에서 log TEU = a + b log GT)
    fit = W[(W["teu"] > 0) & (W["gt"] > 0)]
    coef = np.polyfit(np.log(fit["gt"]), np.log(fit["teu"]), 1) if len(fit) >= 20 else None
    um_rows = []
    keys = list(names)
    for p in unmatched:
        k = norm_name(p["vsslNm"])
        near = difflib.get_close_matches(k, keys, n=12, cutoff=0.75) if k else []
        rows_ = {i for c in near for i in names[c]}

        def gap(i):  # 총톤수 차이 비율(모르면 맨 뒤)
            g = W.at[i, "gt"]
            return abs(g - p["gt_p"]) / p["gt_p"] if np.isfinite(g) and np.isfinite(p["gt_p"]) and p["gt_p"] > 0 else 9.0
        best = sorted(rows_, key=gap)[:3]
        cand_txt = " | ".join(f"{W.at[i, 'wname']} (IMO {W.at[i, 'imo']}, GT {W.at[i, 'gt']:.0f}"
                              f"{'' if gap(i) >= 9 else f' 차이 {gap(i):.0%}'}, TEU {W.at[i, 'teu']:.0f})" for i in best)
        um_rows.append({"clsgn": p["clsgn"], "vsslNm": p["vsslNm"], "gt": p["gt_p"], "n_calls": p["n_calls"], "candidates": cand_txt})
        if coef is not None and np.isfinite(p["gt_p"]) and p["gt_p"] > 0:
            teu = float(np.exp(coef[1] + coef[0] * np.log(p["gt_p"])))
            out.append({"clsgn": p["clsgn"], "vsslNm": p["vsslNm"], "imo": "", "teu": round(teu), "gt": p["gt_p"], "built": "",
                        "operator": "", "match_method": "gt_regression_approx", "n_calls": p["n_calls"]})

    dd = DATA / "clarksons" / "derived"
    dd.mkdir(parents=True, exist_ok=True)
    res = pd.DataFrame(out, columns=["clsgn", "vsslNm", "imo", "teu", "gt", "built", "operator", "match_method", "n_calls"])
    res.sort_values("n_calls", ascending=False).to_csv(dd / "ship_teu_map.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(um_rows, columns=["clsgn", "vsslNm", "gt", "n_calls", "candidates"]).sort_values("n_calls", ascending=False).to_csv(
        dd / "unmatched_vessels.csv", index=False, encoding="utf-8-sig")

    total_calls = int(P["n_calls"].sum()) or 1
    print(f"\nPORT-MIS 선박 {len(P):,}척 · 입항 {total_calls:,}회")
    for m in ("manual", "callsign", "name_gt", "gt_regression_approx"):
        sub = res[res["match_method"] == m]
        print(f"  {m:22s} {len(sub):5,}척 · 입항의 {sub['n_calls'].sum() / total_calls:6.1%}")
    print(f"  {'확인 필요(unmatched)':20s} {len(um_rows):5,}척 → unmatched_vessels.csv에서 위에서부터 확인해 "
          f"meta/manual_matches.csv(clsgn,imo)에 적고 다시 실행")
    print(f"\n→ {dd / 'ship_teu_map.csv'}\n→ {dd / 'unmatched_vessels.csv'}")


if __name__ == "__main__":
    main()
