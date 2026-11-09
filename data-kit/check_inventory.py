"""받은 자료를 점검해 data/data_inventory.md를 만든다.

  python check_inventory.py              # 점검표 만들기(무엇이 있고 무엇이 빠졌는지)
  python check_inventory.py --make-dirs  # 직접 받을 자료의 폴더와 '_여기에_넣을_자료.txt' 안내 파일 만들기
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from collections import defaultdict

from catalog import API_SIGNUPS, ITEMS
from common import DATA, daterange

GROUPS = [("auto", "API로 자동 수집"), ("manual", "로그인 후 직접 받기"), ("clarksons", "Clarksons (요청서 PDF 참고)"),
          ("derived", "스크립트가 만드는 파생 자료")]


def files_for(item):
    out = set()
    pairs = [(item["folder"], item["globs"])]
    if item.get("alt_folder"):
        pairs.append((item["alt_folder"], item.get("alt_globs", item["globs"])))
    for folder, globs in pairs:
        base = DATA / folder
        if not base.exists():
            continue
        for g in globs:
            for p in base.rglob(g):
                if p.is_file() and not p.name.startswith("_") and not p.name.endswith(".part"):
                    out.add(p)
    return sorted(out)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def lines(p):
    with p.open("rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


def portmis_detail(files, start, end):
    days = sorted(dt.datetime.strptime(p.stem, "%Y%m%d").date() for p in files if p.stem.isdigit() and len(p.stem) == 8)
    if not days:
        return ""
    want = set(daterange(start, end))
    have = set(days)
    per = [max(0, lines(p) - 1) for p in files]
    rows, empty = sum(per), sum(1 for n in per if n == 0)
    miss = sorted(want - have)
    s = f"{len(have & want):,}/{len(want):,}일 · {days[0]}~{days[-1]} · 기록 {rows:,}건"
    if empty:
        s += f" · 기록 없는 날 {empty:,}일(제공 기간 밖일 수 있음)"
    if miss:
        s += f" · 아직 {len(miss):,}일 남음({miss[0]}~{miss[-1]} 사이)"
    return s


def merged_detail(path):
    if not path.exists():
        return "합친 CSV 없음(수집을 끝까지 실행하면 생김)"
    first = last = None
    n = 0
    with path.open(encoding="utf-8-sig") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if row:
                first = first or row[0]
                last = row[0]
                n += 1
    return f"{path.name} {n:,}행 · {first}~{last}" if n else f"{path.name} 비어 있음"


def describe(item, files, start, end):
    size = human(sum(p.stat().st_size for p in files))
    base = f"파일 {len(files):,}개 · {size}"
    if item["key"].startswith("portmis"):
        d = portmis_detail(files, start, end)
        return f"{base} · {d}" if d else base
    merged = {"kma_asos": DATA / "kma/asos/asos_hourly.csv", "kma_buoy": DATA / "kma/buoy/buoy_hourly.csv"}.get(item["key"])
    if merged is not None and merged.exists():
        return f"{base} · {merged_detail(merged)}"
    names = ", ".join(p.name for p in files[:3]) + (" …" if len(files) > 3 else "")
    return f"{base} · {names}"


def status(item, files):
    if not files:
        return "❌ 없음"
    ra = item.get("refresh_after")
    if ra:
        newest = max(dt.date.fromtimestamp(p.stat().st_mtime) for p in files)
        if newest < dt.date.fromisoformat(ra):
            return f"⚠️ {ra[5:].replace('-', '/')} 이후 다시"
    return "✅ 있음"


def make_dirs():
    made = 0
    by_folder = defaultdict(list)
    for it in ITEMS:
        if it["how"] in ("manual", "clarksons"):
            by_folder[it["folder"]].append(it)
    for folder, items in by_folder.items():
        d = DATA / folder
        d.mkdir(parents=True, exist_ok=True)
        lines_ = ["이 폴더에 넣을 자료", ""]
        for it in items:
            lines_ += [f"[{it['prio']}] {it['name']}  ({it['src']})", f"  쓰는 곳: {it['use']}"]
            if it.get("url"):
                lines_.append(f"  받는 곳: {it['url']}")
            if it["how"] == "clarksons":
                lines_.append(f"  파일 이름: {it['globs'][0].replace('*', '…')}  (요청서 PDF 5절)")
            if it.get("note"):
                lines_.append(f"  메모: {it['note']}")
            lines_.append("")
        lines_.append("ZIP은 풀지 않고 그대로 넣어도 됩니다. 넣은 뒤 python check_inventory.py 로 확인하세요.")
        (d / "_여기에_넣을_자료.txt").write_text("\n".join(lines_), encoding="utf-8-sig")
        made += 1
    print(f"폴더 {made}개를 만들었습니다 → {DATA}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2023-01-01", help="관제정보 목표 시작일")
    ap.add_argument("--end", default="2026-11-07", help="관제정보 목표 끝일")
    ap.add_argument("--make-dirs", action="store_true")
    a = ap.parse_args()
    if a.make_dirs:
        make_dirs()
        return
    DATA.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# R-Delay 자료 점검표", "", f"만든 시각 {now} · 자료 폴더 `{DATA}`", ""]
    tally = defaultdict(lambda: [0, 0])
    todo = []
    body = []
    for how, title in GROUPS:
        body += [f"## {title}", "", "| 상태 | 우선 | 자료 | 출처 | 들어 있는 것 | 쓰는 곳 |", "|---|---|---|---|---|---|"]
        for it in [x for x in ITEMS if x["how"] == how]:
            files = files_for(it)
            st = status(it, files)
            det = describe(it, files, a.start, a.end) if files else f"`data/{it['folder']}/`"
            body.append(f"| {st} | {it['prio']} | {it['name']} | {it['src']} | {det} | {it['use']} |")
            tally[it["prio"]][1] += 1
            if files and not st.startswith("⚠️"):
                tally[it["prio"]][0] += 1
            elif it["prio"] in ("필수", "권장"):
                if it["how"] == "derived":
                    how_to = f"원자료가 생기면 python {it['src']}"
                else:
                    how_to = it.get("cmd") or it.get("url") or f"요청서 PDF대로 받아 data/{it['folder']}/"
                    if it["how"] == "auto" and it.get("note"):
                        how_to += f"  (또는: {it['note']})"
                if st.startswith("⚠️"):
                    how_to = f"{it.get('note', '')} {it.get('url', '')}".strip()
                todo.append(f"[{it['prio']}] {it['name']} → {how_to}")
        body.append("")
    summary = " · ".join(f"{p} {tally[p][0]}/{tally[p][1]}" for p in ("필수", "권장", "선택"))
    md += [f"**준비된 자료: {summary}**", ""] + body
    md += ["## 다음에 할 일", ""] + ([f"- {t}" for t in todo] or ["- 필수·권장 자료가 모두 있습니다."]) + [""]
    md += ["## API 활용신청", "", "| 사이트 | API | 우선 | 신청 주소 | .env 이름 |", "|---|---|---|---|---|"]
    md += [f"| {s} | {n} | {p} | {u} | {k} |" for s, n, p, u, k in API_SIGNUPS] + [""]
    out = DATA / "data_inventory.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"준비된 자료: {summary}")
    if todo:
        print("\n다음에 할 일")
        for t in todo:
            print(" -", t)
    print(f"\n점검표 → {out}")


if __name__ == "__main__":
    main()
