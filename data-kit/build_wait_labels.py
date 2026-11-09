"""관제정보로 '부산신항 선박 대기 12시간 초과' 라벨과 2시간 단위 대기 척수 시계열을 만든다.

입력: data/portmis/cntl/daily/*.csv (fetch_portmis.py 결과)
출력: data/derived/newport_voyages.csv    항차별 도착·접안 시각, 대기시간, 12시간 초과 여부
      data/derived/newport_waiting_2h.csv  2시간 격자: 대기 중 척수, 도착 수, 접안 수 (해상층 관측 입력)
      data/derived/label_summary.json      건수·비율 요약

판정(label_config.json의 정규식):
  항차 = (호출부호 clsgn, 입항연도 etryptYear, 입항횟수 etryptCo), 컨테이너선만
  첫 '입항' 관제의 시설이 정박지   → 그 시각부터 신항 선석에 처음 댄 관제 시각까지가 대기
  첫 '입항' 관제의 시설이 신항 선석 → 대기 0
  신항 선석에 대지 않은 항차(북항·감천 등), 다른 선석에 먼저 댔다가 신항으로 옮긴 항차는 뺍니다.
먼저 --inspect로 실제 값을 보고 패턴을 맞추세요. 항계 밖 대기는 입항 신고 전이라 빠질 수 있습니다.
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from common import DATA, ROOT


def load(cfg_path):
    cfg = json.loads(open(cfg_path, encoding="utf-8").read())
    files = sorted((DATA / "portmis" / "cntl" / "daily").glob("*.csv"))
    if not files:
        sys.exit("[중단] data/portmis/cntl/daily/*.csv가 없습니다. 먼저 fetch_portmis.py를 실행하세요.")
    frames = [pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False) for f in files if f.stat().st_size > 0]
    frames = [x for x in frames if len(x)]
    if not frames:
        sys.exit("[중단] 받은 파일에 기록이 하나도 없습니다.")
    df = pd.concat(frames, ignore_index=True).fillna("")
    for col in ("clsgn", "etryptYear", "etryptCo", "cntrlNm", "cntrlSe", "fcltyNm", "vsslNm", "vsslKndNm", "vsslGrtg", "cntrlOpertDt"):
        if col not in df:
            df[col] = ""
    digits = df["cntrlOpertDt"].astype(str).str.replace(r"[^0-9]", "", regex=True)
    df["t"] = pd.to_datetime(digits.str.slice(0, 12).str.pad(12, side="right", fillchar="0"), format="%Y%m%d%H%M", errors="coerce")
    df = df.dropna(subset=["t"]).drop_duplicates()
    return cfg, df


def inspect(df):
    print(f"기록 {len(df):,}건 · 기간 {df['t'].min()} ~ {df['t'].max()} · 날짜 파일 기준")
    for col in ("cntrlNm", "cntrlSe", "vsslKndNm"):
        print(f"\n[{col}] 상위 값")
        print(df[col].value_counts().head(20).to_string())
    print("\n[fcltyNm] 상위 60")
    print(df["fcltyNm"].value_counts().head(60).to_string())
    key = ["clsgn", "etryptYear", "etryptCo"]
    sizes = df.groupby(key).size().sort_values(ascending=False)
    for k in sizes.index[:3]:
        sub = df[(df["clsgn"] == k[0]) & (df["etryptYear"] == k[1]) & (df["etryptCo"] == k[2])].sort_values("t")
        print(f"\n[항차 예시] 호출부호 {k[0]} · {k[1]}년 {k[2]}번째 입항")
        print(sub[["t", "cntrlNm", "cntrlSe", "fcltyNm", "vsslNm", "vsslKndNm"]].to_string(index=False))
    print("\n→ label_config.json의 패턴을 위 값에 맞게 고친 뒤 --inspect 없이 실행하세요.")


def build(cfg, df):
    def m(col, pat):
        if not pat:
            return pd.Series(False, index=df.index)
        return df[col].astype(str).str.contains(pat, regex=True, na=False)

    is_arr = m("cntrlNm", cfg["arrival_pattern"]) | m("cntrlSe", cfg["arrival_pattern"])
    is_anch = m("fcltyNm", cfg["anchorage_pattern"])
    is_berth = m("fcltyNm", cfg["newport_berth_pattern"]) & ~is_anch & ~m("fcltyNm", cfg.get("exclude_facility_pattern", ""))
    is_cont = m("vsslKndNm", cfg["container_ship_pattern"])
    d = df.assign(is_arr=is_arr, is_anch=is_anch, is_berth=is_berth, is_cont=is_cont).sort_values("t", kind="stable")
    rows, excluded = [], [0]
    for k, g in d.groupby(["clsgn", "etryptYear", "etryptCo"], sort=False):
        if not g["is_cont"].any():
            continue
        arr = g[g["is_arr"]]
        if arr.empty:
            continue
        a = arr.iloc[0]
        after = g[g["t"] >= a["t"]]
        berth = after[after["is_berth"]]
        if berth.empty:
            continue
        b = berth.iloc[0]
        # 신항 선석 전에 다른 선석(북항·감천 등)에 먼저 댄 항차는 터미널 간 이동이지 대기가 아니므로 뺀다.
        between = g[(g["t"] >= a["t"]) & (g["t"] < b["t"])]
        if (not a["is_anch"] and not a["is_berth"]) or (between["cntrlNm"].eq("접안") & ~between["is_berth"] & ~between["is_anch"]).any():
            excluded[0] += 1
            continue
        if a["is_anch"]:
            wait_h, kind = (b["t"] - a["t"]).total_seconds() / 3600, "정박지→선석"
        elif a["is_berth"]:
            wait_h, kind = 0.0, "바로 접안"
        else:
            wait_h, kind = (b["t"] - a["t"]).total_seconds() / 3600, "입항시설 미분류"
        rows.append({
            "clsgn": k[0], "etryptYear": k[1], "etryptCo": k[2], "vsslNm": a["vsslNm"], "vsslKndNm": a["vsslKndNm"],
            "vsslGrtg": a["vsslGrtg"], "arrival_t": a["t"], "arrival_facility": a["fcltyNm"],
            "berth_t": b["t"], "berth_facility": b["fcltyNm"], "wait_h": round(wait_h, 2), "wait_kind": kind,
            "wait_gt_threshold": int(wait_h > cfg["wait_threshold_h"]),
        })
    out = pd.DataFrame(rows)
    out.attrs["excluded_other_berth_first"] = excluded[0]
    return out


def grid(cfg, v):
    if v.empty:
        return pd.DataFrame(columns=["t", "n_waiting", "n_arrivals", "n_berthings"])
    h = int(cfg["grid_hours"])
    t0 = v["arrival_t"].min().floor(f"{h}h")
    t1 = v["berth_t"].max().ceil(f"{h}h")
    ts = pd.date_range(t0, t1, freq=f"{h}h")
    edges = list(ts) + [ts[-1] + pd.Timedelta(hours=h)]
    waiting = v[v["wait_h"] > 0]
    a = waiting["arrival_t"].to_numpy()
    b = waiting["berth_t"].to_numpy()
    out = pd.DataFrame({"t": ts})
    out["n_waiting"] = [int(((a <= t) & (b > t)).sum()) for t in ts.to_numpy()]
    out["n_arrivals"] = pd.cut(v["arrival_t"], bins=edges, right=False).value_counts(sort=False).to_numpy()
    out["n_berthings"] = pd.cut(v["berth_t"], bins=edges, right=False).value_counts(sort=False).to_numpy()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "label_config.json"))
    ap.add_argument("--inspect", action="store_true", help="관제구분·시설명 실제 값과 항차 예시만 보여 줌")
    a = ap.parse_args()
    cfg, df = load(a.config)
    if a.inspect:
        inspect(df)
        return
    v = build(cfg, df)
    out = DATA / "derived"
    out.mkdir(parents=True, exist_ok=True)
    v.to_csv(out / "newport_voyages.csv", index=False, encoding="utf-8-sig")
    grid(cfg, v).to_csv(out / "newport_waiting_2h.csv", index=False, encoding="utf-8-sig")
    if v.empty:
        print("[주의] 신항 컨테이너선 항차를 하나도 찾지 못했습니다. --inspect로 값을 보고 label_config.json을 고치세요.")
        return
    th = cfg["wait_threshold_h"]
    summary = {
        "기간": [str(v["arrival_t"].min()), str(v["arrival_t"].max())],
        "신항 컨테이너선 항차": int(len(v)),
        f"대기 {th}시간 초과": int(v["wait_gt_threshold"].sum()),
        "초과 비율": round(float(v["wait_gt_threshold"].mean()), 4),
        "대기 중앙값(시간)": round(float(v["wait_h"].median()), 2),
        "판정 종류": {k: int(n) for k, n in v["wait_kind"].value_counts().items()},
        "제외(다른 선석 먼저 접안→신항 이동)": int(v.attrs.get("excluded_other_berth_first", 0)),
    }
    (out / "label_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"신항 컨테이너선 항차 {len(v):,}건 · 대기 {th}시간 초과 {summary[f'대기 {th}시간 초과']:,}건 "
          f"({summary['초과 비율']:.1%}) · 대기 중앙값 {summary['대기 중앙값(시간)']}시간")
    print("판정 종류:", summary["판정 종류"])
    print("\n[확인] 선석으로 잡힌 시설 상위 10 — 북항·감천 시설이 섞였으면 패턴을 고치세요")
    print(v["berth_facility"].value_counts().head(10).to_string())
    print("\n[확인] 도착 시설 상위 10")
    print(v["arrival_facility"].value_counts().head(10).to_string())
    print(f"\n→ {out / 'newport_voyages.csv'}\n→ {out / 'newport_waiting_2h.csv'}\n→ {out / 'label_summary.json'}")


if __name__ == "__main__":
    main()
